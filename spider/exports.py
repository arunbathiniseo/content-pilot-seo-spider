"""CSV / XLSX exports and the built-in reports."""
from __future__ import annotations

import csv
import io
import json
import sqlite3
from typing import Any, Iterable

from openpyxl import Workbook
from openpyxl.utils import get_column_letter

from .config import CrawlConfig
from .issues import COL_LABELS, COLUMNS, TABS, all_filters, counts


def _fmt(col: str, v: Any) -> Any:
    if v is None:
        return ""
    if col == "indexable":
        return "Indexable" if v == 1 else "Non-Indexable" if v == 0 else ""
    if col == "in_sitemap":
        return "Yes" if v else "No"
    if col in ("cookies", "hreflang", "custom") and isinstance(v, str):
        try:
            data = json.loads(v)
        except json.JSONDecodeError:
            return v
        if col == "hreflang":
            return "; ".join(f"{h.get('lang')}={h.get('url')}" for h in data)
        if col == "cookies":
            return ", ".join(data)
        return json.dumps(data, ensure_ascii=False)
    return v


# ----------------------------------------------------------------------------- column filters (Excel / Sheets style)
# Each filter: {"field": col, "op": one of FILTER_OPS, "value": str, "value2": str (between), "values": [str] (in / not_in)}.
# Text ops compare against the value as shown in the grid (see _fmt), so "Indexable" / "Yes" match what the user sees.
FILTER_OPS = {"contains", "not_contains", "equals", "not_equals", "starts", "ends", "empty", "not_empty",
              "gt", "gte", "lt", "lte", "between", "regex", "in", "not_in"}
_DISPLAY_SQL = {
    "indexable": "CASE u.indexable WHEN 1 THEN 'Indexable' WHEN 0 THEN 'Non-Indexable' ELSE '' END",
    "in_sitemap": "CASE WHEN u.in_sitemap IS NULL THEN '' WHEN u.in_sitemap THEN 'Yes' ELSE 'No' END",
}


def _num(v: Any) -> float | None:
    try:
        return float(str(v).strip().replace(",", ""))
    except (TypeError, ValueError):
        return None


def _like(v: str) -> str:
    return v.replace("\\", "\\\\").replace("%", "\\%").replace("_", "\\_")


def column_filter_sql(cols: list[str], filters: list[dict] | None, skip_field: str = "") -> tuple[str, list[Any]]:
    """SQL (to AND onto a WHERE over `urls u`) for the grid's column filters. Raises ValueError on a bad filter."""
    import re
    parts: list[str] = []
    params: list[Any] = []
    for f in filters or []:
        field, op = f.get("field", ""), f.get("op", "")
        if field not in cols or field == skip_field:
            continue
        if op not in FILTER_OPS:
            raise ValueError(f"Unknown filter operator: {op}")
        raw = _DISPLAY_SQL.get(field, f"u.{field}")
        txt = f"coalesce(CAST({raw} AS TEXT),'')"
        isnum = f"typeof({raw}) IN ('integer','real')"
        v = str(f.get("value", "") if f.get("value") is not None else "")
        if op in ("contains", "not_contains"):
            parts.append(f"{txt} {'NOT ' if op == 'not_contains' else ''}LIKE ? ESCAPE '\\'"); params.append(f"%{_like(v)}%")
        elif op == "starts":
            parts.append(f"{txt} LIKE ? ESCAPE '\\'"); params.append(f"{_like(v)}%")
        elif op == "ends":
            parts.append(f"{txt} LIKE ? ESCAPE '\\'"); params.append(f"%{_like(v)}")
        elif op in ("equals", "not_equals"):
            n = _num(v)
            expr = f"({txt} = ? COLLATE NOCASE OR ({isnum} AND {raw} = ?))" if n is not None else f"{txt} = ? COLLATE NOCASE"
            params += [v, n] if n is not None else [v]
            parts.append(expr if op == "equals" else f"NOT {expr}")
        elif op == "empty":
            parts.append(f"{txt} = ''")
        elif op == "not_empty":
            parts.append(f"{txt} <> ''")
        elif op in ("gt", "gte", "lt", "lte"):
            sym = {"gt": ">", "gte": ">=", "lt": "<", "lte": "<="}[op]
            n = _num(v)
            if n is not None:
                parts.append(f"({isnum} AND {raw} {sym} ?)"); params.append(n)
            else:                                           # dates and other text compare as text (ISO dates sort correctly)
                parts.append(f"({txt} <> '' AND {txt} {sym} ?)"); params.append(v)
        elif op == "between":
            v2 = str(f.get("value2", "") or "")
            a, b = _num(v), _num(v2)
            if a is not None and b is not None:
                parts.append(f"({isnum} AND {raw} BETWEEN ? AND ?)"); params += [min(a, b), max(a, b)]
            else:
                parts.append(f"({txt} <> '' AND {txt} BETWEEN ? AND ?)"); params += sorted([v, v2])
        elif op == "regex":
            try:
                re.compile(v)
            except re.error as e:
                raise ValueError(f"Invalid regular expression: {e}") from None
            parts.append(f"{txt} REGEXP ?"); params.append(v)
        elif op in ("in", "not_in"):
            vals = [str(x) for x in (f.get("values") or [])]
            if not vals:
                parts.append("0" if op == "in" else "1")
            else:
                parts.append(f"{txt} {'NOT ' if op == 'not_in' else ''}IN ({','.join('?' * len(vals))})"); params += vals
    return (" AND ".join(parts), params)


