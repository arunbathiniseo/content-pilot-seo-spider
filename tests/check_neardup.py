"""Near-duplicate detection check: synthetic crawl file with known similar pages, then the real saved crawl via the
API (Analyse + Content > Near Duplicates filter + Duplicates pane).

    .venv\\Scripts\\python.exe tests\\check_neardup.py
"""
from __future__ import annotations

import json
import os
import random
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
from spider.neardup import compute, duplicates_of, minhash  # noqa: E402

FAILS: list[str] = []


def check(cond: bool, label: str, extra: str = "") -> None:
    print(("  ok   " if cond else "  FAIL ") + label + (f"  ({extra})" if extra else ""))
    if not cond:
        FAILS.append(label)


WORDS = "payment bank merchant checkout fee card instant transfer refund invoice customer business open banking api secure fast settle account ledger".split()


def text(seed: int, n: int = 400) -> str:
    rnd = random.Random(seed)
    return " ".join(rnd.choice(WORDS) for _ in range(n))


def main() -> None:
    tmp = Path(os.environ.get("TEMP", ".")) / "claude" / "neardup_test.db"
    for suf in ("", "-wal", "-shm"):
        p = Path(str(tmp) + suf)
        if p.exists():
            p.unlink()
    con = dbm.connect(tmp)
    base = text(1)
    words = base.split()
    variant = " ".join(words[:380] + ["totally", "different", "ending", "words", "here"] * 4)        # ~95% shared shingles
    half = " ".join(words[:200] + text(2, 200).split())                                              # ~50% shared
    pages = [("https://t.test/a", base), ("https://t.test/b", variant), ("https://t.test/c", half), ("https://t.test/d", text(3)),
             ("https://t.test/short", "tiny page few words"), ("https://t.test/e", base)]
    for i, (u, t) in enumerate(pages, start=1):
        con.execute("INSERT INTO urls(id, url, is_internal, type, status_code, crawled, indexable, hash) VALUES (?,?,?,?,?,?,?,?)", (i, u, 1, "HTML", 200, 1, 1, f"h{i}"))
        con.execute("INSERT INTO bodies(url_id, text) VALUES (?,?)", (i, dbm.compress(t)))
    con.commit()
    check(minhash(base).jaccard(minhash(base)) == 1.0, "identical text -> similarity 1.0")
    sim_ab = minhash(base).jaccard(minhash(variant))
    check(0.8 < sim_ab < 1.0, "variant text -> high similarity", f"{sim_ab:.2f}")
    sim_ac = minhash(base).jaccard(minhash(half))
    check(0.3 < sim_ac < 0.7, "half-shared text -> medium similarity", f"{sim_ac:.2f}")

    n = compute(con, CrawlConfig(near_duplicate_threshold=90))
    rows = {r[0]: dict(r) for r in con.execute("SELECT url, near_dup_count, closest_similarity, closest_match FROM urls")}
    check(n >= 2, "pairs found at 90%", f"{n} pairs")
    check(rows["https://t.test/a"]["near_dup_count"] >= 2 and rows["https://t.test/a"]["closest_similarity"] == 100.0 and rows["https://t.test/a"]["closest_match"] == "https://t.test/e", "a: near dups b + e, closest = identical e")
    check(rows["https://t.test/c"]["near_dup_count"] == 0, "c (50%) not flagged at 90%")
    check(rows["https://t.test/d"]["near_dup_count"] == 0 and rows["https://t.test/short"]["near_dup_count"] == 0, "unrelated and tiny pages not flagged")
    d = duplicates_of(con, 1)
    check([x["url"] for x in d["near"]][:1] == ["https://t.test/e"] and d["near"][0]["similarity"] == 100.0, "duplicates_of lists near dups by similarity", str(d["near"]))
    n2 = compute(con, CrawlConfig(near_duplicate_threshold=30))
    check(n2 > n and con.execute("SELECT near_dup_count FROM urls WHERE url='https://t.test/c'").fetchone()[0] >= 1, "lower threshold flags the half-shared page too", f"{n2} pairs")
    con.close()
    for suf in ("", "-wal", "-shm"):
        p = Path(str(tmp) + suf)
        if p.exists():
            p.unlink()

    # real crawl through the API
    port = free_port()
    threading.Thread(target=lambda: uvicorn.run("spider.server:app", host="127.0.0.1", port=port, log_level="warning"), daemon=True).start()
    url = f"http://127.0.0.1:{port}"
    assert wait_ready(url)
    crawls = json.loads(urllib.request.urlopen(f"{url}/api/crawls").read())["crawls"]
    target = next(c for c in crawls if c["name"] == fixture_crawl().name)
    urllib.request.urlopen(urllib.request.Request(f"{url}/api/crawls/open", data=json.dumps({"path": target["path"]}).encode(), headers={"Content-Type": "application/json"})).read()
    t0 = time.time()
    urllib.request.urlopen(urllib.request.Request(f"{url}/api/analysis/run", data=b"{}", headers={"Content-Type": "application/json"})).read()
    el = time.time() - t0
    tabs = json.loads(urllib.request.urlopen(f"{url}/api/tabs").read())["tabs"]
    nd = next(f for t in tabs if t["key"] == "content" for f in t["filters"] if f["key"] == "content:near_duplicates")
    check(el < 60, "analysis with near-dups finishes in reasonable time", f"{el:.1f}s")
    check("90%" in nd["desc"], "filter description shows the configured threshold", nd["desc"][:60])
    print(f"  info near duplicates on {target['name']}: {nd['count']} pages")
    rows = json.loads(urllib.request.urlopen(f"{url}/api/urls?key=content:near_duplicates&size=5").read())["data"]
    if rows:
        det = json.loads(urllib.request.urlopen(f"{url}/api/url/{rows[0]['id']}").read())
        check(det["near_duplicates"] and det["near_duplicates"][0]["similarity"] >= 90, "URL details carry near duplicates with similarity", f"{det['near_duplicates'][0]['url']} {det['near_duplicates'][0]['similarity']}%")
        check(rows[0].get("closest_match"), "grid row has Closest Match URL column")
    else:
        print("  info no near duplicates in this crawl at 90% (nothing more to verify on real data)")

    from playwright.sync_api import sync_playwright
    out = str(Path(os.environ.get("TEMP", ".")) / "claude" / "dups_pane.png")
    with sync_playwright() as p:
        b = p.chromium.launch(); pg = b.new_page(viewport={"width": 1500, "height": 900})
        pg.goto(url); pg.wait_for_selector("#splash.hide", state="attached", timeout=20000); pg.wait_for_selector(".tabulator-row", timeout=20000)
        if rows:
            pg.click("#tabs button:has-text('Content')"); pg.select_option("#filter", "content:near_duplicates"); pg.wait_for_selector(".tabulator-row", timeout=20000); time.sleep(0.5)
        box = pg.query_selector(".tabulator-row").bounding_box(); pg.mouse.click(box["x"] + 150, box["y"] + box["height"] / 2)
        pg.click("#bottom-tabs button[data-pane='duplicates']"); pg.wait_for_function("document.querySelector('#bottom-body').innerText.includes('uplicates')", timeout=10000)
        txt = pg.inner_text("#bottom-body")
        check("Near duplicates" in txt or "No exact or near duplicates" in txt, "Duplicates pane renders", txt[:80].replace("\n", " | "))
        pg.screenshot(path=out)
        b.close()
    print("screenshot:", out)
    print(f"{len(FAILS)} FAILED: {FAILS}" if FAILS else "all checks passed")
    sys.exit(1 if FAILS else 0)


if __name__ == "__main__":
    main()
