"""End-to-end check of the MCP server over both transports.

    .venv\\Scripts\\python.exe tests\\check_mcp.py            # uses the newest saved crawl
    .venv\\Scripts\\python.exe tests\\check_mcp.py --crawl    # also runs a small live crawl through the tools

Starts the app on a free port in-process, drives it with the official MCP client over Streamable HTTP, then launches
mcp_server.py as a subprocess (stdio) pointed at the same app and checks it attaches.
"""
from __future__ import annotations

import asyncio
import json
import logging
import os
import sys
import threading
import time
import urllib.error
import urllib.request
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
from urllib.parse import urlsplit  # noqa: E402
from fixture import fixture_start  # noqa: E402
LIVE_SITE = fixture_start()   # the live site the fixture crawl came from, for the small live crawls
LIVE_HOST = urlsplit(LIVE_SITE).netloc

import uvicorn  # noqa: E402
from mcp.client.client import Client  # noqa: E402
from mcp.client.stdio import StdioServerParameters  # noqa: E402

from spider.instance import free_port, wait_ready  # noqa: E402

FAILS: list[str] = []
logging.basicConfig(level=logging.WARNING)
logging.getLogger("httpx").setLevel(logging.WARNING)


def check(cond: bool, label: str, extra: str = "") -> None:
    print(("  ok   " if cond else "  FAIL ") + label + (f"  ({extra})" if extra else ""))
    if not cond:
        FAILS.append(label)


def data(res) -> object:
    """Structured content if the tool returned one, else the first text block parsed as JSON / raw."""
    if res.structured_content is not None:
        sc = res.structured_content
        return sc["result"] if isinstance(sc, dict) and set(sc) == {"result"} else sc
    txt = "".join(getattr(c, "text", "") for c in res.content)
    try:
        return json.loads(txt)
    except ValueError:
        return txt


async def call(c: Client, tool: str, **args):
    res = await c.call_tool(tool, args or None, read_timeout_seconds=900)
    if res.is_error:
        raise RuntimeError(f"{tool}: " + "".join(getattr(x, "text", "") for x in res.content))
    return data(res)