def _base_where(cfg: CrawlConfig, key: str, q: str, filters: list[dict] | None, skip_field: str = "") -> tuple[list[str], str, list[Any]]:
    tab = key.split(":")[0]
    cols = COLUMNS.get(tab, COLUMNS["internal"])
    where = "u.id IN (SELECT url_id FROM url_issues WHERE key=?)"
    params: list[Any] = [key]
    if q:
        where += " AND (u.url LIKE ? OR u.title LIKE ? OR u.h1 LIKE ? OR u.meta_desc LIKE ?)"
        params += [f"%{q}%"] * 4
    fsql, fparams = column_filter_sql(cols, filters, skip_field)
    if fsql:
        where += " AND " + fsql
        params += fparams
    return cols, where, params


def column_values(con: sqlite3.Connection, cfg: CrawlConfig, key: str, field: str, q: str = "", filters: list[dict] | None = None,
                  limit: int = 1000) -> dict[str, Any]:
    """Distinct values (as shown in the grid) of one column with counts, for the "Filter by values" list. Other columns'
    filters apply; this column's own filter does not, as in Excel."""
    cols, where, params = _base_where(cfg, key, q, filters, skip_field=field)
    if field not in cols:
        raise ValueError(f"Unknown column: {field}")
    txt = f"coalesce(CAST({_DISPLAY_SQL.get(field, 'u.' + field)} AS TEXT),'')"
    rows = con.execute(f"SELECT {txt} AS v, count(*) FROM urls u WHERE {where} GROUP BY v ORDER BY count(*) DESC, v LIMIT ?",
                       params + [limit + 1]).fetchall()
    return {"values": [{"value": r[0], "count": r[1]} for r in rows[:limit]], "truncated": len(rows) > limit}


def rows_for(con: sqlite3.Connection, cfg: CrawlConfig, key: str, q: str = "", sort: str = "", direction: str = "asc",
             offset: int = 0, limit: int | None = None, filters: list[dict] | None = None) -> tuple[list[str], list[list[Any]], int]:
    cols, where, params = _base_where(cfg, key, q, filters)
    total = con.execute(f"SELECT count(*) FROM urls u WHERE {where}", params).fetchone()[0]
    order = f"u.{sort}" if sort in cols or sort == "id" else "u.id"
    dirn = "DESC" if direction.lower() == "desc" else "ASC"
    sql = f"SELECT u.id, {', '.join('u.' + c for c in cols)} FROM urls u WHERE {where} ORDER BY {order} {dirn}, u.id"
    if limit:
        sql += f" LIMIT {int(limit)} OFFSET {int(offset)}"
    rows = [[r[0]] + [_fmt(c, r[i + 1]) for i, c in enumerate(cols)] for r in con.execute(sql, params)]
    return cols, rows, total


