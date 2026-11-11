"""MCP server for Content Pilot Spider.

Exposes the crawler to MCP clients (Claude Desktop, Claude Code, Cursor, ...) as a thin wrapper over the local HTTP
API in spider/server.py, so every tool does exactly what the UI does and nothing more. One tool set, two transports:

* Streamable HTTP at  http://127.0.0.1:<port>/mcp  - served by the running app (Config > MCP). Nothing to launch.
* stdio - `python mcp_server.py` in the project root. It talks to the running app, or starts a headless one.

`build_mcp(client_factory)` returns the server; the factory yields an httpx.AsyncClient that reaches the API, either
in-process (httpx.ASGITransport, used by the HTTP mount) or over TCP (used by the stdio launcher).
"""
from __future__ import annotations

import asyncio
import inspect
import re
import time
from pathlib import Path
from typing import Any, Callable, Literal

import httpx
from mcp.server.mcpserver import MCPServer
from mcp.server.mcpserver.exceptions import ToolError

ROOT = Path(__file__).resolve().parents[1]
EXPORT_DIR = ROOT / "exports"
VERSION = "0.2.0"
SERVER_NAME = "content-pilot-spider"

INSTRUCTIONS = """Content Pilot Spider is a local, Screaming Frog style SEO crawler. Everything works on the crawl that is
currently open in the app. Typical flow:
1. list_crawls then open_crawl(name) to use a saved crawl, or crawl_start(url) followed by crawl_wait for a fresh one.
2. overview and list_issues show what is wrong; query_filter(key) lists the affected URLs; url_details(url) explains one page
   (inlinks, outlinks, headers, every issue it triggers).
3. issues_markdown gives a paste-ready summary; run_report returns a report as JSON; export_* tools write CSV/XLSX/zip files
   to disk and return the paths.
4. Fixes: fixes_generate(key) drafts titles/metas/H1s/alt text/redirects with the configured AI provider, fixes_list shows
   the queue, fixes_bulk(status='approved') or fixes_update approves, fixes_push(target) applies them through a connector.
   Pushing to a LIVE target needs confirm=<target name>; always ask the person before pushing to live.
Filter keys are 'tab:filter', e.g. 'page_titles:over_60' or 'response_codes:int_4xx'; list_filters shows them all with counts."""

RUNNING = ("running", "stopping")
REDACT = "[redacted]"


class SpiderError(ToolError):
    """Carries the API's own error message back to the MCP client (ToolError passes the text through verbatim)."""


def _compact(d: dict[str, Any]) -> dict[str, Any]:
    return {k: v for k, v in d.items() if v not in (None, "", [], {})}


def _fname(s: str) -> str:
    return re.sub(r"[^A-Za-z0-9._-]+", "_", s).strip("_") or "export"


