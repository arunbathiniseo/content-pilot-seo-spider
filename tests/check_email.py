"""Crawl-complete email check: mock SMTP sink + test endpoint + a real small crawl that triggers the email.

    .venv\\Scripts\\python.exe tests\\check_email.py
"""
from __future__ import annotations

import json
import os
import subprocess
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
from fixture import fixture_crawl, fixture_root  # noqa: E402,F401
os.environ.setdefault("SPIDER_CRAWLS_DIR", str(Path(os.environ.get("TEMP", ".")) / "claude" / "spider_test_crawls"))   # test crawls never land in the real crawls folder
Path(os.environ["SPIDER_CRAWLS_DIR"]).mkdir(parents=True, exist_ok=True)
import sqlite3  # noqa: E402
_src = fixture_crawl()
_a = sqlite3.connect(f"file:{_src}?mode=ro", uri=True); _b = sqlite3.connect(str(Path(os.environ["SPIDER_CRAWLS_DIR"]) / _src.name))
_a.backup(_b); _a.close(); _b.close()   # snapshot of a real crawl to report on; the original is never written to
import uvicorn  # noqa: E402

import spider.server as srv  # noqa: E402
from spider.instance import free_port, wait_ready  # noqa: E402

FAILS: list[str] = []


def check(cond: bool, label: str, extra: str = "") -> None:
    print(("  ok   " if cond else "  FAIL ") + label + (f"  ({extra})" if extra else ""))
    if not cond:
        FAILS.append(label)


def post(url: str, body: dict) -> dict:
    r = urllib.request.urlopen(urllib.request.Request(url, data=json.dumps(body).encode(), headers={"Content-Type": "application/json"}))
    return json.loads(r.read())


def main() -> None:
    scratch = Path(os.environ.get("TEMP", ".")) / "claude" / "settings_email_test.json"
    if scratch.exists():
        scratch.unlink()
    srv.SETTINGS_PATH = scratch                       # never touch the real settings.json
    srv.State.cfg = srv.CrawlConfig()
    smtp_port, http_port = free_port(), free_port()
    sink = subprocess.Popen([sys.executable, str(ROOT / "tests" / "mock_smtp.py"), str(smtp_port), str(http_port)])
    try:
        port = free_port()
        threading.Thread(target=lambda: uvicorn.run(srv.app, host="127.0.0.1", port=port, log_level="warning"), daemon=True).start()
        url = f"http://127.0.0.1:{port}"
        assert wait_ready(url)
        time.sleep(0.5)
        inbox = lambda: json.loads(urllib.request.urlopen(f"http://127.0.0.1:{http_port}/").read())  # noqa: E731

        # validation
        r = urllib.request.urlopen(urllib.request.Request(f"{url}/api/config", data=json.dumps({"email_enabled": True, "email_to": "not-an-address"}).encode(), headers={"Content-Type": "application/json"}))
        check(False, "config rejects bad recipient") if r.status == 200 else None
    except urllib.error.HTTPError as exc:
        errs = json.loads(exc.read()).get("errors", {})
        check(exc.code == 422 and "email_to" in errs and "smtp_host" in errs, "config rejects bad recipient / missing host", str(errs))
    try:
        url = f"http://127.0.0.1:{port}"
        inbox = lambda: json.loads(urllib.request.urlopen(f"http://127.0.0.1:{http_port}/").read())  # noqa: E731
        # test endpoint without settings
        try:
            post(f"{url}/api/email/test", {})
            check(False, "test email without settings errors")
        except urllib.error.HTTPError as exc:
            check(exc.code == 400 and "recipient" in json.loads(exc.read())["detail"], "test email without settings errors")

        cfg = {"email_enabled": True, "email_to": "arun@example.com, seo@example.com", "email_from": "spider@example.com", "smtp_host": "127.0.0.1",
               "smtp_port": smtp_port, "smtp_security": "none", "smtp_user": "", "smtp_pass": "", "max_urls": 12, "crawl_images": False, "crawl_css": False, "crawl_js": False, "check_external": False}
        check(post(f"{url}/api/config", cfg).get("ok") is True, "email settings saved")

        # test endpoint with an open crawl (newest saved crawl) -> full issue table
        crawls = json.loads(urllib.request.urlopen(f"{url}/api/crawls").read())["crawls"]
        post(f"{url}/api/crawls/open", {"path": crawls[0]["path"]})
        t = post(f"{url}/api/email/test", {})
        check(t.get("ok") and "arun@example.com" in t["message"], "test email sent", t.get("message", "")[:80])
        m = inbox()
        check(len(m) == 1 and m[0]["to"] == ["arun@example.com", "seo@example.com"], "sink received it, both recipients", str(m[0]["to"]) if m else "none")
        check(m and m[0]["subject"].startswith("[Test] [Content Pilot Spider]") and "issues" in m[0]["subject"], "subject", m[0]["subject"] if m else "")
        check(m and "<table" in m[0]["html"] and "Priority" in m[0]["html"] and "Issue" in m[0]["text"], "text + html bodies with the issue table")
        rows = m[0]["html"].count("<tr>") - 1 if m else 0
        check(rows >= 1, "html table has issue rows", f"{rows} rows")

        # a real crawl triggers the email automatically; the second crawl of the same site gets the compare summary
        def run_crawl() -> dict:
            s = post(f"{url}/api/crawl/start", {"start_url": LIVE_SITE, "mode": "spider", "config": {"max_urls": 12, "crawl_images": False, "crawl_css": False, "crawl_js": False, "check_external": False}})
            check(s.get("ok"), "crawl started")
            for _ in range(240):
                st = json.loads(urllib.request.urlopen(f"{url}/api/crawl/status").read())
                if st["state"] in ("finished", "stopped", "error"):
                    break
                time.sleep(1)
            check(st["state"] == "finished", "crawl finished", f"{st.get('crawled')} URLs in {st.get('elapsed')}s")
            return st

        for want in (2, 3):
            run_crawl()
            for _ in range(60):
                if len(inbox()) >= want:
                    break
                time.sleep(0.5)
        m = inbox()
        check(len(m) == 3, "crawl-complete emails arrived automatically", f"{len(m)} messages")
        if len(m) == 3:
            last = json.loads(urllib.request.urlopen(f"{url}/api/email/last").read())
            check(last.get("ok") is True, "/api/email/last records success", last.get("message", "")[:80])
            check(LIVE_HOST in m[2]["subject"] and not m[2]["subject"].startswith("[Test]"), "subject names the site", m[2]["subject"])
            check("Since the crawl of" in m[2]["text"], "compare summary included on the second crawl", m[2]["text"].split("\n")[3][:90] if m[2]["text"].count("\n") > 3 else "")
        # test crawls live in the scratch crawls folder (SPIDER_CRAWLS_DIR); scratch settings removed below
    finally:
        sink.terminate()
        if scratch.exists():
            scratch.unlink()
    print(f"{len(FAILS)} FAILED: {FAILS}" if FAILS else "all checks passed")
    sys.exit(1 if FAILS else 0)


if __name__ == "__main__":
    main()