def _no_formula(v: Any, starts: tuple[str, ...] = ("=", "+", "-", "@", "\t", "\r")) -> Any:
    """Crawled text that a spreadsheet would run as a formula gets a leading apostrophe."""
    return "'" + v if isinstance(v, str) and v.startswith(starts) else v


def to_csv(headers: list[str], rows: Iterable[Iterable[Any]]) -> bytes:
    buf = io.StringIO()
    w = csv.writer(buf, quoting=csv.QUOTE_ALL)
    w.writerow(headers)
    for r in rows:
        w.writerow(["" if v is None else _no_formula(v) for v in r])
    return ("﻿" + buf.getvalue()).encode("utf-8")


def to_xlsx(sheets: list[tuple[str, list[str], Iterable[Iterable[Any]]]]) -> bytes:
    wb = Workbook()
    wb.remove(wb.active)
    for name, headers, rows in sheets:
        ws = wb.create_sheet(title=name[:31].replace("/", "-").replace(":", "-") or "Sheet")
        ws.append(headers)
        for r in rows:
            ws.append(["" if v is None else (v if isinstance(v, (int, float)) else _no_formula(str(v)[:32000], ("=",))) for v in r])
        for i, h in enumerate(headers, 1):
            ws.column_dimensions[get_column_letter(i)].width = min(60, max(12, len(str(h)) + 2))
        ws.freeze_panes = "A2"
    out = io.BytesIO()
    wb.save(out)
    return out.getvalue()


def export_filter(con: sqlite3.Connection, cfg: CrawlConfig, key: str, fmt: str = "csv", q: str = "",
                  filters: list[dict] | None = None) -> tuple[str, bytes]:
    cols, rows, _ = rows_for(con, cfg, key, q=q, filters=filters)
    headers = [COL_LABELS.get(c, c) for c in cols]
    body = [r[1:] for r in rows]
    name = key.replace(":", "_") + ("_filtered" if q or filters else "")
    if fmt == "xlsx":
        return f"{name}.xlsx", to_xlsx([(name, headers, body)])
    return f"{name}.csv", to_csv(headers, body)


# ----------------------------------------------------------------------------- reports
LINK_HEADERS = ["Type", "Source", "Destination", "Size (bytes)", "Alt Text", "Anchor", "Status Code", "Status", "Follow", "Target", "Rel", "Path Type", "Link Position", "Link Path"]
LINK_SQL = """
SELECT l.kind, f.url, t.url, t.size, l.alt, l.anchor, t.status_code, t.status, l.follow, l.target, l.rel,
       CASE WHEN l.is_internal=1 THEN 'Absolute' ELSE 'Absolute' END, l.position, l.path
FROM links l JOIN urls f ON f.id=l.from_id JOIN urls t ON t.id=l.to_id
"""
KIND_NAMES = {"hyperlink": "Hyperlink", "image": "Image", "css": "CSS", "js": "JavaScript", "canonical": "Canonical", "hreflang": "HREFLANG",
              "pagination": "Pagination", "meta_refresh": "Meta Refresh", "iframe": "IFrame", "form": "Form", "amp": "AMP", "mobile_alt": "Mobile Alternate",
              "media": "Media", "redirect": "Redirect", "other": "Other"}


def _link_rows(con: sqlite3.Connection, where: str = "", params: tuple = ()) -> list[list[Any]]:
    rows = []
    for r in con.execute(LINK_SQL + (f" WHERE {where}" if where else "") + " ORDER BY f.id, l.id", params):
        r = list(r)
        r[0] = KIND_NAMES.get(r[0], r[0])
        r[8] = "True" if r[8] else "False"
        rows.append(r)
    return rows