def build_mcp(client_factory: Callable[[], httpx.AsyncClient]) -> MCPServer:
    srv = MCPServer(SERVER_NAME, title="Content Pilot Spider", instructions=INSTRUCTIONS, version=VERSION)
    _state: dict[str, httpx.AsyncClient | None] = {"client": None}

    def client() -> httpx.AsyncClient:
        c = _state["client"]
        if c is None or c.is_closed:
            c = _state["client"] = client_factory()
        return c

    async def call(method: str, path: str, params: dict[str, Any] | None = None, body: Any = None, raw: bool = False) -> Any:
        params = {k: v for k, v in (params or {}).items() if v not in (None, "")}
        try:
            r = await client().request(method, path, params=params, json=body)
        except httpx.HTTPError as exc:
            raise SpiderError(f"Cannot reach the Content Pilot Spider app: {exc}") from exc
        if r.status_code >= 400:
            try:
                j = r.json()
                detail = j.get("detail") or j.get("errors") or r.text
            except ValueError:
                detail = r.text
            raise SpiderError(f"{detail}"[:1200])
        return r if raw else r.json()

    def _save(r: httpx.Response, fallback: str, out_dir: str | None) -> str:
        d = Path(out_dir) if out_dir else EXPORT_DIR
        d.mkdir(parents=True, exist_ok=True)
        m = re.search(r'filename="([^"]+)"', r.headers.get("content-disposition", ""))
        p = d / _fname(m.group(1) if m else fallback)
        p.write_bytes(r.content)
        return str(p)

    async def _resolve_id(url: str) -> int:
        s = url.strip()
        if s.isdigit():
            return int(s)
        for key in ("internal:all", "external:all"):
            d = await call("GET", "/api/urls", {"key": key, "q": s, "size": 50})
            for row in d.get("data", []):
                if row.get("url") in (s, s.rstrip("/"), s + "/"):
                    return int(row["id"])
        raise SpiderError(f"No crawled URL matches '{s}'. Pass the exact address as shown in query_filter, or its numeric id.")

    # ------------------------------------------------------------------ crawls
    @srv.tool()
    async def crawl_start(url: str = "", mode: Literal["spider", "sitemap", "list"] = "spider", urls: list[str] | None = None,
                          max_urls: int | None = None, max_depth: int | None = None,
                          rendering: Literal["text", "javascript"] | None = None, config: dict[str, Any] | None = None) -> dict[str, Any]:
        """Start a new crawl and return at once (follow with crawl_wait or crawl_status). spider = follow links from
        `url`; sitemap = fetch every URL listed in the XML sitemap at `url`; list = crawl exactly `urls`. Optional
        overrides apply to this crawl only: max_urls / max_depth (0 = unlimited), rendering ('javascript' needs
        headless Chromium, see system_check), or any CrawlConfig field in `config` (see get_config)."""
        over = dict(config or {})
        if max_urls is not None:
            over["max_urls"] = max_urls
        if max_depth is not None:
            over["max_depth"] = max_depth
        if rendering:
            over["rendering"] = rendering
        return await call("POST", "/api/crawl/start", body={"start_url": url, "mode": mode, "list_urls": urls or [], "config": over})

    @srv.tool()
    async def crawl_status() -> dict[str, Any]:
        """Progress of the current crawl: state (idle/running/paused/stopping/finished/stopped/error), URLs crawled and
        queued, elapsed seconds, rate, the URLs being fetched right now, and the crawl file."""
        return await call("GET", "/api/crawl/status")

    @srv.tool()
    async def crawl_wait(timeout_seconds: int = 300, poll_seconds: float = 2.0) -> dict[str, Any]:
        """Block until the running crawl finishes (or `timeout_seconds` pass) and return its final status. Call again if
        `timed_out` is true. Returns immediately when nothing is running."""
        end = time.time() + max(1, timeout_seconds)
        while True:
            s = await call("GET", "/api/crawl/status")
            if s.get("state") not in RUNNING or time.time() >= end:
                s["timed_out"] = s.get("state") in RUNNING
                return s
            await asyncio.sleep(max(0.5, poll_seconds))

    @srv.tool()
    async def crawl_control(action: Literal["pause", "resume", "stop"]) -> dict[str, Any]:
        """Pause, resume or stop the running crawl. Stopping keeps everything crawled so far and runs the final analysis."""
        return await call("POST", f"/api/crawl/{action}")

    @srv.tool()
    async def list_crawls() -> list[dict[str, Any]]:
        """Saved crawl files (newest first): name, site, state, URL count, size, date, and which one is open."""
        d = await call("GET", "/api/crawls")
        return [{"name": c["name"], "start_url": c["start_url"], "state": c["state"], "urls": c["urls"], "size_mb": round(c["size"] / 1e6, 1),
                 "modified": c["modified"], "open": c["open"]} for c in d.get("crawls", [])]

    @srv.tool()
    async def open_crawl(name: str) -> dict[str, Any]:
        """Open a saved crawl by file name (from list_crawls; a unique part of the name or the site host also works).
        Every other tool then reads from it."""
        d = await call("GET", "/api/crawls")
        crawls = d.get("crawls", [])
        hit = [c for c in crawls if c["name"] == name or c["path"] == name]
        if not hit:
            hit = [c for c in crawls if name.lower() in c["name"].lower() or name.lower() in (c["start_url"] or "").lower()]
        if not hit:
            raise SpiderError(f"No saved crawl matches '{name}'. Use list_crawls to see the names.")
        if len(hit) > 1 and not any(c["name"] == name for c in hit):
            raise SpiderError("Several crawls match: " + ", ".join(c["name"] for c in hit[:8]) + ". Pass the full file name.")
        c = hit[0]
        await call("POST", "/api/crawls/open", body={"path": c["path"]})
        return {"ok": True, "opened": c["name"], "start_url": c["start_url"], "urls": c["urls"], "state": c["state"]}

    @srv.tool()
    async def run_analysis() -> dict[str, Any]:
        """Re-run crawl analysis on the open crawl (inlink counts, link score, duplicates, redirect/canonical chains,
        hreflang, sitemap membership). Runs automatically during and after a crawl; use it after changing thresholds."""
        return await call("POST", "/api/analysis/run")

    # ------------------------------------------------------------------ inspect
    @srv.tool()
    async def overview() -> dict[str, Any]:
        """Summary of the open crawl: site, totals (internal/external/indexable), status code, depth and response time
        distributions, and the top issues by priority."""
        o, cfg, iss = await asyncio.gather(call("GET", "/api/overview"), call("GET", "/api/config"), call("GET", "/api/issues"))
        issues = iss.get("issues", [])
        return {"start_url": cfg["config"].get("start_url", ""), **o,
                "issue_count": len(issues),
                "top_issues": [{k: i[k] for k in ("key", "name", "kind", "priority", "count")} for i in issues[:15]]}

    @srv.tool()
    async def site_audit() -> dict[str, Any]:
        """The Site Audit dashboard for the open crawl: Site Health score (0-100) with grade, errors / warnings /
        notices (issue types and affected pages), crawled-pages breakdown (healthy, with errors, broken, redirects,
        blocked), thematic scores (crawlability, HTTPS, Core Web Vitals, performance, internal linking, on-page, markup,
        international, accessibility) each with its costliest issues, Core Web Vitals summary when Lighthouse ran, and
        the top issues ranked by how many points they cost."""
        return await call("GET", "/api/audit")

    @srv.tool()
    async def site_audit_history() -> dict[str, Any]:
        """Site Health trend: one point per completed crawl of the same site (health, errors, warnings, notices, pages)."""
        return await call("GET", "/api/audit/history")

    @srv.tool()
    async def list_issues() -> list[dict[str, Any]]:
        """Every issue the open crawl triggers (the Issues sidebar), sorted by priority then count: filter key, tab, name,
        kind (issue/warning/opportunity), priority, URL count, what it means and how to fix it."""
        return (await call("GET", "/api/issues")).get("issues", [])

    @srv.tool()
    async def list_filters(tab: str | None = None, only_nonempty: bool = True) -> list[dict[str, Any]]:
        """All tabs and filters with their URL counts, like Screaming Frog's right-hand overview. `tab` limits to one
        tab key (internal, external, security, response_codes, url, page_titles, meta_description, h1, h2, content,
        images, canonicals, pagination, directives, hreflang, javascript, links, validation, sitemaps, ...)."""
        d = await call("GET", "/api/tabs")
        out = []
        for t in d.get("tabs", []):
            if tab and t["key"] != tab:
                continue
            for f in t["filters"]:
                if only_nonempty and not f["count"]:
                    continue
                out.append(_compact({"key": f["key"], "tab": t["key"], "name": f["name"], "kind": f["kind"], "priority": f["priority"], "count": f["count"]}))
        return out

    @srv.tool()
    async def query_filter(key: str, page: int = 1, size: int = 50, search: str = "", sort: str = "", descending: bool = False,
                           columns: list[str] | None = None) -> dict[str, Any]:
        """URLs in a filter (key = 'tab:filter' from list_filters / list_issues), paged. `search` matches address, title,
        H1 or meta description. `sort` is a column name. `columns` picks which columns to return (default: that tab's
        columns, empty values dropped). Each row carries `id` and `url` for url_details."""
        size = max(1, min(500, size))
        d = await call("GET", "/api/urls", {"key": key, "page": page, "size": size, "q": search, "sort": sort, "dir": "desc" if descending else "asc"})
        rows = []
        for r in d.get("data", []):
            if columns:
                rows.append({k: r.get(k) for k in ["id", "url"] + [c for c in columns if c not in ("id", "url")]})
            else:
                rows.append(_compact(r))
        return {"key": key, "total": d.get("total", 0), "page": page, "pages": d.get("last_page", 1), "columns": d.get("columns", []), "rows": rows}

    @srv.tool()
    async def url_details(url: str, include_links: bool = True, max_links: int = 50) -> dict[str, Any]:
        """Everything the crawler knows about one URL (exact address, or numeric id from query_filter): status,
        indexability, title/meta/H1/H2 with lengths, canonical, directives, word count, response headers, cookies,
        the issues it triggers, plus inlinks and outlinks (first `max_links` of each, with totals)."""
        uid = await _resolve_id(url)
        d = await call("GET", f"/api/url/{uid}")
        link_keys = ("kind", "source", "destination", "anchor", "alt", "follow", "status_code", "position")
        out: dict[str, Any] = {}
        for k, v in d.items():
            if k in ("inlinks", "outlinks", "images", "resources", "uncrawlable"):
                continue
            if v in (None, "", 0, []) and not k.startswith(("title", "meta_desc", "h1", "status", "indexab", "canonical", "word")):
                continue
            out[k] = v
        out["issue_count"] = len(d.get("issue_names", []))
        if include_links:
            for k in ("inlinks", "outlinks", "images", "resources", "uncrawlable"):
                rows = d.get(k, [])
                out[f"{k}_total"] = len(rows)
                out[k] = [_compact({kk: r.get(kk) for kk in link_keys}) if k != "uncrawlable" else r for r in rows[:max(0, max_links)]]
        return out

    @srv.tool()
    async def url_source(url: str, kind: Literal["text", "html", "rendered"] = "text", max_chars: int = 20000, offset: int = 0) -> dict[str, Any]:
        """Stored body of a crawled URL: `text` = extracted visible text, `html` = original HTML, `rendered` = HTML after
        JavaScript (only for JS crawls). Paged with `offset`/`max_chars`."""
        uid = await _resolve_id(url)
        r = await call("GET", f"/api/url/{uid}/source", {"kind": kind}, raw=True)
        text = r.text
        return {"id": uid, "kind": kind, "total_chars": len(text), "offset": offset, "text": text[offset:offset + max(100, max_chars)],
                "truncated": offset + max_chars < len(text)}

    @srv.tool()
    async def site_structure(max_depth: int = 4, max_children: int = 25) -> dict[str, Any]:
        """Folder tree of the crawled HTML pages: pages and indexable pages per folder, nested `children`."""
        return await call("GET", "/api/structure", {"max_depth": max_depth, "max_children": max_children})

    @srv.tool()
    async def crawl_tree(mode: Literal["crawl", "directory"] = "crawl", max_nodes: int = 300) -> dict[str, Any]:
        """Site hierarchy as nested JSON: 'crawl' = shortest click path from the start page over internal links (what
        the Crawl Tree / force-directed diagrams draw, with orphan count and max depth), 'directory' = URL folders with
        page counts and pages as leaves. Each node has url, status, indexable, inlinks, depth."""
        return await call("GET", "/api/viz/tree", {"mode": mode, "max_nodes": max_nodes})

    @srv.tool()
    async def word_frequencies(kind: Literal["anchor", "body"] = "anchor", limit: int = 100) -> dict[str, Any]:
        """Most frequent words in internal inlink anchor text ('anchor') or in indexable page text ('body'), stop
        words removed: the data behind the word clouds, useful for spotting over-optimised or missing anchor terms."""
        return await call("GET", "/api/viz/words", {"kind": kind, "limit": limit})

    @srv.tool()
    async def issues_markdown() -> str:
        """Paste-ready Markdown summary of every triggered issue (name, type, priority, count, meaning, fix) for a
        ticket, doc or report."""
        r = await call("GET", "/api/export/issues.md", raw=True)
        return r.text

    # ------------------------------------------------------------------ schedules
    @srv.tool()
    async def schedules_list() -> dict[str, Any]:
        """Scheduled crawls: name, site, frequency (hourly/daily/weekly/once), next and last run, what each run does
        (export, compare, Lighthouse, Google), and the last runs' results."""
        return await call("GET", "/api/schedules")

    @srv.tool()
    async def schedule_add(url: str, name: str = "", frequency: Literal["hourly", "daily", "weekly", "once"] = "daily", time_of_day: str = "03:00", day: str = "mon",
                           every_hours: int = 6, once_at: str = "", max_urls: int = 0, export: Literal["issues", "everything", "none"] = "issues", out_dir: str = "",
                           compare: bool = True, lighthouse: int = 0, google: bool = False, mode: Literal["spider", "sitemap", "list"] = "spider") -> dict[str, Any]:
        """Create a scheduled crawl. The run crawls `url`, exports the issues pack (or everything) to `out_dir`, compares
        with the previous crawl of the site, optionally audits `lighthouse` top pages and fetches Google data, and sends
        the crawl-complete email if Config → Email is on. Runs while the app is open; `python cli.py schedule
        install-task` adds a Windows task so runs happen when it is closed."""
        body = {"url": url, "name": name or url, "frequency": frequency, "time": time_of_day, "day": day, "every_hours": every_hours, "once_at": once_at,
                "config": {"max_urls": max_urls} if max_urls else {}, "export": export, "out_dir": out_dir, "compare": compare, "lighthouse": lighthouse, "google": google, "mode": mode}
        return await call("POST", "/api/schedules", body=body)

    @srv.tool()
    async def schedule_run(schedule_id: str) -> dict[str, Any]:
        """Run a schedule now (background). Follow with crawl_status / crawl_wait; the result lands in schedules_list history."""
        return await call("POST", f"/api/schedules/{schedule_id}/run")

    @srv.tool()
    async def schedule_remove(schedule_id: str) -> dict[str, Any]:
        """Delete a schedule."""
        r = await call("DELETE", f"/api/schedules/{schedule_id}", raw=True)
        return r.json()

    # ------------------------------------------------------------------ Google Search Console + GA4
    @srv.tool()
    async def google_fetch(gsc: bool = True, ga4: bool = True, inspect: int = 0, wait: bool = True, timeout_seconds: int = 600) -> dict[str, Any]:
        """Pull Search Console (clicks, impressions, CTR, position per page) and GA4 (sessions, engagement, bounce rate,
        views, key events per page) for the configured date range into the open crawl, adding orphan URLs that have
        traffic but were never crawled. `inspect` = number of pages to run through the URL Inspection API (index
        verdict, coverage, Google's canonical; quota 2,000/day). Needs a connected Google account (Config → Search
        Console & GA4). Then use the search_console:* and analytics:* filters and url_details."""
        started = await call("POST", "/api/google/run", body={"gsc": gsc, "ga4": ga4, "inspect": inspect})
        if not wait:
            return started
        end = time.time() + max(30, timeout_seconds)
        while True:
            s = (await call("GET", "/api/google/status"))["job"]
            if s.get("state") in ("done", "error", "cancelled", "idle") or time.time() >= end:
                return s
            await asyncio.sleep(2)

    @srv.tool()
    async def google_status() -> dict[str, Any]:
        """Whether a Google account is connected, which Search Console / GA4 properties are selected, when data was last
        fetched into the open crawl, and the state of a running fetch."""
        return await call("GET", "/api/google/status")

    # ------------------------------------------------------------------ link metrics
    @srv.tool()
    async def linkmetrics_run(urls: list[str] | None = None, filter_key: str = "", sample: int = 0, wait: bool = True, timeout_seconds: int = 600) -> dict[str, Any]:
        """Fetch Ahrefs (UR, DR, backlinks, referring domains, keywords, traffic), Moz (PA, DA, spam score, linking
        domains) and/or Majestic (Trust Flow, Citation Flow, backlinks, ref. domains) for pages, using the keys in
        Config → Link metrics; capped at linkmetrics_max_urls per run because every URL costs credits. Pass exact
        `urls`, a `filter_key`, or `sample` = N top pages. Then query_filter('link_metrics:has') and the other
        link_metrics:* filters, or url_details(url) for the lm_* fields."""
        ids: list[int] = []
        for u in urls or []:
            ids.append(await _resolve_id(u))
        started = await call("POST", "/api/linkmetrics/run", body={"ids": ids, "key": filter_key, "sample": sample})
        if not wait:
            return started
        end = time.time() + max(30, timeout_seconds)
        while True:
            s = await call("GET", "/api/linkmetrics/status")
            if s.get("state") in ("done", "cancelled", "error", "idle") or time.time() >= end:
                s.update({k: started[k] for k in ("urls", "providers")})
                return s
            await asyncio.sleep(2)

    # ------------------------------------------------------------------ Lighthouse / PageSpeed
    @srv.tool()
    async def lighthouse_run(urls: list[str] | None = None, filter_key: str = "", sample: int = 0, wait: bool = True, timeout_seconds: int = 900) -> dict[str, Any]:
        """Audit pages with Lighthouse (performance, accessibility, best practices, SEO; local headless Chrome, or the
        PageSpeed Insights API when a key is configured, which adds CrUX field data). Pass exact `urls`, a `filter_key`
        (every page in that filter, max 50), or `sample` = N top pages (start page + most-linked indexable pages).
        ~25 s per page locally. Results: query_filter('pagespeed:audited'), the pagespeed:* / accessibility:* filters,
        and url_details(url)['lighthouse'] (scores, metrics, failing audits with savings and sample elements)."""
        ids: list[int] = []
        for u in urls or []:
            ids.append(await _resolve_id(u))
        started = await call("POST", "/api/lighthouse/run", body={"ids": ids, "key": filter_key, "sample": sample})
        if not wait:
            return started
        end = time.time() + max(30, timeout_seconds)
        while True:
            s = await call("GET", "/api/lighthouse/status")
            if s.get("state") in ("done", "cancelled", "error", "idle") or time.time() >= end:
                s["timed_out"] = s.get("state") == "running"
                s.update({k: started[k] for k in ("pages", "source", "device")})
                return s
            await asyncio.sleep(3)

    @srv.tool()
    async def lighthouse_status() -> dict[str, Any]:
        """Progress of the current Lighthouse run and how many pages in the open crawl have Lighthouse data."""
        return await call("GET", "/api/lighthouse/status")

    # ------------------------------------------------------------------ compare (change detection)
    @srv.tool()
    async def compare_crawls(before: str = "", after: str = "", limit: int = 200) -> dict[str, Any]:
        """Change detection between two crawls of a site: pages added / removed / changed (per-field before and after:
        status, indexability, title, meta description, H1, canonical, robots, word count, inlinks, depth, redirect) and
        every issue's count before vs after. `after` defaults to the open crawl, `before` to the previous completed
        crawl of the same site (names from list_crawls). Lists are trimmed to `limit`; summary has the totals."""
        return await call("GET", "/api/compare", {"a": before, "b": after, "limit": limit})

    @srv.tool()
    async def export_compare(before: str = "", after: str = "", out_dir: str | None = None) -> dict[str, Any]:
        """Write the change-detection workbook (Summary, Issues, Changed, Added, Removed sheets) and return its path."""
        r = await call("GET", "/api/compare/export", {"a": before, "b": after}, raw=True)
        return {"file": _save(r, "compare.xlsx", out_dir)}

    # ------------------------------------------------------------------ reports & exports
    @srv.tool()
    async def list_reports() -> list[dict[str, str]]:
        """Built-in reports (crawl_overview, issues_overview, all_inlinks, redirect_chains, orphan_pages, serp_summary, ...)
        for run_report / export_report."""
        return (await call("GET", "/api/reports")).get("reports", [])

    @srv.tool()
    async def run_report(name: str, max_rows: int = 200) -> dict[str, Any]:
        """Run a report (key from list_reports) and return its sheets as JSON: headers plus the first `max_rows` rows of
        each sheet with the full row count. Use export_report for the whole thing as a file."""
        d = await call("GET", f"/api/report/{name}", {"format": "json"})
        sheets = []
        for s in d.get("sheets", []):
            rows = s.get("rows", [])
            sheets.append({"name": s["name"], "headers": s["headers"], "total_rows": len(rows), "rows": rows[:max(1, max_rows)]})
        return {"report": name, "sheets": sheets}

    @srv.tool()
    async def export_filters(keys: list[str], format: Literal["csv", "xlsx"] = "csv", with_inlinks: bool = False,
                             out_dir: str | None = None) -> dict[str, Any]:
        """Bulk export: one file per filter key, written to `out_dir` (default <app>/exports). `with_inlinks` adds a second
        sheet with every link pointing at the affected URLs (xlsx). Returns the file paths."""
        files = []
        for key in keys:
            if with_inlinks:
                r = await call("GET", "/api/export/issue", {"key": key, "what": "both", "format": "xlsx"}, raw=True)
                files.append(_save(r, key.replace(":", "_") + "_with_inlinks.xlsx", out_dir))
            else:
                r = await call("GET", "/api/export", {"key": key, "format": format}, raw=True)
                files.append(_save(r, key.replace(":", "_") + "." + format, out_dir))
        return {"files": files, "count": len(files)}

    @srv.tool()
    async def export_report(name: str, format: Literal["csv", "xlsx"] = "xlsx", out_dir: str | None = None) -> dict[str, Any]:
        """Write a report (key from list_reports) to a CSV or XLSX file and return its path."""
        r = await call("GET", f"/api/report/{name}", {"format": format}, raw=True)
        return {"file": _save(r, f"{name}.{format}", out_dir)}

    @srv.tool()
    async def export_bundle(parts: list[str] | None = None, format: Literal["xlsx", "zip"] = "xlsx", include_plain_filters: bool = False,
                            out_dir: str | None = None) -> dict[str, Any]:
        """The Export Centre as one file: a multi-sheet workbook or a zip of CSVs. `parts` from: overview, issues,
        issue_urls, issue_inlinks, matrix, tabs, filters, links, reports, fixes, or ['all']. Default = the issues pack
        (overview, issues, issue_urls, issue_inlinks, matrix). Returns the path."""
        chosen = parts or ["overview", "issues", "issue_urls", "issue_inlinks", "matrix"]
        if "all" in chosen:
            chosen = [p["key"] for p in (await call("GET", "/api/export/parts")).get("parts", [])]
        r = await call("GET", "/api/export/bundle", {"parts": ",".join(chosen), "format": format, "all_filters_too": int(include_plain_filters)}, raw=True)
        return {"file": _save(r, f"spider-export.{format}", out_dir), "parts": chosen}

    @srv.tool()
    async def export_sitemap(include_noindex: bool = False, include_canonicalised: bool = False, include_paginated: bool = False, include_pdfs: bool = False,
                             lastmod: bool = True, priority: bool = True, changefreq: bool = True, images: bool = False, out_dir: str | None = None) -> dict[str, Any]:
        """Generate an XML sitemap from the open crawl (indexable 2xx HTML pages by default; options add noindex,
        canonicalised, paginated pages and PDFs; lastmod from the Last-Modified header, priority / changefreq by crawl
        depth, optional image entries). Splits into sitemap1.xml… + sitemap_index.xml above 50,000 URLs. Returns the
        file path and how many URLs were included / excluded and why."""
        params = {"include_noindex": include_noindex, "include_canonicalised": include_canonicalised, "include_paginated": include_paginated,
                  "include_pdfs": include_pdfs, "lastmod": lastmod, "priority": priority, "changefreq": changefreq, "images": images}
        q = {k: str(v).lower() for k, v in params.items()}
        prev = await call("GET", "/api/sitemap/preview", q)
        r = await call("GET", "/api/sitemap/generate", q, raw=True)
        return {"file": _save(r, "sitemap.xml", out_dir), "urls": prev.get("urls"), "excluded": prev.get("excluded"), "files": prev.get("files")}

    @srv.tool()
    async def export_fixes(format: Literal["csv", "xlsx", "json"] = "xlsx", status: str = "", out_dir: str | None = None) -> dict[str, Any]:
        """Write the fix queue (optionally one status: proposed/approved/rejected/pushed/failed) to a file and return its path."""
        r = await call("GET", "/api/fixes/export", {"format": format, "status": status}, raw=True)
        return {"file": _save(r, f"fixes.{format}", out_dir)}

    # ------------------------------------------------------------------ fixes
    @srv.tool()
    async def list_targets() -> list[dict[str, Any]]:
        """Push targets configured in Config > Connectors (name, type, env staging/live, site URL). Secrets are not returned."""
        cfg = (await call("GET", "/api/config"))["config"]
        return [{"name": t.get("name"), "type": t.get("type"), "env": t.get("env"), "base_url": t.get("base_url")} for t in cfg.get("targets", [])]

    @srv.tool()
    async def fixes_generate(key: str, ids: list[int] | None = None, wait: bool = True, timeout_seconds: int = 600) -> dict[str, Any]:
        """Draft fixes for every URL in a filter (or just `ids`) with the configured AI provider: titles, meta descriptions,
        H1s, image alt text, redirect targets for 404s, self-canonicals; anything else gets a developer brief. Proposals
        land in the review queue as 'proposed'. Waits for completion by default."""
        started = await call("POST", "/api/fixes/generate", body={"key": key, "ids": ids or []})
        if not wait:
            return started
        end = time.time() + max(5, timeout_seconds)
        while True:
            s = await call("GET", "/api/fixes/status")
            if s.get("state") in ("done", "error", "cancelled", "idle") or time.time() >= end:
                s["kind"] = started.get("kind")
                s["timed_out"] = s.get("state") == "running"
                return s
            await asyncio.sleep(2)

    @srv.tool()
    async def fixes_status() -> dict[str, Any]:
        """Progress of the current fix generation plus counts per status in the queue."""
        return await call("GET", "/api/fixes/status")

    @srv.tool()
    async def fixes_cancel() -> dict[str, Any]:
        """Cancel the running fix generation (proposals already created are kept)."""
        return await call("POST", "/api/fixes/cancel")

    @srv.tool()
    async def fixes_list(status: str = "", kind: str = "", search: str = "", page: int = 1, size: int = 100) -> dict[str, Any]:
        """The fix review queue: id, URL, kind, issue, current value, proposed value, rationale, status, and push result.
        Filter by status (proposed/approved/rejected/pushed/failed), kind (title/meta_description/h1/alt_text/redirect/
        canonical/brief) or a search string."""
        d = await call("GET", "/api/fixes", {"status": status, "kind": kind, "q": search, "page": page, "size": max(1, min(500, size))})
        keep = ("id", "url", "kind", "field", "issue_name", "current", "proposed", "rationale", "status", "target", "result", "model")
        return {"total": d.get("total", 0), "page": page, "pages": d.get("last_page", 1), "kinds": d.get("kinds", []),
                "fixes": [_compact({k: r.get(k) for k in keep}) for r in d.get("data", [])]}

    @srv.tool()
    async def fixes_update(id: int, proposed: str | None = None, status: Literal["proposed", "approved", "rejected"] | None = None) -> dict[str, Any]:
        """Edit one proposal's text and/or set its status."""
        return await call("POST", "/api/fixes/update", body={"id": id, "proposed": proposed, "status": status})

    @srv.tool()
    async def fixes_bulk(ids: list[int] | None = None, status: Literal["proposed", "approved", "rejected"] = "approved",
                         filter_status: str = "", filter_kind: str = "", delete: bool = False) -> dict[str, Any]:
        """Approve / reject / reset many fixes at once: by `ids`, or every fix matching filter_status / filter_kind.
        `delete=true` removes them instead. Pushed fixes are never changed."""
        return await call("POST", "/api/fixes/bulk", body={"ids": ids or [], "status": status, "filter_status": filter_status, "filter_kind": filter_kind, "delete": delete})

    @srv.tool()
    async def fixes_push(target: str, ids: list[int] | None = None, confirm: str = "") -> dict[str, Any]:
        """Apply approved fixes (all, or `ids`) through a connector target from list_targets. A LIVE target refuses unless
        `confirm` equals the target name: get the person's explicit go-ahead first. Returns pushed/failed/skipped counts
        and a per-fix result."""
        return await call("POST", "/api/fixes/push", body={"target": target, "ids": ids or [], "confirm": confirm})

    # ------------------------------------------------------------------ config & system
    @srv.tool()
    async def get_config() -> dict[str, Any]:
        """Current crawl configuration (every CrawlConfig field: crawl toggles, limits, rendering, thresholds, AI provider,
        connectors...) with secrets redacted. Field names are what set_config and crawl_start(config=...) accept."""
        cfg = dict((await call("GET", "/api/config"))["config"])
        if cfg.get("ai_api_key"):
            cfg["ai_api_key"] = REDACT
        if cfg.get("auth_pass"):
            cfg["auth_pass"] = REDACT
        if cfg.get("smtp_pass"):
            cfg["smtp_pass"] = REDACT
        for k in ("google_client_secret", "google_refresh_token", "psi_api_key", "ahrefs_token", "moz_secret", "majestic_key"):
            if cfg.get(k):
                cfg[k] = REDACT
        cfg["custom_headers"] = {k: REDACT for k in (cfg.get("custom_headers") or {})}
        cfg["targets"] = [{**t, "secret": REDACT if t.get("secret") else "", "extra": {}} for t in cfg.get("targets", [])]
        return cfg

    @srv.tool()
    async def set_config(changes: dict[str, Any]) -> dict[str, Any]:
        """Change and save configuration fields (see get_config), e.g. {"max_threads": 3, "title_max_chars": 65}. Validation
        errors come back per field. Secrets (API keys, passwords) are left to the app's own Config window."""
        for k in ("ai_api_key", "auth_pass", "targets", "custom_headers", "smtp_pass", "google_client_secret", "google_refresh_token", "psi_api_key", "ahrefs_token", "moz_secret", "majestic_key"):
            changes.pop(k, None)
        await call("POST", "/api/config", body=changes)
        return {"ok": True, "changed": sorted(changes)}

    @srv.tool()
    async def ai_models() -> dict[str, Any]:
        """Models the configured AI provider offers right now, and whether the chosen model is among them (valid =
        true/false, or null when the provider could not be asked, with the reason in `error`)."""
        return await call("GET", "/api/ai/models")

    @srv.tool()
    async def ai_test() -> dict[str, Any]:
        """Send a one-word test prompt to the configured AI provider; the message also warns if the chosen model is not
        in the provider's model list."""
        return await call("POST", "/api/ai/test")

    @srv.tool()
    async def email_test() -> dict[str, Any]:
        """Send the crawl-complete report for the open crawl to the recipients in Config > Email (needs SMTP settings
        there). Crawls started with crawl_start send it automatically when email is enabled."""
        return await call("POST", "/api/email/test")

    @srv.tool()
    async def spelling_run(ignore_words: list[str] | None = None) -> dict[str, Any]:
        """Re-run the spelling & grammar check on the open crawl (enable spell_check / grammar_check via set_config first).
        `ignore_words` are added to the dictionary first. Then query_filter('content:spelling_errors') and
        url_details(url)['spelling'] show the findings, run_report('spelling') the per-word summary."""
        last: dict[str, Any] = {}
        for w in ignore_words or []:
            last = await call("POST", "/api/spelling/ignore", body={"word": w})
        return last or await call("POST", "/api/spelling/run")

    @srv.tool()
    async def usage_stats() -> dict[str, Any]:
        """Usage so far: crawls run, URLs and pages fetched, crawl time, AI requests / tokens / estimated cost per
        provider and model (Anthropic prices built in; others as set on the Usage page), and the same per run."""
        return await call("GET", "/api/usage")

    @srv.tool()
    async def system_check() -> dict[str, Any]:
        """What this machine can do: Playwright + Chromium present (JavaScript rendering), crawls folder, free disk."""
        return await call("GET", "/api/system/check")

    return srv


