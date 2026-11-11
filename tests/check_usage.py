"""Usage stats check: module math, crawl + AI recording through the API (mock provider), prices endpoint, Usage modal.

    .venv\\Scripts\\python.exe tests\\check_usage.py
"""
from __future__ import annotations

import json
import os
import subprocess
import sys
import threading
import time
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
_a.backup(_b); _a.close(); _b.close()   # fresh snapshot (WAL included) of the real crawl for the AI-brief part; the original is never written to
import uvicorn  # noqa: E402

import spider.server as srv  # noqa: E402
import spider.usage as usage  # noqa: E402
from spider.instance import free_port, wait_ready  # noqa: E402

FAILS: list[str] = []


def check(cond: bool, label: str, extra: str = "") -> None:
    print(("  ok   " if cond else "  FAIL ") + label + (f"  ({extra})" if extra else ""))
    if not cond:
        FAILS.append(label)


def post(url: str, body: dict) -> dict:
    r = urllib.request.urlopen(urllib.request.Request(url, data=json.dumps(body).encode(), headers={"Content-Type": "application/json"}))
    return json.loads(r.read())


def get(url: str) -> dict:
    return json.loads(urllib.request.urlopen(url).read())


def main() -> None:
    tmp = Path(os.environ.get("TEMP", ".")) / "claude"
    usage.USAGE_PATH = tmp / "usage_test.json"          # never touch the real usage.json / settings.json
    if usage.USAGE_PATH.exists():
        usage.USAGE_PATH.unlink()
    srv.SETTINGS_PATH = tmp / "settings_usage_test.json"
    if srv.SETTINGS_PATH.exists():
        srv.SETTINGS_PATH.unlink()
    srv.State.cfg = srv.CrawlConfig()

    # module
    check(usage.cost_usd("anthropic", "claude-opus-5-5", 1_000_000, 1_000_000) == 24.0, "price table: opus 5.5 = $4 + $20 per M")
    check(usage.cost_usd("anthropic", "claude-opus-5-5-20260401", 1_000_000, 0) == 4.0, "dated model id falls back to base price")
    check(usage.cost_usd("openai", "gpt-4.1", 1000, 1000) is None, "unknown model -> None (unpriced)")
    usage.set_prices({"openai:gpt-4.1": [2, 8]})
    check(usage.cost_usd("openai", "gpt-4.1", 500_000, 250_000) == 3.0, "custom price applies")
    usage.set_prices({"openai:gpt-4.1": None})
    check(usage.cost_usd("openai", "gpt-4.1", 1, 1) is None, "empty price removes the override")
    usage.record_ai("anthropic", "claude-haiku-5-5", "title", 3, 30_000, 6_000, crawl="x.db", label="t")
    s = usage.summary()
    check(s["totals"]["ai_requests"] == 3 and s["by_model"][0]["cost_usd"] == 0.006, "summary aggregates requests and cost", str(s["by_model"][0]["cost_usd"]))
    usage.record_crawl("x.db", "https://x.test", "spider", "finished", 120, 40, 33.3, "2026-10-15T10:00:00")
    s = usage.summary()
    check(s["runs"][0]["ai"]["requests"] == 3 and s["totals"]["urls"] == 120, "per-run row carries its AI usage")
    usage.record_crawl("x.db", "https://x.test", "spider", "finished", 121, 40, 34.0, "2026-10-15T10:00:00")
    check(len(usage.summary()["runs"]) == 1, "re-recording a crawl replaces, not duplicates")
    usage.USAGE_PATH.unlink()

    # api, with the mock provider
    mock_port = free_port()
    mock = subprocess.Popen([sys.executable, str(ROOT / "tests" / "mock_ai.py"), str(mock_port)], stdout=subprocess.DEVNULL)
    try:
        port = free_port()
        threading.Thread(target=lambda: uvicorn.run(srv.app, host="127.0.0.1", port=port, log_level="warning"), daemon=True).start()
        url = f"http://127.0.0.1:{port}"
        assert wait_ready(url)
        time.sleep(0.5)
        post(f"{url}/api/config", {"ai_provider": "custom", "ai_base_url": f"http://127.0.0.1:{mock_port}/v1", "ai_api_key": "x", "ai_model": "mock"})
        t = post(f"{url}/api/ai/test", {})
        check(t.get("ok"), "test connection", t.get("message", "")[:60])
        u = get(f"{url}/api/usage")
        check(u["totals"]["ai_requests"] == 1 and u["totals"]["input_tokens"] > 0, "test connection recorded with tokens", f"{u['totals']['input_tokens']} in / {u['totals']['output_tokens']} out")
        crawls = get(f"{url}/api/crawls")["crawls"]
        target = next(c for c in crawls if c["name"] == fixture_crawl().name)
        post(f"{url}/api/crawls/open", {"path": target["path"]})
        issues = get(f"{url}/api/issues")["issues"]
        key = next((i["key"] for i in issues if i["key"].startswith("page_titles:")), issues[0]["key"])
        post(f"{url}/api/fixes/generate", {"key": key, "ids": []})
        for _ in range(120):
            st = get(f"{url}/api/fixes/status")
            if st["state"] in ("done", "error", "cancelled"):
                break
            time.sleep(0.5)
        check(st["state"] == "done" and st["created"] > 0, "fixes generated with the mock", f"{st['created']} proposals, {st['total']} URLs")
        u = get(f"{url}/api/usage")
        m = next((x for x in u["by_model"] if x["model"] == "mock"), None)
        check(m and m["requests"] >= 2 and m["input_tokens"] > 0, "fix job usage recorded per model", f"{m['requests']} req, {m['input_tokens']} tok" if m else "missing")
        check(not m["priced"] and "custom:mock" in u["totals"]["unpriced_models"], "unpriced model flagged")
        other = u["other_ai"]
        check(target["name"] in other and other[target["name"]]["requests"] >= 1, "AI usage tied to the crawl file it ran on")
        r = post(f"{url}/api/usage/prices", {"prices": {"custom:mock": [1.0, 2.0]}})
        check(r["ok"] and r["prices"]["custom:mock"] == [1.0, 2.0], "prices saved via API")
        u = get(f"{url}/api/usage")
        m = next(x for x in u["by_model"] if x["model"] == "mock")
        check(m["priced"] and m["cost_usd"] > 0 and not u["totals"]["unpriced_models"], "cost computed after pricing", str(m["cost_usd"]))

        # a real small crawl is recorded as a run (override limits in the request so the opened crawl's config does not apply)
        s0 = post(f"{url}/api/crawl/start", {"start_url": LIVE_SITE, "mode": "spider",
                                             "config": {"max_urls": 10, "crawl_images": False, "crawl_css": False, "crawl_js": False, "check_external": False}})
        check(s0.get("ok"), "crawl started")
        for _ in range(180):
            st = get(f"{url}/api/crawl/status")
            if st["state"] in ("finished", "stopped", "error"):
                break
            time.sleep(1)
        time.sleep(1.5)
        u = get(f"{url}/api/usage")
        run = u["runs"][0] if u["runs"] else {}
        check(st["state"] == "finished" and run.get("urls", 0) > 0 and LIVE_HOST in run.get("start_url", ""), "finished crawl recorded as a run", f"{run.get('urls')} URLs in {run.get('elapsed')}s")
        check(u["totals"]["crawls"] == 1, "totals count the run")

        # modal
        from playwright.sync_api import sync_playwright
        out = str(tmp / "usage_modal.png")
        with sync_playwright() as p:
            b = p.chromium.launch(); pg = b.new_page(viewport={"width": 1400, "height": 900})
            pg.goto(url); pg.wait_for_selector("#splash.hide", state="attached", timeout=20000)
            pg.click("#btn-help"); pg.click("#help-usage"); pg.wait_for_selector("#usage-body .tiles", timeout=15000)
            txt = pg.inner_text("#usage-body")
            check("Crawls run" in txt and "Est. AI cost" in txt and "mock" in txt, "usage modal renders totals + model table")
            pg.click("#modal-usage .cmp-tabs button[data-u='runs']"); pg.wait_for_selector("#usage-body table"); check(LIVE_HOST in pg.inner_text("#usage-body"), "per-run tab lists the crawl")
            pg.click("#modal-usage .cmp-tabs button[data-u='prices']"); pg.wait_for_selector("#price-table")
            check(pg.evaluate("document.querySelectorAll('#price-table tr[data-k]').length") > 10, "prices tab lists built-in + used models")
            pg.screenshot(path=out)
            b.close()
        print("screenshot:", out)
    finally:
        mock.terminate()
        for pth in (usage.USAGE_PATH, srv.SETTINGS_PATH):
            if pth.exists():
                pth.unlink()
    print(f"{len(FAILS)} FAILED: {FAILS}" if FAILS else "all checks passed")
    sys.exit(1 if FAILS else 0)


if __name__ == "__main__":
    main()