def report(con: sqlite3.Connection, cfg: CrawlConfig, name: str) -> list[tuple[str, list[str], list[list[Any]]]]:
    """Return sheets [(sheet name, headers, rows)] for a named report."""
    if name == "crawl_overview":
        return [("Crawl Overview", ["Section", "Name", "URLs", "% of Total", "Total URLs"], crawl_overview_rows(con, cfg))]
    if name == "issues_overview":
        return [("Issues", ["Issue Name", "Issue Type", "Issue Priority", "URLs", "% of Total", "Description", "How To Fix"], issues_overview_rows(con, cfg))]
    if name == "all_inlinks":
        return [("All Inlinks", LINK_HEADERS, _link_rows(con, "t.is_internal=1"))]
    if name == "all_outlinks":
        return [("All Outlinks", LINK_HEADERS, _link_rows(con))]
    if name == "external_links":
        return [("External Links", LINK_HEADERS, _link_rows(con, "t.is_internal=0 AND l.kind='hyperlink'"))]
    if name == "all_anchor_text":
        rows = [list(r) for r in con.execute(
            "SELECT f.url, t.url, l.anchor, l.alt, l.follow FROM links l JOIN urls f ON f.id=l.from_id JOIN urls t ON t.id=l.to_id WHERE l.kind='hyperlink' AND t.is_internal=1 ORDER BY t.url")]
        return [("Anchor Text", ["Source", "Destination", "Anchor", "Alt Text", "Follow"], rows)]
    if name == "structured_data":
        rows = []
        for r in con.execute("SELECT u.url, s.syntax, s.type, s.errors, s.warnings FROM structured_data s JOIN urls u ON u.id=s.url_id ORDER BY u.url, s.id"):
            errs, warns = json.loads(r[3] or "[]"), json.loads(r[4] or "[]")
            rows.append([r[0], r[1], r[2], len(errs), len(warns), " | ".join(errs), " | ".join(warns)])
        return [("Structured Data", ["URL", "Syntax", "Type", "Errors", "Warnings", "Error Messages", "Warning Messages"], rows)]
    if name == "spelling":
        from .spelling import summary_rows
        return [("Spelling & Grammar", ["Kind", "Text", "Suggestion", "Pages", "Example URL"], summary_rows(con))]
    if name == "redirect_chains":
        return [("Redirect Chains", ["Address", "Status Code", "Number of Redirects", "Redirect Loop", "Final Address", "Final Status Code", "Chain"], redirect_chain_rows(con))]
    if name == "redirects_to_errors":
        rows = [list(r) for r in con.execute(
            "SELECT url, status_code, redirect_url, final_url, final_status FROM urls WHERE redirect_url<>'' AND (final_status IS NULL OR final_status>=400) ORDER BY url")]
        return [("Redirects To Errors", ["Address", "Status Code", "Redirect URL", "Final Address", "Final Status Code"], rows)]
    if name == "canonical_chains":
        rows = canonical_chain_rows(con)
        return [("Canonical Chains", ["Address", "Canonical", "Canonical of Canonical", "Chain Length"], rows)]
    if name == "orphan_pages":
        rows = [list(r) for r in con.execute("SELECT url, status_code, indexability_status, sitemap_count FROM urls WHERE in_sitemap=1 AND unique_inlinks=0 AND type='HTML' AND crawled=1 ORDER BY url")]
        return [("Orphan Pages", ["Address", "Status Code", "Indexability Status", "Sitemaps"], rows)]
    if name == "insecure_content":
        rows = [list(r) for r in con.execute(
            "SELECT f.url, t.url, l.kind FROM links l JOIN urls f ON f.id=l.from_id JOIN urls t ON t.id=l.to_id WHERE f.url LIKE 'https://%' AND t.url LIKE 'http://%' AND l.kind IN ('image','css','js','iframe','media','form') ORDER BY f.url")]
        return [("Insecure Content", ["Source", "Insecure URL", "Type"], rows)]
    if name == "http_headers":
        rows = []
        for r in con.execute("SELECT url, headers FROM urls WHERE crawled=1 AND headers IS NOT NULL"):
            try:
                for k, v in json.loads(r["headers"]).items():
                    rows.append([r["url"], k, v])
            except json.JSONDecodeError:
                continue
        return [("HTTP Headers", ["Address", "Header", "Value"], rows)]
    if name == "cookies":
        rows = []
        for r in con.execute("SELECT url, headers FROM urls WHERE crawled=1 AND headers LIKE '%set-cookie%'"):
            try:
                for c in json.loads(r["headers"]).get("set-cookie", "").split("\n"):
                    if c.strip():
                        rows.append([r["url"], c.split("=", 1)[0].strip(), c])
            except json.JSONDecodeError:
                continue
        return [("Cookies", ["Address", "Cookie Name", "Set-Cookie"], rows)]
    if name == "serp_summary":
        rows = [list(r) for r in con.execute("SELECT url, title, title_len, title_px, meta_desc, md_len, md_px FROM urls WHERE crawled=1 AND type='HTML' AND is_internal=1 ORDER BY url")]
        return [("SERP Summary", ["Address", "Title", "Title Length", "Title Pixel Width", "Meta Description", "Description Length", "Description Pixel Width"], rows)]
    if name == "sitemaps":
        rows = [list(r) for r in con.execute("SELECT url, status_code, is_index, url_count, size, parent FROM sitemaps ORDER BY url")]
        return [("Sitemaps", ["Sitemap", "Status Code", "Is Index", "URL Count", "Size (bytes)", "Parent"], rows)]
    if name == "uncrawlable":
        rows = [list(r) for r in con.execute("SELECT f.url, u.href, u.anchor, u.reason FROM uncrawlable u JOIN urls f ON f.id=u.from_id ORDER BY f.url")]
        return [("Uncrawlable Links", ["Source", "Href", "Anchor", "Reason"], rows)]
    raise KeyError(name)


