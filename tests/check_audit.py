"""Site Audit dashboard check: scoring maths, module on the real crawl, API, report, trend, launcher CORS, modal headless.

    .venv\\Scripts\\python.exe tests\\check_audit.py
"""
from __future__ import annotations

import json
import os
import sys
import threading
import time
import urllib.request
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
from fixture import fixture_crawl, fixture_root  # noqa: E402,F401
import uvicorn  # noqa: E402

from spider import db as dbm  # noqa: E402
from spider.audit import _score, history, report_markdown, site_audit, theme_of  # noqa: E402
from spider.config import CrawlConfig  # noqa: E402
from spider.instance import free_port, wait_ready  # noqa: E402

FAILS: list[str] = []


def check(cond: bool, label: str, extra: str = "") -> None:
    print(("  ok   " if cond else "  FAIL ") + label + (f"  ({extra})" if extra else ""))
    if not cond:
        FAILS.append(label)


def main() -> None:
    s, pen = _score([{"kind": "issue", "priority": "High", "count": 100}], 100)
    check(s == 75.0 and pen[0]["penalty"] == 25.0, "high error on every page costs 25", str(s))
    s, _ = _score([{"kind": "opportunity", "priority": "Low", "count": 100}], 100)
    check(s == 98.5, "low notice on every page costs 1.5", str(s))
    s, _ = _score([{"kind": "issue", "priority": "High", "count": 50}, {"kind": "warning", "priority": "Medium", "count": 100}], 100)
    check(s == 100 - 12.5 - 7.5, "penalties add up", str(s))
    s, _ = _score([{"kind": "issue", "priority": "High", "count": 100}] * 5, 100)
    check(s == 0.0, "floor at 0")
    check(theme_of("security:mixed_content", "security") == "https" and theme_of("pagespeed:lcp_slow", "pagespeed") == "cwv" and theme_of("pagespeed:unused-css-rules", "pagespeed") == "performance"
          and theme_of("response_codes:int_4xx", "response_codes") == "linking" and theme_of("response_codes:int_blocked_robots", "response_codes") == "crawlability" and theme_of("hreflang:missing_return", "hreflang") == "international", "theme mapping")

    db = fixture_crawl()
    con = dbm.connect(db)
    cfg = CrawlConfig.from_dict({**CrawlConfig().to_dict(), **(dbm.get_meta(con, "config", {}) or {})})
    a = site_audit(con, cfg)
    check(0 <= a["health"] <= 100 and a["grade"] in ("good", "fair", "poor"), "real crawl: health score", f"{a['health']} ({a['grade']})")
    check(a["tally"]["issue"]["types"] + a["tally"]["warning"]["types"] + a["tally"]["opportunity"]["types"] == a["issue_count"], "tally types sum to issue count", str(a["tally"]))
    p = a["pages"]
    check(p["healthy"] + p["with_issues"] == p["ok"] and p["ok"] + p["broken"] + p["redirects"] == p["html"], "crawled pages breakdown adds up", str(p))
    check(len(a["themes"]) == 9 and all(t["score"] is None or 0 <= t["score"] <= 100 for t in a["themes"]), "nine thematic scores")
    check(a["top_issues"] and a["top_issues"][0]["penalty"] >= a["top_issues"][-1]["penalty"], "top issues sorted by cost", f"{a['top_issues'][0]['name']} −{a['top_issues'][0]['penalty']}")
    expected = round(max(0, 100 - sum(i["penalty"] for i in _score([{**i} for i in a["top_issues"]] if a["issue_count"] <= 15 else [], 1)[1])), 1)
    check(dbm.get_meta(con, "audit_summary", {}).get("health") == a["health"], "summary cached in the crawl file")
    md = report_markdown(a)
    check(md.startswith("# Site audit") and "Thematic scores" in md and "Top issues" in md, "markdown report", f"{len(md)} chars")
    pts = history(ROOT / "crawls", db, cfg)
    check(pts and any(pt["current"] for pt in pts) and all("health" in pt for pt in pts), "history across crawls of the site", f"{len(pts)} points: {[round(pt['health']) for pt in pts]}")
    con.close()
    print(f"  info themes: " + ", ".join(f"{t['name']} {t['score']}" for t in a["themes"]))

    port = free_port()
    threading.Thread(target=lambda: uvicorn.run("spider.server:app", host="127.0.0.1", port=port, log_level="warning"), daemon=True).start()
    url = f"http://127.0.0.1:{port}"
    assert wait_ready(url)
    urllib.request.urlopen(urllib.request.Request(f"{url}/api/crawls/open", data=json.dumps({"path": str(db)}).encode(), headers={"Content-Type": "application/json"})).read()
    api_a = json.loads(urllib.request.urlopen(f"{url}/api/audit").read())
    check(api_a["health"] == a["health"], "api audit matches module")
    h = json.loads(urllib.request.urlopen(f"{url}/api/audit/history").read())["points"]
    check(len(h) == len(pts), "api history")
    named = json.loads(urllib.request.urlopen(f"{url}/api/audit?crawl={urllib.parse.quote(db.name)}").read())   # the hosted app reads a crawl by name, without opening it
    check(named["health"] == a["health"] and named["issue_count"] == a["issue_count"], "api audit of a named crawl", f"{named['health']}")
    hn = json.loads(urllib.request.urlopen(f"{url}/api/audit/history?crawl={urllib.parse.quote(db.name)}").read())["points"]
    check(len(hn) == len(pts) and any(pt["current"] for pt in hn), "api history of a named crawl")
    try:
        urllib.request.urlopen(f"{url}/api/audit?crawl=missing.db"); check(False, "unknown crawl name answers 404")
    except urllib.error.HTTPError as exc:
        check(exc.code == 404, "unknown crawl name answers 404")
    r = urllib.request.urlopen(f"{url}/api/audit/report.md"); check(r.headers["Content-Type"].startswith("text/markdown"), "api report download")
    # launcher CORS / private network access preflight
    req = urllib.request.Request(f"{url}/api/system/check", method="OPTIONS", headers={"Origin": "https://contentpilot.cloud", "Access-Control-Request-Method": "GET", "Access-Control-Request-Private-Network": "true"})
    r = urllib.request.urlopen(req)
    check(r.status == 204 and r.headers["Access-Control-Allow-Origin"] == "https://contentpilot.cloud" and r.headers["Access-Control-Allow-Private-Network"] == "true", "preflight for the Content Pilot launcher")
    r = urllib.request.urlopen(urllib.request.Request(f"{url}/api/system/check", headers={"Origin": "https://contentpilot.cloud"}))
    check(r.headers.get("Access-Control-Allow-Origin") == "https://contentpilot.cloud", "GET carries CORS for the launcher origin")
    r = urllib.request.urlopen(urllib.request.Request(f"{url}/api/system/check", headers={"Origin": "https://evil.example"}))
    check(r.headers.get("Access-Control-Allow-Origin") is None, "other origins get no CORS")
    from playwright.sync_api import sync_playwright
    out = str(Path(os.environ.get("TEMP", ".")) / "claude" / "audit_dashboard.png")
    with sync_playwright() as p:
        b = p.chromium.launch(); pg = b.new_page(viewport={"width": 1500, "height": 950})
        pg.goto(url + "/?url=example.com"); pg.wait_for_selector("#splash.hide", state="attached", timeout=20000); time.sleep(2)
        check(pg.input_value("#start-url") == "https://example.com", "?url= deep link prefills the start URL", pg.input_value("#start-url"))
        pg.wait_for_selector(".tabulator-row", timeout=20000)
        pg.click("#btn-audit"); pg.wait_for_selector(".audit-ring", timeout=15000); time.sleep(0.5)
        txt = pg.inner_text("#audit-body")
        check("Site Health" in txt and "Errors" in txt and "Crawlability" in txt and "Top issues" in txt, "dashboard renders health, tally, themes, top issues")
        check(pg.evaluate("document.querySelectorAll('.audit-theme').length") == 9, "nine theme cards")
        check(pg.evaluate("document.querySelectorAll('.audit-trend circle').length") >= 2, "trend chart with points")
        pg.screenshot(path=out)
        pg.click(".audit-top a[data-filter]"); time.sleep(0.8)
        check(pg.evaluate("document.querySelector('#modal-audit').classList.contains('hidden')") and pg.evaluate("document.querySelectorAll('.tabulator-row').length") > 0, "clicking an issue opens its filter")
        b.close()
    print("screenshot:", out)
    print(f"{len(FAILS)} FAILED: {FAILS}" if FAILS else "all checks passed")
    sys.exit(1 if FAILS else 0)


if __name__ == "__main__":
    main()
