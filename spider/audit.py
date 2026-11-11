"""Site Audit overview (the Semrush / Ahrefs style dashboard): one Site Health score, errors / warnings / notices,
thematic scores (Crawlability, HTTPS, Core Web Vitals, Performance, Internal linking, On-page, Markup, International,
Accessibility), crawled-pages breakdown, top issues, and the trend across crawls of the same site.

Health: start at 100 and subtract, per triggered issue type, weight(kind) × weight(priority) × share of pages affected
× 25. An error on every page costs 25 points; a low-priority notice on every page costs 1.5. The same formula on a
theme's own issues gives the thematic score. The score is explainable: the dashboard lists what cost the most."""
from __future__ import annotations

import json
import sqlite3
from pathlib import Path
from typing import Any

from . import db as dbm
from .config import CrawlConfig
from .issues import TABS, all_filters, counts

KIND_W = {"issue": 1.0, "warning": 0.5, "opportunity": 0.2}
PRIO_W = {"High": 1.0, "Medium": 0.6, "Low": 0.3}
SCALE = 25.0
THEMES: list[tuple[str, str, str]] = [
    ("crawlability", "Crawlability", "Status codes, redirects, canonicals, directives, URLs and sitemaps: can search engines reach and index the right pages?"),
    ("https", "HTTPS & security", "Mixed content, insecure forms, security headers, protocol-relative resources."),
    ("cwv", "Core Web Vitals", "LCP, CLS and INP / TBT from Lighthouse (lab) and CrUX (field) on the audited pages."),
    ("performance", "Site performance", "Lighthouse opportunities, heavy images, slow responses, large pages."),
    ("linking", "Internal linking", "Orphans, crawl depth, broken and redirected internal links, anchor text."),
    ("onpage", "On-page content", "Titles, meta descriptions, headings, thin and duplicate content, alt text, spelling."),
    ("markup", "Markup & structured data", "HTML validation, structured data, AMP."),
    ("international", "International SEO", "hreflang reciprocity, codes and canonicals."),
    ("accessibility", "Accessibility", "axe-core checks from Lighthouse."),
]
TAB_THEME = {
    "response_codes": "crawlability", "url": "crawlability", "canonicals": "crawlability", "directives": "crawlability", "sitemaps": "crawlability", "pagination": "crawlability",
    "search_console": "crawlability", "javascript": "crawlability",
    "security": "https",
    "pagespeed": "performance", "images": "performance",
    "links": "linking",
    "page_titles": "onpage", "meta_description": "onpage", "meta_keywords": "onpage", "h1": "onpage", "h2": "onpage", "content": "onpage",
    "validation": "markup", "structured_data": "markup", "amp": "markup",
    "hreflang": "international",
    "accessibility": "accessibility",
    "analytics": "onpage", "link_metrics": "linking",
}
CWV_KEYS = {"pagespeed:lcp_slow", "pagespeed:cls_poor", "pagespeed:tbt_high", "pagespeed:field_lcp_poor", "pagespeed:field_inp_poor", "pagespeed:perf_poor", "pagespeed:perf_needs_work"}
LINKING_KEYS = {"response_codes:int_4xx", "response_codes:int_5xx", "response_codes:int_redirect_chain", "response_codes:int_3xx", "response_codes:int_redirect_loop", "response_codes:int_no_response"}


def theme_of(key: str, tab: str) -> str:
    if key in CWV_KEYS:
        return "cwv"
    if key in LINKING_KEYS or key.startswith("images:missing_alt") or key.startswith("images:alt_"):
        return "linking" if key in LINKING_KEYS else "onpage"
    return TAB_THEME.get(tab, "crawlability")


def _score(issues: list[dict[str, Any]], total: int) -> tuple[float, list[dict[str, Any]]]:
    pen = []
    for i in issues:
        share = min(1.0, i["count"] / max(1, total)) if i.get("per_page", True) else 1.0
        p = KIND_W.get(i["kind"], 0.3) * PRIO_W.get(i["priority"], 0.5) * share * SCALE
        pen.append({**i, "penalty": round(p, 2)})
    pen.sort(key=lambda x: -x["penalty"])
    return round(max(0.0, 100.0 - sum(x["penalty"] for x in pen)), 1), pen