REPORT_NAMES = [
    ("crawl_overview", "Crawl Overview"), ("issues_overview", "Issues Overview"), ("all_inlinks", "All Inlinks"), ("all_outlinks", "All Outlinks"),
    ("external_links", "External Links"), ("all_anchor_text", "All Anchor Text"), ("redirect_chains", "Redirect Chains"),
    ("redirects_to_errors", "Redirects To Errors"), ("canonical_chains", "Canonical Chains"), ("orphan_pages", "Orphan Pages"),
    ("insecure_content", "Insecure Content"), ("serp_summary", "SERP Summary"), ("http_headers", "HTTP Header Summary"),
    ("cookies", "Cookie Summary"), ("sitemaps", "XML Sitemaps"), ("uncrawlable", "Uncrawlable Links"), ("spelling", "Spelling & Grammar Summary"), ("structured_data", "Structured Data"),
]


def crawl_overview_rows(con: sqlite3.Connection, cfg: CrawlConfig) -> list[list[Any]]:
    c = counts(con, cfg)
    total = con.execute("SELECT count(*) FROM urls WHERE crawled IN (1,3)").fetchone()[0]
    internal = c.get("internal:all", 0)
    external = c.get("external:all", 0)
    rows: list[list[Any]] = [
        ["Summary", "Total URLs Encountered", total, "100.00%", total],
        ["Summary", "Total Internal URLs", internal, _pct(internal, total), total],
        ["Summary", "Total External URLs", external, _pct(external, total), total],
        ["Summary", "Total Internal Indexable URLs", con.execute("SELECT count(*) FROM urls WHERE crawled=1 AND is_internal=1 AND indexable=1").fetchone()[0], "", total],
        ["Summary", "Total Internal Non-Indexable URLs", con.execute("SELECT count(*) FROM urls WHERE crawled IN (1,3) AND is_internal=1 AND indexable=0").fetchone()[0], "", total],
    ]
    tab_totals = {"internal": internal, "external": external}
    for tab_key, tab_name in TABS:
        base = tab_totals.get(tab_key, internal if tab_key not in ("images",) else c.get("internal:images", 0) + c.get("external:images", 0))
        for f in all_filters(cfg):
            if f["tab"] != tab_key or f["issue_only"]:
                continue
            n = c.get(f["key"], 0)
            rows.append([tab_name, f["name"], n, _pct(n, base), base])
    return rows