def _schema_type(p: dict[str, Any]) -> str:
    if "enum" in p:
        return " / ".join(f"'{e}'" for e in p["enum"])
    if "anyOf" in p:
        return " / ".join(_schema_type(x) for x in p["anyOf"])
    t = p.get("type", "any")
    if t == "array":
        return f"{_schema_type(p.get('items', {}))}[]"
    return str(t)


async def tools_markdown(srv: MCPServer, http_url: str = "") -> str:
    """The 'View tools' export: every tool with its parameters, as Markdown."""
    tools = await srv.list_tools()
    lines = ["# Content Pilot Spider MCP tools", ""]
    if http_url:
        lines += [f"Streamable HTTP endpoint: `{http_url}`  -  stdio: `python mcp_server.py` in the app folder.", ""]
    lines += [INSTRUCTIONS.strip(), "", f"{len(tools)} tools.", ""]
    for t in tools:
        lines += [f"## {t.name}", "", inspect.cleandoc(t.description or ""), ""]
        props = (t.input_schema or {}).get("properties", {})
        req = set((t.input_schema or {}).get("required", []))
        if props:
            lines += ["| parameter | type | default |", "|---|---|---|"]
            for n, p in props.items():
                default = "required" if n in req else repr(p.get("default", None))
                lines.append(f"| `{n}` | {_schema_type(p)} | {default} |")
            lines.append("")
    return "\n".join(lines)