def site_audit(con: sqlite3.Connection, cfg: CrawlConfig) -> dict[str, Any]:
    c = counts(con, cfg)
    names = dict(TABS)
    issues = []
    for f in all_filters(cfg):
        if not f["kind"] or not c.get(f["key"]):
            continue
        issues.append({"key": f["key"], "tab": f["tab"], "tab_name": names.get(f["tab"], f["tab"]), "name": f["name"], "kind": f["kind"], "priority": f["priority"],
                       "count": c[f["key"]], "desc": f["desc"], "fix": f["fix"], "theme": theme_of(f["key"], f["tab"])})
    q = lambda sql, *p: con.execute(sql, p).fetchone()[0]  # noqa: E731
    pages = q("SELECT count(*) FROM urls WHERE crawled=1 AND is_internal=1 AND type='HTML'")
    pages_200 = q("SELECT count(*) FROM urls WHERE crawled=1 AND is_internal=1 AND type='HTML' AND status_code BETWEEN 200 AND 299")
    total = q("SELECT count(*) FROM urls WHERE crawled IN (1,3)")
    health, penalties = _score(issues, max(1, pages_200))
    # errors / warnings / notices: issue types and affected page-issue pairs
    tally = {k: {"types": 0, "instances": 0} for k in ("issue", "warning", "opportunity")}
    for i in issues:
        tally[i["kind"]]["types"] += 1
        tally[i["kind"]]["instances"] += i["count"]
    # crawled pages breakdown (Semrush "Crawled pages" donut)
    broken = q("SELECT count(*) FROM urls WHERE crawled=1 AND is_internal=1 AND type='HTML' AND (status_code>=400 OR status_code IS NULL)")
    redirects = q("SELECT count(*) FROM urls WHERE crawled=1 AND is_internal=1 AND type='HTML' AND status_code BETWEEN 300 AND 399")
    blocked = q("SELECT count(*) FROM urls WHERE crawled=3 AND is_internal=1")
    issue_keys = [i["key"] for i in issues if i["kind"] == "issue" and i["tab"] not in ("search_console", "analytics", "link_metrics")]
    with_issues = q(f"SELECT count(DISTINCT url_id) FROM url_issues ui JOIN urls u ON u.id=ui.url_id WHERE u.crawled=1 AND u.is_internal=1 AND u.type='HTML' AND u.status_code BETWEEN 200 AND 299 AND ui.key IN ({','.join('?' * len(issue_keys))})", *issue_keys) if issue_keys else 0
    healthy = max(0, pages_200 - with_issues)
    # themes
    themes = []
    for tid, label, desc in THEMES:
        ti = [i for i in issues if i["theme"] == tid]
        has_data = True
        if tid in ("cwv", "performance", "accessibility"):
            has_data = q("SELECT count(*) FROM urls WHERE lh_at IS NOT NULL") > 0 or any(i["tab"] == "images" for i in ti)
        if tid == "international":
            has_data = q("SELECT count(*) FROM urls WHERE hreflang_count>0") > 0 or bool(ti)
        sc, pen = _score(ti, max(1, pages_200))
        themes.append({"id": tid, "name": label, "desc": desc, "score": sc if has_data else None, "issues": len(ti), "errors": sum(1 for i in ti if i["kind"] == "issue"),
                       "top": [{k: x[k] for k in ("key", "name", "kind", "priority", "count", "penalty", "tab_name")} for x in pen[:3]], "has_data": has_data})
    # Core Web Vitals summary from Lighthouse
    lh_n = q("SELECT count(*) FROM urls WHERE lh_at IS NOT NULL")
    cwv = None
    if lh_n:
        cwv = {"audited": lh_n,
               "lcp_good": q("SELECT count(*) FROM urls WHERE lh_at IS NOT NULL AND lh_lcp <= 2500"), "cls_good": q("SELECT count(*) FROM urls WHERE lh_at IS NOT NULL AND lh_cls <= 0.1"),
               "tbt_good": q("SELECT count(*) FROM urls WHERE lh_at IS NOT NULL AND lh_tbt <= 200"), "perf_avg": q("SELECT round(avg(lh_perf)) FROM urls WHERE lh_perf IS NOT NULL"),
               "a11y_avg": q("SELECT round(avg(lh_a11y)) FROM urls WHERE lh_a11y IS NOT NULL"), "field": q("SELECT count(*) FROM urls WHERE lh_field_lcp IS NOT NULL")}
    meta = {r["key"]: dbm.get_meta(con, r["key"]) for r in con.execute("SELECT key FROM meta WHERE key IN ('start_url','started_at','finished_at','state','config')")}
    out = {
        "site": meta.get("start_url", cfg.start_url), "started_at": meta.get("started_at", ""), "finished_at": meta.get("finished_at", ""), "state": meta.get("state", ""),
        "health": health, "grade": "good" if health >= 80 else "fair" if health >= 60 else "poor",
        "tally": tally, "pages": {"html": pages, "ok": pages_200, "healthy": healthy, "with_issues": with_issues, "broken": broken, "redirects": redirects, "blocked": blocked, "total_urls": total},
        "themes": themes, "cwv": cwv, "top_issues": penalties[:15], "issue_count": len(issues),
        "indexable": q("SELECT count(*) FROM urls WHERE crawled=1 AND is_internal=1 AND type='HTML' AND indexable=1"),
        "non_indexable": q("SELECT count(*) FROM urls WHERE crawled=1 AND is_internal=1 AND type='HTML' AND indexable=0"),
    }
    # cache a compact summary in the crawl file for the trend
    try:
        dbm.set_meta(con, "audit_summary", {"health": health, "errors": tally["issue"]["instances"], "warnings": tally["warning"]["instances"], "notices": tally["opportunity"]["instances"],
                                            "error_types": tally["issue"]["types"], "pages": pages, "at": meta.get("finished_at") or meta.get("started_at", ""),
                                            "themes": {t["id"]: t["score"] for t in themes}})
        con.commit()
    except sqlite3.Error:
        pass
    return out