def issues_overview_rows(con: sqlite3.Connection, cfg: CrawlConfig) -> list[list[Any]]:
    c = counts(con, cfg)
    internal = c.get("internal:all", 0) or 1
    rows = []
    names = dict(TABS)
    for f in all_filters(cfg):
        if not f["kind"]:
            continue
        n = c.get(f["key"], 0)
        if not n:
            continue
        rows.append([f"{names.get(f['tab'], f['tab'])}: {f['name']}", f["kind"].capitalize(), f["priority"], n, _pct(n, internal), f["desc"], f["fix"]])
    order = {"High": 0, "Medium": 1, "Low": 2}
    rows.sort(key=lambda r: (order.get(r[2], 3), -r[3]))
    return rows


def redirect_chain_rows(con: sqlite3.Connection) -> list[list[Any]]:
    redirects = {r["url"]: (r["status_code"], r["redirect_url"]) for r in con.execute("SELECT url, status_code, redirect_url FROM urls WHERE redirect_url<>''")}
    rows = []
    for r in con.execute("SELECT url, status_code, redirect_chain_len, redirect_loop, final_url, final_status FROM urls WHERE redirect_url<>'' AND (redirect_chain_len>=2 OR redirect_loop=1) ORDER BY url"):
        chain, cur, seen = [r["url"]], r["url"], set()
        while cur in redirects and cur not in seen and len(chain) < 30:
            seen.add(cur)
            cur = redirects[cur][1]
            chain.append(cur)
        rows.append([r["url"], r["status_code"], r["redirect_chain_len"], "True" if r["redirect_loop"] else "False", r["final_url"], r["final_status"], " -> ".join(chain)])
    return rows


def canonical_chain_rows(con: sqlite3.Connection) -> list[list[Any]]:
    canon = {r["url"]: r["canonical"] for r in con.execute("SELECT url, canonical FROM urls WHERE canonical<>'' AND canonical<>url")}
    rows = []
    for url, c1 in canon.items():
        c2 = canon.get(c1)
        if c2 and c2 != c1:
            rows.append([url, c1, c2, 2])
    return rows


def _pct(n: int, total: int) -> str:
    return f"{(100.0 * n / total):.2f}%" if total else "0.00%"


# ----------------------------------------------------------------------------- export centre (every combination)
import re as _re
import zipfile

BUNDLE_PARTS = [
    ("overview", "Crawl overview", "Totals per tab and filter, like Screaming Frog's crawl_overview.csv."),
    ("issues", "Issues overview", "Every triggered issue: type, priority, URL count, what it means, how to fix."),
    ("issue_urls", "Issues → URLs (one sheet per issue)", "The affected URLs for every triggered issue, with that tab's columns."),
    ("issue_inlinks", "Issues → inlinks (one sheet per issue)", "Every link pointing at an affected URL: source page, anchor text, position. Hand this to a dev to fix links."),
    ("matrix", "URL × issues matrix", "One row per crawled URL with every issue it triggers in one cell — the audit spreadsheet."),
    ("tabs", "All tabs (All filter)", "Internal, External, Security … one sheet per tab with its full column set."),
    ("filters", "Every non-empty filter", "One sheet per filter that has rows, issues and plain filters alike."),
    ("links", "Links", "All inlinks, all outlinks, external links, anchor text."),
    ("reports", "All reports", "Redirect chains, canonical chains, orphan pages, insecure content, SERP summary, headers, cookies, sitemaps…"),
    ("fixes", "AI fixes", "Every proposal with status, target and push result (if any)."),
]


