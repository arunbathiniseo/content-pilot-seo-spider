"""Change detection check: compares the two newest completed crawls of the same site through the API and the
module, and opens the Compare modal headless.

    .venv\\Scripts\\python.exe tests\\check_compare.py
"""
from __future__ import annotations

import json
import os
import sys
import threading
import urllib.parse
import urllib.request
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
import uvicorn  # noqa: E402

from spider.compare import compare, previous_crawl, to_sheets  # noqa: E402
from spider.config import CrawlConfig  # noqa: E402
from spider.instance import free_port, wait_ready  # noqa: E402

FAILS: list[str] = []


def check(cond: bool, label: str, extra: str = "") -> None:
    print(("  ok   " if cond else "  FAIL ") + label + (f"  ({extra})" if extra else ""))
    if not cond:
        FAILS.append(label)


def main() -> None:
    crawls = sorted((ROOT / "crawls").glob("*.db"), key=lambda p: p.stat().st_mtime, reverse=True)
    newest = next((p for p in crawls if previous_crawl(p, ROOT / "crawls")), None)
    assert newest, "need two completed crawls of one site in crawls/"
    prev = previous_crawl(newest, ROOT / "crawls")
    print(f"comparing {prev.name} -> {newest.name}")
    r = compare(prev, newest, CrawlConfig())
    s = r["summary"]
    check(s["pages_before"] > 0 and s["pages_after"] > 0, "module: both crawls have pages", f"{s['pages_before']} -> {s['pages_after']}")
    check(s["added"] + s["removed"] + s["changed"] + s["unchanged"] >= max(s["pages_before"], s["pages_after"]) - s["removed"], "module: counts add up")
    check(isinstance(r["issues"], list) and all({"key", "before", "after", "delta"} <= set(i) for i in r["issues"]), "module: issues table", f"{len(r['issues'])} issues")
    check(all(i["delta"] == i["after"] - i["before"] for i in r["issues"]), "module: deltas consistent")
    sheets = to_sheets(r)
    check([n for n, _, _ in sheets] == ["Summary", "Issues", "Changed", "Added", "Removed"], "module: workbook sheets")
    r2 = compare(prev, newest, CrawlConfig())
    check(r2 is r, "module: cached per file pair")

    port = free_port()
    threading.Thread(target=lambda: uvicorn.run("spider.server:app", host="127.0.0.1", port=port, log_level="warning"), daemon=True).start()
    url = f"http://127.0.0.1:{port}"
    assert wait_ready(url)
    q = urllib.parse.urlencode({"a": prev.name, "b": newest.name, "limit": 5})
    d = json.loads(urllib.request.urlopen(f"{url}/api/compare?{q}").read())
    check(d["summary"] == s and len(d["changed"]) <= 5, "api: /api/compare matches module, limit applied")
    body = json.dumps({"path": str(newest)}).encode()
    urllib.request.urlopen(urllib.request.Request(f"{url}/api/crawls/open", data=body, headers={"Content-Type": "application/json"})).read()
    d2 = json.loads(urllib.request.urlopen(f"{url}/api/compare?limit=1").read())
    check(d2["a"]["name"] == prev.name and d2["b"]["name"] == newest.name, "api: defaults = open crawl vs previous of same site")
    x = urllib.request.urlopen(f"{url}/api/compare/export?{urllib.parse.urlencode({'a': prev.name, 'b': newest.name})}").read()
    check(x[:2] == b"PK" and len(x) > 2000, "api: xlsx export", f"{len(x)} bytes")
    try:
        urllib.request.urlopen(f"{url}/api/compare?{urllib.parse.urlencode({'a': newest.name, 'b': newest.name})}").read()
        check(False, "api: same crawl twice rejected")
    except urllib.error.HTTPError as exc:
        check(exc.code == 400, "api: same crawl twice rejected", str(exc.code))

    from playwright.sync_api import sync_playwright
    out = os.path.join(os.environ.get("TEMP", "."), "claude", "compare_modal.png")
    with sync_playwright() as p:
        b = p.chromium.launch(); pg = b.new_page(viewport={"width": 1500, "height": 900})
        pg.goto(url); pg.wait_for_selector("#splash.hide", state="attached", timeout=20000)
        pg.click("#btn-compare"); pg.wait_for_selector(".tiles", timeout=20000)
        check(pg.input_value("#cmp-b") == newest.name and pg.input_value("#cmp-a") == prev.name, "ui: selects preselect open crawl vs previous")
        check(pg.evaluate("document.querySelectorAll('#compare-body table tr').length") > 1, "ui: issues table rendered")
        pg.click(".cmp-tabs button[data-t='changed']"); pg.wait_for_timeout(200)
        check("Changed" in pg.inner_text(".cmp-tabs button.active"), "ui: changed tab")
        pg.screenshot(path=out)
        b.close()
    print("screenshot:", out)
    print(f"{len(FAILS)} FAILED: {FAILS}" if FAILS else "all checks passed")
    sys.exit(1 if FAILS else 0)


if __name__ == "__main__":
    main()
