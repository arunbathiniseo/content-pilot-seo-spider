"""Visualisation data + rendering check: crawl tree, directory tree, word clouds via module, API and headless UI.

    .venv\\Scripts\\python.exe tests\\check_viz.py
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
from spider.viz import crawl_tree, directory_tree, word_frequencies  # noqa: E402

FAILS: list[str] = []


def check(cond: bool, label: str, extra: str = "") -> None:
    print(("  ok   " if cond else "  FAIL ") + label + (f"  ({extra})" if extra else ""))
    if not cond:
        FAILS.append(label)


def count_nodes(n: dict) -> int:
    return 1 + sum(count_nodes(c) for c in n.get("children", []))


def main() -> None:
    db = fixture_crawl()
    con = dbm.connect(db, readonly=True)
    cfg = CrawlConfig(start_url=dbm.get_meta(con, "start_url", ""))
    t = crawl_tree(con, cfg, 500)
    check(t["tree"] and t["nodes"] == count_nodes(t["tree"]) and t["nodes"] <= 500, "crawl tree: node count matches", f"{t['nodes']} of {t['total']} pages, {t['orphans']} orphans, depth {t['max_depth']}")
    check(t["tree"]["url"].rstrip("/") in (fixture_root(db), fixture_root(db) + "/sitemap_index.xml") or t["tree"]["depth"] == 0, "root is the home page (sitemap crawl has no start page, so the home URL is used)", t["tree"]["url"])
    check(t["max_depth"] >= 2 and t["tree"].get("children"), "tree has depth")
    small = crawl_tree(con, cfg, 60)
    check(small["nodes"] == 60 and small["truncated"], "max_nodes respected and reported")
    d = directory_tree(con, 2000)
    check(d["tree"]["folder"] and d["total"] == t["total"] and any(c.get("folder") for c in d["tree"]["children"]), "directory tree: folders with page counts", f"{[(c['name'], c['pages']) for c in d['tree']['children'][:4]]}")
    biggest = max((c for c in d["tree"]["children"] if c.get("folder")), key=lambda c: c["pages"])
    check(biggest["pages"] > 1 and any(not c.get("folder") for c in biggest["children"]), "biggest folder has page leaves", f"{biggest['name']}: {biggest['pages']} pages")
    w = word_frequencies(con, "anchor", 50)
    check(w["words"] and w["words"][0]["count"] >= w["words"][-1]["count"] and all(x["text"] not in ("the", "and") for x in w["words"]), "anchor words ranked, stop words removed", str([x["text"] for x in w["words"][:6]]))
    b = word_frequencies(con, "body", 50)
    check(b["words"] and b["total_words"] > w["total_words"], "body words", str([x["text"] for x in b["words"][:6]]))
    con.close()

    port = free_port()
    threading.Thread(target=lambda: uvicorn.run("spider.server:app", host="127.0.0.1", port=port, log_level="warning"), daemon=True).start()
    url = f"http://127.0.0.1:{port}"
    assert wait_ready(url)
    urllib.request.urlopen(urllib.request.Request(f"{url}/api/crawls/open", data=json.dumps({"path": str(db)}).encode(), headers={"Content-Type": "application/json"})).read()
    a = json.loads(urllib.request.urlopen(f"{url}/api/viz/tree?mode=crawl&max_nodes=300").read())
    check(a["nodes"] == 300 and a["tree"], "api crawl tree")
    a = json.loads(urllib.request.urlopen(f"{url}/api/viz/tree?mode=directory").read())
    check(a["tree"]["folder"], "api directory tree")
    a = json.loads(urllib.request.urlopen(f"{url}/api/viz/words?kind=body&limit=20").read())
    check(len(a["words"]) == 20, "api words limit")
    from playwright.sync_api import sync_playwright
    out_dir = Path(os.environ.get("TEMP", ".")) / "claude"
    with sync_playwright() as p:
        b = p.chromium.launch(); pg = b.new_page(viewport={"width": 1500, "height": 900})
        errs = []
        pg.on("pageerror", lambda e: errs.append(str(e)))
        pg.goto(url); pg.wait_for_selector("#splash.hide", state="attached", timeout=20000); pg.wait_for_selector(".tabulator-row", timeout=20000)
        check(pg.evaluate("typeof d3 !== 'undefined' && typeof d3.layout.cloud === 'function'"), "d3 + d3-cloud loaded")
        pg.click("#btn-viz"); pg.wait_for_selector("#viz-svg g.node", timeout=20000); time.sleep(0.5)
        check(pg.evaluate("document.querySelectorAll('#viz-svg g.node').length") >= 100, "crawl tree renders nodes", str(pg.evaluate("document.querySelectorAll('#viz-svg g.node').length")))
        pg.screenshot(path=str(out_dir / "viz_tree.png"))
        for tab, sel in (("dirtree", "#viz-svg g.node"), ("force", "#viz-svg line.link"), ("dirforce", "#viz-svg line.link"), ("anchor", "#viz-svg text"), ("body", "#viz-svg text")):
            pg.click(f"#viz-tabs button[data-v='{tab}']"); pg.wait_for_selector(sel, timeout=30000); time.sleep(1.2 if tab in ("force", "dirforce") else 0.6)
            n = pg.evaluate(f"document.querySelectorAll('{sel}').length")
            check(n > 10, f"{tab} renders", f"{n} elements")
            pg.screenshot(path=str(out_dir / f"viz_{tab}.png"))
        # hover tooltip + click filters the grid
        pg.click("#viz-tabs button[data-v='tree']"); pg.wait_for_selector("#viz-svg g.node circle", timeout=20000); time.sleep(0.5)
        circ = pg.query_selector_all("#viz-svg g.node circle")[2]; box = circ.bounding_box()
        pg.mouse.move(box["x"] + box["width"] / 2, box["y"] + box["height"] / 2); time.sleep(0.2)
        check(not pg.evaluate("document.querySelector('#viz-tip').classList.contains('hidden')") and "inlinks" in pg.inner_text("#viz-tip"), "tooltip shows node facts", pg.inner_text("#viz-tip").split("\n")[0][:60])
        pg.mouse.click(box["x"] + box["width"] / 2, box["y"] + box["height"] / 2); time.sleep(0.8)
        check(pg.evaluate("document.querySelector('#modal-viz').classList.contains('hidden')") and pg.input_value("#search").startswith("https://"), "click filters the grid to that URL", pg.input_value("#search")[:60])
        check(not errs, "no page errors", "; ".join(errs)[:200])
        b.close()
    print("screenshots in", out_dir)
    print(f"{len(FAILS)} FAILED: {FAILS}" if FAILS else "all checks passed")
    sys.exit(1 if FAILS else 0)


if __name__ == "__main__":
    main()