def _safe(name: str, limit: int = 31) -> str:
    s = _re.sub(r"[\[\]\*\?/\\:]", "-", name).strip()
    return s[:limit] or "Sheet"


def _issue_inlink_rows(con: sqlite3.Connection, key: str) -> list[list[Any]]:
    return _link_rows(con, "l.to_id IN (SELECT url_id FROM url_issues WHERE key=?) AND l.kind='hyperlink'", (key,))


def matrix_rows(con: sqlite3.Connection, cfg: CrawlConfig) -> tuple[list[str], list[list[Any]]]:
    fk = filters_by_key_cached(cfg)
    names = dict(TABS)
    per_url: dict[int, list[str]] = {}
    for url_id, key in con.execute("SELECT url_id, key FROM url_issues"):
        f = fk.get(key)
        if f and f["kind"] and not f["issue_only"]:
            per_url.setdefault(url_id, []).append(f"{names.get(f['tab'], '')}: {f['name']}")
    rows = []
    for r in con.execute("SELECT id, url, status_code, indexable, type, title, inlinks, depth FROM urls WHERE crawled IN (1,3) ORDER BY id"):
        issues = sorted(set(per_url.get(r["id"], [])))
        rows.append([r["url"], r["status_code"], "Indexable" if r["indexable"] == 1 else "Non-Indexable" if r["indexable"] == 0 else "", r["type"], r["title"], r["inlinks"], r["depth"], len(issues), " | ".join(issues)])
    return ["Address", "Status Code", "Indexability", "Type", "Title 1", "Inlinks", "Crawl Depth", "Issue Count", "Issues"], rows


_fk_cache: dict[int, dict[str, dict[str, Any]]] = {}


def filters_by_key_cached(cfg: CrawlConfig) -> dict[str, dict[str, Any]]:
    from .issues import filters_by_key
    return filters_by_key(cfg)


def build_bundle(con: sqlite3.Connection, cfg: CrawlConfig, parts: list[str], issues_only_filters: bool = True) -> list[tuple[str, str, list[str], list[list[Any]]]]:
    """Return [(folder, sheet, headers, rows)] for the chosen parts."""
    out: list[tuple[str, str, list[str], list[list[Any]]]] = []
    c = counts(con, cfg)
    names = dict(TABS)
    fl = all_filters(cfg)
    if "overview" in parts:
        out.append(("", "Crawl Overview", ["Section", "Name", "URLs", "% of Total", "Total URLs"], crawl_overview_rows(con, cfg)))
    if "issues" in parts:
        out.append(("", "Issues Overview", ["Issue Name", "Issue Type", "Issue Priority", "URLs", "% of Total", "Description", "How To Fix"], issues_overview_rows(con, cfg)))
    if "matrix" in parts:
        h, rows = matrix_rows(con, cfg)
        out.append(("", "URL x Issues", h, rows))
    if "issue_urls" in parts or "issue_inlinks" in parts:
        for f in fl:
            if not f["kind"] or f["issue_only"] or not c.get(f["key"], 0):
                continue
            label = f"{names.get(f['tab'], f['tab'])} - {f['name']}"
            if "issue_urls" in parts:
                cols, rows, _ = rows_for(con, cfg, f["key"])
                out.append(("issues", label, [COL_LABELS.get(x, x) for x in cols], [r[1:] for r in rows]))
            if "issue_inlinks" in parts:
                rows = _issue_inlink_rows(con, f["key"])
                if rows:
                    out.append(("issue-inlinks", label + " (inlinks)", LINK_HEADERS, rows))
    if "tabs" in parts:
        for tab_key, tab_name in TABS:
            key = f"{tab_key}:all"
            if not c.get(key, 0):
                continue
            cols, rows, _ = rows_for(con, cfg, key)
            out.append(("tabs", tab_name, [COL_LABELS.get(x, x) for x in cols], [r[1:] for r in rows]))
    if "filters" in parts:
        for f in fl:
            if f["issue_only"] or f["short"] == "all" or not c.get(f["key"], 0):
                continue
            if issues_only_filters and not f["kind"]:
                continue
            cols, rows, _ = rows_for(con, cfg, f["key"])
            out.append(("filters", f"{names.get(f['tab'], f['tab'])} - {f['name']}", [COL_LABELS.get(x, x) for x in cols], [r[1:] for r in rows]))
    if "links" in parts:
        for name in ("all_inlinks", "all_outlinks", "external_links", "all_anchor_text"):
            for sheet, h, rows in report(con, cfg, name):
                out.append(("links", sheet, h, rows))
    if "reports" in parts:
        for name, _label in REPORT_NAMES:
            if name in ("crawl_overview", "issues_overview", "all_inlinks", "all_outlinks", "external_links", "all_anchor_text"):
                continue
            for sheet, h, rows in report(con, cfg, name):
                if rows:
                    out.append(("reports", sheet, h, rows))
    if "fixes" in parts:
        rows = [list(r) for r in con.execute("SELECT url, kind, field, issue_name, current, proposed, rationale, status, target, result, created_at, pushed_at FROM fixes ORDER BY id")]
        if rows:
            out.append(("", "AI Fixes", ["URL", "Kind", "Field", "Issue", "Current", "Proposed", "Rationale", "Status", "Target", "Result", "Created", "Pushed"], rows))
    return out