def history(crawls_dir: Path, current: Path | None, cfg: CrawlConfig, limit: int = 20) -> list[dict[str, Any]]:
    """Health trend: one point per completed crawl of the same site (summaries cached in each crawl file)."""
    from .compare import host_of
    host = ""
    if current is not None and current.exists():
        try:
            c = dbm.connect(current, readonly=True)
            host = host_of(current, {"start_url": dbm.get_meta(c, "start_url", "")})
            c.close()
        except sqlite3.Error:
            host = host_of(current)
    points = []
    for p in sorted(crawls_dir.glob("*.db"), key=lambda x: x.stat().st_mtime):
        try:
            c = dbm.connect(p, readonly=True)
            meta = {"start_url": dbm.get_meta(c, "start_url", ""), "state": dbm.get_meta(c, "state", ""), "started_at": dbm.get_meta(c, "started_at", "")}
            summary = dbm.get_meta(c, "audit_summary", None)
            c.close()
        except sqlite3.Error:
            continue
        if host and host_of(p, meta) != host:
            continue
        if meta["state"] not in ("finished", "stopped"):
            continue
        if summary is None:
            try:
                w = dbm.connect(p)
                try:
                    site_audit(w, CrawlConfig.from_dict({**cfg.to_dict(), **(dbm.get_meta(w, "config", {}) or {})}))
                    summary = dbm.get_meta(w, "audit_summary", None)
                finally:
                    w.close()
            except sqlite3.Error:
                continue
        if summary:
            points.append({"crawl": p.name, "at": meta["started_at"], **summary, "current": current is not None and p.resolve() == current.resolve()})
    return points[-limit:]


def report_markdown(a: dict[str, Any]) -> str:
    lines = [f"# Site audit — {a['site']}", "", f"**Site health: {a['health']} / 100** ({a['grade']}) · {a['pages']['html']:,} HTML pages crawled · {a['indexable']:,} indexable",
             "", f"- Errors: {a['tally']['issue']['instances']:,} on {a['tally']['issue']['types']} issue types", f"- Warnings: {a['tally']['warning']['instances']:,} on {a['tally']['warning']['types']} types",
             f"- Notices: {a['tally']['opportunity']['instances']:,} on {a['tally']['opportunity']['types']} types", "",
             f"Pages: {a['pages']['healthy']:,} healthy · {a['pages']['with_issues']:,} with errors · {a['pages']['broken']:,} broken · {a['pages']['redirects']:,} redirects · {a['pages']['blocked']:,} blocked", "",
             "## Thematic scores", "", "| Theme | Score | Issues | Biggest cost |", "|---|---:|---:|---|"]
    for t in a["themes"]:
        top = t["top"][0]["name"] + f" ({t['top'][0]['count']:,} pages)" if t["top"] else "—"
        lines.append(f"| {t['name']} | {t['score'] if t['score'] is not None else 'n/a'} | {t['issues']} | {top} |")
    lines += ["", "## Top issues by impact on the score", "", "| Issue | Type | Priority | Pages | Cost | Fix |", "|---|---|---|---:|---:|---|"]
    for i in a["top_issues"]:
        lines.append(f"| {i['tab_name']}: {i['name']} | {i['kind']} | {i['priority']} | {i['count']:,} | −{i['penalty']} | {i['fix']} |")
    if a.get("cwv"):
        c = a["cwv"]
        lines += ["", "## Core Web Vitals (Lighthouse)", "", f"{c['audited']} pages audited · avg performance {c['perf_avg']} · LCP good on {c['lcp_good']} · CLS good on {c['cls_good']} · TBT good on {c['tbt_good']}"]
    return "\n".join(lines) + "\n"
