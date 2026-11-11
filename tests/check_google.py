"""Search Console + GA4 integration check against a mock Google server: OAuth loopback flow, properties, fetch,
merge (matches + orphans), URL Inspection, filters, status, and the Config panel headless.

    .venv\\Scripts\\python.exe tests\\check_google.py
"""
from __future__ import annotations

import json
import os
import subprocess
import sys
import threading
import time
import urllib.parse
import urllib.request
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
from fixture import fixture_crawl, fixture_root  # noqa: E402,F401
import uvicorn  # noqa: E402

import spider.server as srv  # noqa: E402
from spider.google import pick_gsc_site  # noqa: E402
from spider.instance import free_port, wait_ready  # noqa: E402

FAILS: list[str] = []


def check(cond: bool, label: str, extra: str = "") -> None:
    print(("  ok   " if cond else "  FAIL ") + label + (f"  ({extra})" if extra else ""))
    if not cond:
        FAILS.append(label)


def post(url: str, body: dict) -> dict:
    r = urllib.request.urlopen(urllib.request.Request(url, data=json.dumps(body).encode(), headers={"Content-Type": "application/json"}))
    return json.loads(r.read())


def get(url: str):
    return json.loads(urllib.request.urlopen(url).read())


def main() -> None:
    check(pick_gsc_site([{"url": "https://other.test/", "permission": "x"}, {"url": "sc-domain:example.com", "permission": "siteOwner"}], "https://www.example.com/") == "sc-domain:example.com", "domain property preferred for the crawled host")
    check(pick_gsc_site([{"url": "https://example.com/", "permission": "x"}], "https://example.com/blog/") == "https://example.com/", "URL-prefix property matched")

    scratch = Path(os.environ.get("TEMP", ".")) / "claude" / "settings_google_test.json"
    if scratch.exists():
        scratch.unlink()
    srv.SETTINGS_PATH = scratch; srv.State.cfg = srv.CrawlConfig()
    port = free_port()
    threading.Thread(target=lambda: uvicorn.run(srv.app, host="127.0.0.1", port=port, log_level="warning"), daemon=True).start()
    url = f"http://127.0.0.1:{port}"
    assert wait_ready(url)
    db = fixture_crawl()
    post(f"{url}/api/crawls/open", {"path": str(db)})
    site = fixture_root(db)
    internal0 = get(f"{url}/api/tabs")["tabs"][0]["filters"][0]["count"]
    rows = get(f"{url}/api/urls?key=internal:html&size=10")["data"]
    pages = [r["url"] for r in rows[:3]]
    pages = [p for p in pages if p.rstrip("/") != site][:2] + [site + "/pricing/"] if any(r["url"] == site + "/pricing/" for r in get(f"{url}/api/urls?key=internal:html&size=400&q=pricing")["data"]) else pages
    mock_port = free_port()
    mock = subprocess.Popen([sys.executable, str(ROOT / "tests" / "mock_google.py"), str(mock_port), *pages], stdout=subprocess.DEVNULL)
    try:
        time.sleep(1.0)
        base = f"http://127.0.0.1:{mock_port}"
        # not configured yet
        st = get(f"{url}/api/google/status")
        check(not st["configured"] and not st["connected"], "status: not configured")
        try:
            get(f"{url}/api/google/auth-url"); check(False, "auth-url refuses without client id")
        except urllib.error.HTTPError as exc:
            check(exc.code == 400, "auth-url refuses without client id")
        post(f"{url}/api/config", {"google_client_id": "cid.apps.googleusercontent.com", "google_client_secret": "shh", "google_base_override": base, "gsc_days": 28})
        a = get(f"{url}/api/google/auth-url")
        q = urllib.parse.parse_qs(urllib.parse.urlsplit(a["url"]).query)
        check(a["url"].startswith(base + "/o/oauth2/v2/auth") and q["redirect_uri"][0] == f"{url}/api/google/callback" and "webmasters.readonly" in q["scope"][0] and q["access_type"][0] == "offline", "auth URL: loopback redirect, scopes, offline access")
        # simulate Google redirecting back with a code
        r = urllib.request.urlopen(f"{url}/api/google/callback?code=abc&state={q['state'][0]}")
        html = r.read().decode()
        check(r.status == 200 and "Connected to Google" in html and "arun@example.test" in html, "callback exchanges the code and stores the account")
        try:
            urllib.request.urlopen(f"{url}/api/google/callback?code=abc&state=bogus"); check(False, "callback rejects a bad state")
        except urllib.error.HTTPError as exc:
            check(exc.code == 400, "callback rejects a bad / replayed state")
        cfg = get(f"{url}/api/config")["config"]
        check(srv.State.cfg.google_refresh_token == "rt-1" and cfg["google_refresh_token"] == srv.SECRET_MASK and cfg["google_account"] == "arun@example.test", "refresh token + account saved")
        st = get(f"{url}/api/google/status")
        check(st["connected"] and st["account"] == "arun@example.test", "status: connected")
        p = get(f"{url}/api/google/properties")
        check(p["suggested_gsc"] == "sc-domain:" + urllib.parse.urlsplit(pages[0]).hostname and p["ga4_properties"][0]["id"] == "properties/123", "properties listed; domain property auto-matched", str(p["gsc_sites"]))
        post(f"{url}/api/config", {"ga4_property": "properties/123"})
        # fetch + inspect 3 URLs
        r = post(f"{url}/api/google/run", {"gsc": True, "ga4": True, "inspect": 3})
        check(r["ok"] and r["inspect"] == 3, "run started")
        for _ in range(60):
            j = get(f"{url}/api/google/status")["job"]
            if j["state"] in ("done", "error"):
                break
            time.sleep(0.5)
        check(j["state"] == "done", "job finished", str(j["errors"]))
        g, ga, ins = j["result"].get("gsc", {}), j["result"].get("ga4", {}), j["result"].get("inspect", {})
        check(g.get("matched") == len(pages) and g.get("orphans") == 1, "Search Console rows matched to crawled URLs + 1 orphan", str(g))
        check(ga.get("matched") == len(pages) and ga.get("orphans") == 1, "GA4 paths matched + 1 orphan", str(ga))
        check(ins.get("inspected") == 3, "3 URLs inspected", str(ins))
        det = get(f"{url}/api/url/{rows[0]['id']}") if rows[0]["url"] in pages else get(f"{url}/api/url/{next(x['id'] for x in rows if x['url'] in pages)}")
        check(det["gsc_clicks"] is not None and det["ga_sessions"] is not None, "URL details carry gsc_* and ga_* fields", f"{det['url']}: {det['gsc_clicks']} clicks, {det['ga_sessions']} sessions")
        tabs = get(f"{url}/api/tabs")["tabs"]
        sc = {f["key"]: f["count"] for t in tabs if t["key"] == "search_console" for f in t["filters"]}
        an = {f["key"]: f["count"] for t in tabs if t["key"] == "analytics" for f in t["filters"]}
        check(sc["search_console:clicks"] == len(pages) + 1 and sc["search_console:orphans"] == 1 and sc["search_console:inspected"] == 3, "Search Console filters", str({k: v for k, v in sc.items() if v}))
        check(an["analytics:sessions"] == len(pages) + 1 and an["analytics:orphans"] == 1 and an["analytics:key_events"] >= 1, "Analytics filters", str({k: v for k, v in an.items() if v}))
        check(sc["search_console:no_data"] > 0, "pages without Search Console data flagged", str(sc["search_console:no_data"]))
        if site + "/pricing/" in pages:
            check(sc["search_console:not_on_google"] == 1 and sc["search_console:canonical_mismatch"] == 1, "URL Inspection: not-indexed + canonical mismatch filters")
        orphans = get(f"{url}/api/urls?key=search_console:orphans&size=5")["data"]
        check(orphans and orphans[0]["url"].endswith("/old-landing-page/") and orphans[0]["gsc_clicks"] == 7, "orphan row carries its metrics", orphans[0]["url"] if orphans else "")
        # orphans must not leak into the normal tabs
        internal = get(f"{url}/api/tabs")["tabs"][0]["filters"][0]["count"]
        check(internal == internal0, "orphans do not appear in the Internal tab", str(internal))
        st = get(f"{url}/api/google/status")
        check(st["gsc"] and st["gsc"]["site"] == "sc-domain:" + urllib.parse.urlsplit(pages[0]).hostname and st["ga4"]["property"] == "properties/123", "status carries last-fetch metadata")
        # a second run replaces, not duplicates
        post(f"{url}/api/google/run", {"gsc": True, "ga4": True, "inspect": 0})
        for _ in range(60):
            if get(f"{url}/api/google/status")["job"]["state"] in ("done", "error"):
                break
            time.sleep(0.5)
        check(get(f"{url}/api/tabs")["tabs"][0]["filters"][0]["count"] == internal0 and get(f"{url}/api/urls?key=search_console:orphans&size=5")["total"] == 1, "re-fetch replaces previous data")
        calls = get(f"{base}/calls")
        check(sum(1 for c in calls if "urlInspection" in c) == 3 and sum(1 for c in calls if "searchAnalytics" in c) == 2, "API call counts as expected", f"{len(calls)} calls")
        # disconnect
        post(f"{url}/api/google/disconnect", {})
        check(not get(f"{url}/api/google/status")["connected"], "disconnect clears the token")
        # panel
        from playwright.sync_api import sync_playwright
        out = str(Path(os.environ.get("TEMP", ".")) / "claude" / "google_panel.png")
        with sync_playwright() as p:
            b = p.chromium.launch(); pg = b.new_page(viewport={"width": 1400, "height": 900})
            pg.goto(url); pg.wait_for_selector("#splash.hide", state="attached", timeout=20000)
            pg.click("#btn-config"); pg.click(".cfg-nav button[data-g='Search Console & GA4']"); pg.wait_for_selector("#cfg-g-connect")
            time.sleep(0.6)
            check("Not connected" in pg.inner_text("#cfg-g-out"), "panel shows not-connected state", pg.inner_text("#cfg-g-out")[:60])
            pg.screenshot(path=out)
            pg.click("#config-cancel")
            pg.click("#tabs button:has-text('Search Console')"); pg.wait_for_selector(".tabulator-row", timeout=20000)
            check(pg.evaluate("document.querySelectorAll('.tabulator-row').length") >= 3, "Search Console tab lists pages with data")
            b.close()
        print("screenshot:", out)
    finally:
        mock.terminate()
        # clean the Google columns + orphan rows the test wrote into the real crawl file
        try:
            import sqlite3
            con = sqlite3.connect(str(db)); con.execute("DELETE FROM urls WHERE crawled=5")
            con.execute("UPDATE urls SET gsc_clicks=NULL, gsc_impressions=NULL, gsc_ctr=NULL, gsc_position=NULL, ga_sessions=NULL, ga_engaged=NULL, ga_engagement_rate=NULL, ga_bounce_rate=NULL, ga_views=NULL, ga_key_events=NULL, inspect_verdict=NULL, inspect_coverage=NULL, inspect_at=NULL, inspect_google_canonical=NULL, inspect_user_canonical=NULL")
            con.execute("DELETE FROM meta WHERE key IN ('gsc','ga4')"); con.commit(); con.close()
        except Exception as exc:  # noqa: BLE001
            print("cleanup failed:", exc)
        if scratch.exists():
            scratch.unlink()
    print(f"{len(FAILS)} FAILED: {FAILS}" if FAILS else "all checks passed")
    sys.exit(1 if FAILS else 0)


if __name__ == "__main__":
    main()