async def drive(c: Client, live_crawl: bool) -> None:
    tools = (await c.list_tools()).tools
    names = {t.name for t in tools}
    check(len(tools) >= 30, f"{len(tools)} tools listed")
    for must in ("crawl_start", "crawl_wait", "list_issues", "query_filter", "url_details", "run_report", "export_bundle", "fixes_generate", "fixes_push"):
        check(must in names, f"tool {must} present")

    crawls = await call(c, "list_crawls")
    check(isinstance(crawls, list) and crawls, f"list_crawls -> {len(crawls)} crawls")
    if not crawls:
        return
    newest = crawls[0]["name"]
    opened = await call(c, "open_crawl", name=newest)
    check(opened.get("ok") is True, f"open_crawl({newest})", opened.get("start_url", ""))

    ov = await call(c, "overview")
    check(ov.get("total", 0) > 0, "overview", f"{ov.get('total')} URLs, {ov.get('issue_count')} issues")

    issues = await call(c, "list_issues")
    check(isinstance(issues, list), f"list_issues -> {len(issues)}")
    filters = await call(c, "list_filters", tab="page_titles")
    check(any(f["key"].startswith("page_titles:") for f in filters), "list_filters(tab=page_titles)")

    key = (issues[0]["key"] if issues else "internal:html")
    q = await call(c, "query_filter", key=key, size=5)
    check(q.get("total", 0) >= 0 and "rows" in q, f"query_filter({key})", f"{q.get('total')} rows total")
    q2 = await call(c, "query_filter", key="internal:html", size=3, columns=["title", "status_code"])
    check(q2["rows"] and set(q2["rows"][0]) == {"id", "url", "title", "status_code"}, "query_filter columns=")

    # a page with a stored body: 200 and some words (the first HTML row is often a redirect)
    page = await call(c, "query_filter", key="internal:html", size=20, columns=["status_code", "word_count"])
    live = [r for r in page["rows"] if r.get("status_code") == 200 and (r.get("word_count") or 0) > 0] or q2["rows"]
    if live:
        q2 = {"rows": live}
        u = q2["rows"][0]["url"]
        det = await call(c, "url_details", url=u, max_links=5)
        check(det.get("url") == u and "inlinks_total" in det, "url_details(url)", f"{det.get('inlinks_total')} inlinks, {det.get('issue_count')} issues")
        det2 = await call(c, "url_details", url=str(q2["rows"][0]["id"]), include_links=False)
        check(det2.get("url") == u and "inlinks" not in det2, "url_details(id, include_links=False)")
        src = await call(c, "url_source", url=u, kind="text", max_chars=500)
        check(src.get("total_chars", 0) > 0 and len(src["text"]) <= 500, "url_source", f"{src.get('total_chars')} chars")

    md = await call(c, "issues_markdown")
    check(isinstance(md, str) and md.startswith("#"), "issues_markdown", f"{len(md)} chars")
    tree = await call(c, "site_structure", max_depth=2, max_children=5)
    check(tree.get("pages", 0) > 0, "site_structure")

    reps = await call(c, "list_reports")
    check(any(r["key"] == "crawl_overview" for r in reps), "list_reports")
    rep = await call(c, "run_report", name="crawl_overview", max_rows=5)
    check(rep["sheets"] and rep["sheets"][0]["rows"], "run_report(crawl_overview)", f"{rep['sheets'][0]['total_rows']} rows")

    out_dir = str(ROOT / "exports" / "mcp-check")
    ex = await call(c, "export_filters", keys=[key, "internal:html"], format="csv", out_dir=out_dir)
    check(ex["count"] == 2 and all(Path(f).exists() for f in ex["files"]), "export_filters x2 csv")
    ex2 = await call(c, "export_filters", keys=[key], with_inlinks=True, out_dir=out_dir)
    check(Path(ex2["files"][0]).suffix == ".xlsx", "export_filters with_inlinks")
    bun = await call(c, "export_bundle", parts=["overview", "issues"], format="zip", out_dir=out_dir)
    check(Path(bun["file"]).exists() and Path(bun["file"]).stat().st_size > 100, "export_bundle zip")
    er = await call(c, "export_report", name="issues_overview", format="xlsx", out_dir=out_dir)
    check(Path(er["file"]).exists(), "export_report xlsx")
    fx = await call(c, "export_fixes", format="json", out_dir=out_dir)
    check(Path(fx["file"]).exists(), "export_fixes json")

    cfg = await call(c, "get_config")
    check("max_threads" in cfg and cfg.get("ai_api_key", "") in ("", "[redacted]"), "get_config redacted")
    cur = cfg["max_threads"]
    sc = await call(c, "set_config", changes={"max_threads": cur})
    check(sc.get("ok") is True, "set_config no-op")
    try:
        await call(c, "set_config", changes={"max_threads": 999})
        check(False, "set_config rejects out-of-range")
    except RuntimeError as exc:
        check("max_threads" in str(exc), "set_config rejects out-of-range", str(exc)[:80])

    fl = await call(c, "fixes_list", size=5)
    check("total" in fl, "fixes_list", f"{fl['total']} fixes")
    tg = await call(c, "list_targets")
    check(isinstance(tg, list) and all("secret" not in t for t in tg), "list_targets (no secrets)")
    st = await call(c, "crawl_status")
    check("state" in st, "crawl_status", st["state"])
    sysc = await call(c, "system_check")
    check("crawls_dir" in sysc, "system_check")
    try:
        await call(c, "url_details", url="https://nope.invalid/none")
        check(False, "url_details unknown URL errors")
    except RuntimeError as exc:
        check("No crawled URL" in str(exc), "url_details unknown URL errors")

    if live_crawl:
        started = await call(c, "crawl_start", url=LIVE_SITE, max_urls=30)
        check(started.get("ok") is True, "crawl_start")
        fin = await call(c, "crawl_wait", timeout_seconds=240)
        check(fin.get("state") in ("finished", "stopped") and fin.get("crawled", 0) > 0, "crawl_wait", f"{fin.get('state')} after {fin.get('elapsed')}s, {fin.get('crawled')} URLs")
        ov2 = await call(c, "overview")
        check(LIVE_HOST in ov2.get("start_url", ""), "overview after crawl", f"{ov2.get('total')} URLs")


