"""Lighthouse integration check: normaliser on a saved report, storage + filters on a synthetic crawl, then a real
local run on two pages through the API (~1 min) and the PageSpeed pane headless.

    .venv\\Scripts\\python.exe tests\\check_lighthouse.py [--skip-live]
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
from spider.config import CrawlConfig  # noqa: E402
from spider.instance import free_port, wait_ready  # noqa: E402
from spider.issues import TABS, counts, rebuild_issue_table  # noqa: E402
from spider.lighthouse import available, chrome_path, normalise, result_for, sample_ids, store  # noqa: E402

FAILS: list[str] = []


def check(cond: bool, label: str, extra: str = "") -> None:
    print(("  ok   " if cond else "  FAIL ") + label + (f"  ({extra})" if extra else ""))
    if not cond:
        FAILS.append(label)


def post(url: str, body: dict) -> dict:
    r = urllib.request.urlopen(urllib.request.Request(url, data=json.dumps(body).encode(), headers={"Content-Type": "application/json"}))
    return json.loads(r.read())


def main() -> None:
    av = available()
    check(av["node"] and av["lighthouse"] and chrome_path(), "node + lighthouse + chromium available", f"{av['node_path']} / {chrome_path()[-40:]}")
    saved = ROOT / "tests" / "lighthouse_sample.json"   # a real mobile Lighthouse report, site name replaced by example.com
    lhr = json.loads(saved.read_text(encoding="utf-8"))
    res = normalise(lhr, "local", "mobile")
    check(res["scores"]["performance"] == 70 and res["scores"]["accessibility"] == 88, "normalise: category scores", str(res["scores"]))
    check(res["metrics"]["lcp"] and res["metrics"]["lcp"] > 5000 and res["metrics"]["lcp_display"].endswith("s"), "normalise: metrics with display values", res["metrics"]["lcp_display"])
    ids = {a["id"] for a in res["failed"]}
    check("color-contrast" in ids and "offscreen-images" in ids and "largest-contentful-paint" not in ids, "normalise: failing audits, metric audits excluded", f"{len(res['failed'])} audits")
    cc = next(a for a in res["failed"] if a["id"] == "color-contrast")
    check(cc["group"] == "accessibility" and cc["items"] > 0 and cc["sample"] and cc["sample"][0].get("snippet"), "accessibility audit carries element snippets", cc["sample"][0].get("snippet", "")[:60])
    perf_first = next(a for a in res["failed"] if a["group"] == "performance")
    check(res["failed"][0]["group"] == "performance", "performance audits sorted first by savings", f"{perf_first['title']} {perf_first['savings_ms']}")
    # PSI field data normalisation
    fake_field = {"metrics": {"LARGEST_CONTENTFUL_PAINT_MS": {"percentile": 3100, "category": "NEEDS_IMPROVEMENT"}, "CUMULATIVE_LAYOUT_SHIFT_SCORE": {"percentile": 12, "category": "GOOD"}, "INTERACTION_TO_NEXT_PAINT": {"percentile": 180, "category": "GOOD"}}, "overall_category": "NEEDS_IMPROVEMENT"}
    r2 = normalise(lhr, "psi", "mobile", fake_field)
    check(r2["field"]["lcp"] == 3100 and abs(r2["field"]["cls"] - 0.12) < 1e-9 and r2["field"]["inp"] == 180, "PSI field data (CrUX) normalised", str(r2["field"]))

    # storage + filters on a synthetic crawl
    tmp = Path(os.environ.get("TEMP", ".")) / "claude" / "lh_test.db"
    for suf in ("", "-wal", "-shm"):
        p = Path(str(tmp) + suf)
        if p.exists():
            p.unlink()
    con = dbm.connect(tmp)
    for i, (u, inl, dep) in enumerate([("https://x.test/", 50, 0), ("https://x.test/a/", 30, 1), ("https://x.test/b/", 10, 1), ("https://x.test/c/", 5, 2)], start=1):
        con.execute("INSERT INTO urls(id, url, is_internal, type, status_code, crawled, indexable, inlinks, depth) VALUES (?,?,?,?,?,?,?,?,?)", (i, u, 1, "HTML", 200, 1, 1, inl, dep))
    con.commit()
    cfg = CrawlConfig(start_url="https://x.test/")
    check(sample_ids(con, cfg, 2) == [1, 2], "sample: start page first, then most-linked", str(sample_ids(con, cfg, 2)))
    store(con, 1, res); store(con, 2, r2); con.commit()
    row = con.execute("SELECT lh_perf, lh_a11y, lh_lcp, lh_failed, lh_a11y_fails, lh_field_lcp FROM urls WHERE id=2").fetchone()
    check(row["lh_perf"] == 70 and ",color-contrast," in row["lh_failed"] and row["lh_a11y_fails"] >= 3 and row["lh_field_lcp"] == 3100, "store: url columns filled", f"a11y fails {row['lh_a11y_fails']}")
    back = result_for(con, 1)
    check(back and back["scores"] == res["scores"] and len(back["failed"]) == len(res["failed"]), "result_for round-trips")
    assert ("pagespeed", "PageSpeed") in TABS and ("accessibility", "Accessibility") in TABS
    rebuild_issue_table(con, cfg); con.commit()
    c = counts(con, cfg)
    check(c.get("pagespeed:audited") == 2 and c.get("pagespeed:perf_needs_work") == 2 and c.get("pagespeed:lcp_slow") == 2 and c.get("pagespeed:offscreen-images") == 2, "PageSpeed filters count", str({k: v for k, v in c.items() if k.startswith("pagespeed:") and v}))
    check(c.get("accessibility:color-contrast") == 2 and c.get("accessibility:score_needs_work") == 2 and c.get("pagespeed:field_lcp_poor") == 1, "Accessibility + field filters count")
    con.close()
    for suf in ("", "-wal", "-shm"):
        p = Path(str(tmp) + suf)
        if p.exists():
            p.unlink()

    if "--skip-live" in sys.argv:
        print(f"{len(FAILS)} FAILED: {FAILS}" if FAILS else "all checks passed (live run skipped)")
        sys.exit(1 if FAILS else 0)

    # live: real local Lighthouse on 2 pages of the saved crawl through the API
    import spider.server as srv
    scratch = Path(os.environ.get("TEMP", ".")) / "claude" / "settings_lh_test.json"
    if scratch.exists():
        scratch.unlink()
    srv.SETTINGS_PATH = scratch; srv.State.cfg = srv.CrawlConfig()
    port = free_port()
    threading.Thread(target=lambda: uvicorn.run(srv.app, host="127.0.0.1", port=port, log_level="warning"), daemon=True).start()
    url = f"http://127.0.0.1:{port}"
    assert wait_ready(url)
    db = fixture_crawl()
    post(f"{url}/api/crawls/open", {"path": str(db)})
    post(f"{url}/api/config", {"lighthouse_concurrency": 2, "lighthouse_device": "mobile"})
    rows = json.loads(urllib.request.urlopen(f"{url}/api/urls?key=internal:html&size=40").read())["data"]
    picks = [r["id"] for r in rows if r["url"].rstrip("/") in (fixture_root(db), fixture_root(db) + "/pricing")][:2] or [r["id"] for r in rows[:2]]
    t0 = time.time()
    r = post(f"{url}/api/lighthouse/run", {"ids": picks})
    check(r["ok"] and r["pages"] == len(picks) and r["source"] == "local", "api run started", str(r))
    for _ in range(100):
        st = json.loads(urllib.request.urlopen(f"{url}/api/lighthouse/status").read())
        if st["state"] in ("done", "error", "cancelled"):
            break
        time.sleep(3)
    check(st["state"] == "done" and st["ok"] == len(picks), "live run finished", f"{st['ok']} ok, {st['error_count']} errors in {time.time() - t0:.0f}s {st['errors'][:1]}")
    det = json.loads(urllib.request.urlopen(f"{url}/api/url/{picks[0]}").read())
    lh = det.get("lighthouse")
    check(lh and lh["scores"]["performance"] is not None and lh["failed"], "url details carry the Lighthouse result", f"{det['url']} -> {lh['scores'] if lh else None}")
    tabs = json.loads(urllib.request.urlopen(f"{url}/api/tabs").read())["tabs"]
    ps = {f["key"]: f["count"] for t in tabs if t["key"] == "pagespeed" for f in t["filters"]}
    check((ps.get("pagespeed:audited") or 0) >= len(picks), "PageSpeed tab populated", str({k: v for k, v in ps.items() if v}))
    from playwright.sync_api import sync_playwright
    out = str(Path(os.environ.get("TEMP", ".")) / "claude" / "pagespeed_pane.png")
    with sync_playwright() as p:
        b = p.chromium.launch(); pg = b.new_page(viewport={"width": 1500, "height": 950})
        pg.goto(url); pg.wait_for_selector("#splash.hide", state="attached", timeout=20000); pg.wait_for_selector(".tabulator-row", timeout=20000)
        pg.click("#tabs button:has-text('PageSpeed')"); pg.wait_for_selector(".tabulator-row", timeout=20000); time.sleep(0.5)
        box = pg.query_selector(".tabulator-row").bounding_box(); pg.mouse.click(box["x"] + 150, box["y"] + box["height"] / 2)
        pg.click("#bottom-tabs button[data-pane='pagespeed']"); pg.wait_for_selector(".lh-score", timeout=10000)
        check(pg.evaluate("document.querySelectorAll('.lh-score').length") == 4 and pg.evaluate("document.querySelectorAll('.lh-audit').length") > 3, "PageSpeed pane renders scores and audits")
        pg.screenshot(path=out)
        b.close()
    print("screenshot:", out)
    # leave the real crawl clean of the test's lighthouse rows? keep them: they are genuine audit results for the site
    if scratch.exists():
        scratch.unlink()
    print(f"{len(FAILS)} FAILED: {FAILS}" if FAILS else "all checks passed")
    sys.exit(1 if FAILS else 0)


if __name__ == "__main__":
    main()