def bundle_to_zip(sheets: list[tuple[str, str, list[str], list[list[Any]]]], readme: str = "") -> bytes:
    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w", zipfile.ZIP_DEFLATED) as z:
        seen: set[str] = set()
        for folder, sheet, headers, rows in sheets:
            base = _safe(sheet, 80).replace(" ", "_")
            path = f"{folder}/{base}.csv" if folder else f"{base}.csv"
            n = 2
            while path in seen:
                path = f"{folder}/{base}_{n}.csv" if folder else f"{base}_{n}.csv"
                n += 1
            seen.add(path)
            z.writestr(path, to_csv(headers, rows))
        if readme:
            z.writestr("README.txt", readme)
    return buf.getvalue()


def bundle_to_xlsx(sheets: list[tuple[str, str, list[str], list[list[Any]]]]) -> bytes:
    used: set[str] = set()
    named = []
    for folder, sheet, headers, rows in sheets:
        name = _safe(sheet)
        n = 2
        while name in used:
            suffix = f" ({n})"
            name = _safe(sheet, 31 - len(suffix)) + suffix
            n += 1
        used.add(name)
        named.append((name, headers, rows))
    # a contents sheet first so a 150-sheet workbook is navigable
    toc = [[name, folder or "-", len(rows)] for (name, headers, rows), (folder, *_rest) in zip(named, sheets)]
    return to_xlsx([("Contents", ["Sheet", "Group", "Rows"], toc)] + named)


def issues_markdown(con: sqlite3.Connection, cfg: CrawlConfig, start_url: str = "") -> str:
    rows = issues_overview_rows(con, cfg)
    total = con.execute("SELECT count(*) FROM urls WHERE crawled IN (1,3)").fetchone()[0]
    lines = [f"# SEO issues — {start_url or 'crawl'}", "", f"{total:,} URLs crawled · {len(rows)} issue types triggered", "",
             "| Issue | Type | Priority | URLs | How to fix |", "|---|---|---|---:|---|"]
    for name, kind, prio, n, _pct_, desc, fix in rows:
        lines.append(f"| {name} | {kind} | {prio} | {n:,} | {fix} |")
    return "\n".join(lines) + "\n"