def serve(port: int) -> None:
    uvicorn.run("spider.server:app", host="127.0.0.1", port=port, log_level="warning")


async def main() -> None:
    # never touch the real settings.json (the gate test toggles mcp_enabled)
    import spider.server as srv
    scratch = Path(os.environ.get("TEMP", ".")) / "claude" / "settings_mcp_test.json"
    if scratch.exists():
        scratch.unlink()
    srv.SETTINGS_PATH = scratch
    srv.State.cfg = srv.CrawlConfig()
    port = free_port()
    threading.Thread(target=serve, args=(port,), daemon=True).start()
    url = f"http://127.0.0.1:{port}"
    assert wait_ready(url), "app did not start"
    for _ in range(150):   # the MCP endpoint mounts a moment after the API is up
        if json.loads(urllib.request.urlopen(url + "/api/mcp/info").read()).get("ready"):
            break
        time.sleep(0.2)
    print(f"app on {url}")

    print("\n[HTTP transport]")
    async with Client(url + "/mcp") as c:
        check(c.server_info is not None and "spider" in (c.server_info.name or ""), "initialize", c.server_info.name if c.server_info else "")
        await drive(c, "--crawl" in sys.argv)

    print("\n[gate]")
    body = json.dumps({"mcp_enabled": False}).encode()
    urllib.request.urlopen(urllib.request.Request(url + "/api/config", data=body, headers={"Content-Type": "application/json"})).read()
    try:
        urllib.request.urlopen(urllib.request.Request(url + "/mcp", data=b"{}", headers={"Content-Type": "application/json", "Accept": "application/json, text/event-stream"})).read()
        check(False, "endpoint returns 404 when disabled")
    except urllib.error.HTTPError as exc:
        check(exc.code == 404 and b"turned off" in exc.read(), "endpoint returns 404 when disabled", str(exc.code))
    urllib.request.urlopen(urllib.request.Request(url + "/api/config", data=json.dumps({"mcp_enabled": True}).encode(), headers={"Content-Type": "application/json"})).read()
    async with Client(url + "/mcp") as c:
        check(len((await c.list_tools()).tools) > 0, "endpoint back after re-enable")

    info = json.loads(urllib.request.urlopen(url + "/api/mcp/info").read())
    check(info["enabled"] and info["tools"] >= 30 and "claude mcp add" in info["snippets"]["claude_code_http"], "/api/mcp/info")
    md = urllib.request.urlopen(url + "/api/mcp/tools.md").read().decode()
    check(md.startswith("# Content Pilot Spider MCP tools") and "## crawl_start" in md, "/api/mcp/tools.md", f"{len(md)} chars")

    print("\n[stdio transport -> attaches to the running app]")
    env = {**os.environ, "SPIDER_URL": url}
    params = StdioServerParameters(command=sys.executable, args=[str(ROOT / "mcp_server.py")], env=env, cwd=str(ROOT))
    t0 = time.time()
    async with Client(params) as c:
        tools = (await c.list_tools()).tools
        check(len(tools) >= 30, f"stdio: {len(tools)} tools in {time.time() - t0:.1f}s")
        ov = await call(c, "overview")
        check(ov.get("total", 0) > 0, "stdio: overview reads the same open crawl", f"{ov.get('total')} URLs")

    print()
    if FAILS:
        print(f"{len(FAILS)} FAILED: " + "; ".join(FAILS))
        sys.exit(1)
    print("all checks passed")


if __name__ == "__main__":
    asyncio.run(main())
