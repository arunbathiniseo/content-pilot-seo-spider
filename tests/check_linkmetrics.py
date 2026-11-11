"""Link metrics check against a mock of the Ahrefs / Moz / Majestic APIs: provider adapters, batching, storage, the
Link Metrics tab filters, the API, and the menu + tab headless.

    .venv\\Scripts\\python.exe tests\\check_linkmetrics.py
"""
from __future__ import annotations

import json
import os
import sys
import threading
import time
import urllib.parse
import urllib.request
from http.server import BaseHTTPRequestHandler, HTTPServer
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
from fixture import fixture_crawl, fixture_root  # noqa: E402,F401
import uvicorn  # noqa: E402

import spider.server as srv  # noqa: E402
from spider.config import CrawlConfig  # noqa: E402
from spider.instance import free_port, wait_ready  # noqa: E402
from spider.linkmetrics import providers  # noqa: E402

FAILS: list[str] = []
CALLS: list[str] = []


def check(cond: bool, label: str, extra: str = "") -> None:
    print(("  ok   " if cond else "  FAIL ") + label + (f"  ({extra})" if extra else ""))
    if not cond:
        FAILS.append(label)


class Mock(BaseHTTPRequestHandler):
    def log_message(self, *a):
        pass

    def _json(self, code, obj):
        body = json.dumps(obj).encode(); self.send_response(code); self.send_header("Content-Type", "application/json"); self.send_header("Content-Length", str(len(body))); self.end_headers(); self.wfile.write(body)

    def do_GET(self):
        CALLS.append(self.path.split("?")[0])
        q = urllib.parse.parse_qs(urllib.parse.urlsplit(self.path).query)
        if self.headers.get("Authorization") != "Bearer ah-token" and "/v3/" in self.path:
            return self._json(401, {"error": "bad token"})
        if self.path.startswith("/v3/site-explorer/backlinks-stats"):
            n = 0 if q["target"][0].endswith("/pricing/") else 120
            return self._json(200, {"metrics": {"live": n, "all_time": n * 2, "live_refdomains": n // 3, "all_time_refdomains": n // 2}})
        if self.path.startswith("/v3/site-explorer/url-rating"):
            return self._json(200, {"url_rating": {"url_rating": 7.5 if q["target"][0].endswith("/pricing/") else 24.0}})
        if self.path.startswith("/v3/site-explorer/domain-rating"):
            return self._json(200, {"domain_rating": {"domain_rating": 52.0}})
        if self.path.startswith("/v3/site-explorer/metrics"):
            return self._json(200, {"metrics": {"org_keywords": 310, "org_traffic": 1450}})
        if self.path.startswith("/api/json"):
            if q.get("app_api_key", [""])[0] != "maj-key":
                return self._json(200, {"Code": "InvalidAPIKey", "ErrorMessage": "bad key"})
            n = int(q["items"][0])
            rows = [{"Item": q[f"item{i}"][0], "TrustFlow": 18 + i, "CitationFlow": 30 + i, "ExtBackLinks": 400 + i, "RefDomains": 60 + i} for i in range(n)]
            return self._json(200, {"Code": "OK", "DataTables": {"Results": {"Data": rows}}})
        self._json(404, {"error": self.path})

    def do_POST(self):
        CALLS.append(self.path)
        raw = self.rfile.read(int(self.headers.get("Content-Length", 0)) or 0)
        if self.path == "/v2/url_metrics":
            if not self.headers.get("Authorization", "").startswith("Basic "):
                return self._json(401, {"error": "no auth"})
            targets = json.loads(raw)["targets"]
            return self._json(200, {"results": [{"page": t, "page_authority": 15 if t.endswith("/pricing/") else 38, "domain_authority": 47, "spam_score": 35 if t.endswith("/pricing/") else 2, "root_domains_to_page": 12, "external_pages_to_page": 55} for t in targets]})
        self._json(404, {"error": self.path})


def post(url: str, body: dict) -> dict:
    r = urllib.request.urlopen(urllib.request.Request(url, data=json.dumps(body).encode(), headers={"Content-Type": "application/json"}))
    return json.loads(r.read())


def get(url: str):
    return json.loads(urllib.request.urlopen(url).read())


def main() -> None:
    check(providers(CrawlConfig()) == [] and providers(CrawlConfig(ahrefs_token="x", moz_access_id="a", moz_secret="b", majestic_key="m")) == ["ahrefs", "moz", "majestic"], "providers() from keys")
    check(providers(CrawlConfig(moz_access_id="a")) == [], "Moz needs id + secret")
    mock_port = free_port()
    httpd = HTTPServer(("127.0.0.1", mock_port), Mock); threading.Thread(target=httpd.serve_forever, daemon=True).start()
    scratch = Path(os.environ.get("TEMP", ".")) / "claude" / "settings_lm_test.json"
    if scratch.exists():
        scratch.unlink()
    srv.SETTINGS_PATH = scratch; srv.State.cfg = srv.CrawlConfig()
    port = free_port()
    threading.Thread(target=lambda: uvicorn.run(srv.app, host="127.0.0.1", port=port, log_level="warning"), daemon=True).start()
    url = f"http://127.0.0.1:{port}"
    assert wait_ready(url)
    db = fixture_crawl()
    post(f"{url}/api/crawls/open", {"path": str(db)})
    try:
        post(f"{url}/api/linkmetrics/run", {"sample": 3}); check(False, "run refuses without keys")
    except urllib.error.HTTPError as exc:
        check(exc.code == 400 and "API key" in json.loads(exc.read())["detail"], "run refuses without keys")
    post(f"{url}/api/config", {"ahrefs_token": "ah-token", "moz_access_id": "id", "moz_secret": "sec", "majestic_key": "maj-key", "linkmetrics_base_override": f"http://127.0.0.1:{mock_port}", "linkmetrics_max_urls": 5})
    rows = get(f"{url}/api/urls?key=internal:html&size=400&q=pricing")["data"]
    pricing = next((r for r in rows if r["url"] == fixture_root(db) + "/pricing/"), None)
    r = post(f"{url}/api/linkmetrics/run", {"sample": 8})
    check(r["ok"] and r["urls"] == 5 and r["providers"] == ["ahrefs", "moz", "majestic"], "run capped at linkmetrics_max_urls, all providers", str(r))
    for _ in range(60):
        s = get(f"{url}/api/linkmetrics/status")
        if s["state"] in ("done", "error"):
            break
        time.sleep(0.5)
    check(s["state"] == "done" and s["done"] == 5 and not s["errors"] and s["fetched"] == 5, "job done, 5 URLs stored", str(s["errors"]))
    check(CALLS.count("/v2/url_metrics") == 1 and CALLS.count("/api/json") == 1 and CALLS.count("/v3/site-explorer/backlinks-stats") == 5, "Moz + Majestic batched once, Ahrefs per URL", f"{len(CALLS)} calls")
    top = get(f"{url}/api/urls?key=link_metrics:has&size=10")["data"]
    check(len(top) == 5 and all(t["lm_ahrefs_dr"] == 52 and t["lm_moz_da"] == 47 and t["lm_majestic_tf"] >= 18 for t in top), "all three providers' columns filled", f"{top[0]['url']}: UR {top[0]['lm_ahrefs_ur']} PA {top[0]['lm_moz_pa']} TF {top[0]['lm_majestic_tf']}")
    det = get(f"{url}/api/url/{top[0]['id']}")
    check(det["lm_providers"] == "ahrefs,moz,majestic" and det["lm_ahrefs_backlinks"] == 120, "URL details carry lm_* fields")
    if pricing:
        r = post(f"{url}/api/linkmetrics/run", {"ids": [pricing["id"]]})
        for _ in range(40):
            if get(f"{url}/api/linkmetrics/status")["state"] in ("done", "error"):
                break
            time.sleep(0.5)
        tabs = get(f"{url}/api/tabs")["tabs"]
        lm = {f["key"]: f["count"] for t in tabs if t["key"] == "link_metrics" for f in t["filters"]}
        check(lm["link_metrics:has"] == 6 and lm["link_metrics:no_backlinks"] >= 1 and lm["link_metrics:low_authority"] >= 1 and lm["link_metrics:spam"] == 1, "Link Metrics filters", str({k: v for k, v in lm.items() if v}))
    # bad credentials surface as errors, not crashes
    post(f"{url}/api/config", {"ahrefs_token": "wrong", "majestic_key": "wrong"})
    post(f"{url}/api/linkmetrics/run", {"sample": 2})
    for _ in range(40):
        s = get(f"{url}/api/linkmetrics/status")
        if s["state"] in ("done", "error"):
            break
        time.sleep(0.5)
    check(s["state"] == "done" and s["error_count"] >= 2 and any("Ahrefs rejected" in e for e in s["errors"]) and any("Majestic" in e for e in s["errors"]), "bad keys reported per provider; Moz still stored", str(s["errors"])[:160])
    # UI
    from playwright.sync_api import sync_playwright
    out = str(Path(os.environ.get("TEMP", ".")) / "claude" / "linkmetrics_tab.png")
    with sync_playwright() as p:
        b = p.chromium.launch(); pg = b.new_page(viewport={"width": 1500, "height": 900})
        pg.goto(url); pg.wait_for_selector("#splash.hide", state="attached", timeout=20000); pg.wait_for_selector(".tabulator-row", timeout=20000)
        pg.click("#btn-google"); check(pg.is_visible("#google-menu a[data-lm='sample']"), "Data menu has link metrics entries")
        pg.click("#google-menu a[data-lm='config']"); pg.wait_for_selector("[data-k='ahrefs_token']"); check(True, "menu opens the Link metrics config"); pg.click("#config-cancel")
        pg.click("#tabs button:has-text('Link Metrics')"); pg.wait_for_selector(".tabulator-row", timeout=20000); time.sleep(0.4)
        check(pg.evaluate("document.querySelectorAll('.tabulator-row').length") >= 5, "Link Metrics tab lists fetched pages")
        pg.screenshot(path=out)
        b.close()
    print("screenshot:", out)
    httpd.shutdown()
    # clean the mock numbers out of the real crawl file
    import sqlite3
    con = sqlite3.connect(str(db))
    con.execute("UPDATE urls SET " + ", ".join(f"{c}=NULL" for c in ["lm_ahrefs_ur", "lm_ahrefs_dr", "lm_ahrefs_backlinks", "lm_ahrefs_refdomains", "lm_ahrefs_keywords", "lm_ahrefs_traffic", "lm_moz_pa", "lm_moz_da", "lm_moz_spam", "lm_moz_linking_domains", "lm_moz_backlinks", "lm_majestic_tf", "lm_majestic_cf", "lm_majestic_backlinks", "lm_majestic_refdomains", "lm_at", "lm_providers"]))
    con.commit(); con.close()
    if scratch.exists():
        scratch.unlink()
    print(f"{len(FAILS)} FAILED: {FAILS}" if FAILS else "all checks passed")
    sys.exit(1 if FAILS else 0)


if __name__ == "__main__":
    main()
