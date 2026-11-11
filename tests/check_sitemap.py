"""XML sitemap generation check: module on the newest big saved crawl (selection rules, options, XML validity, split
into index), then preview + download through the API and the modal headless.

    .venv\\Scripts\\python.exe tests\\check_sitemap.py
"""
from __future__ import annotations

import io
import json
import os
import sys
import threading
import time
import urllib.parse
import urllib.request
import zipfile
from pathlib import Path
from xml.etree import ElementTree as ET

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
from fixture import fixture_crawl, fixture_root  # noqa: E402,F401
import uvicorn  # noqa: E402

from spider import db as dbm  # noqa: E402
from spider.instance import free_port, wait_ready  # noqa: E402
from spider.sitemapgen import SitemapOptions, build, select, to_zip  # noqa: E402

FAILS: list[str] = []
NS = {"s": "http://www.sitemaps.org/schemas/sitemap/0.9", "image": "http://www.google.com/schemas/sitemap-image/1.1"}


def check(cond: bool, label: str, extra: str = "") -> None:
    print(("  ok   " if cond else "  FAIL ") + label + (f"  ({extra})" if extra else ""))
    if not cond:
        FAILS.append(label)


def main() -> None:
    db = fixture_crawl()
    site = fixture_root(db)
    con = dbm.connect(db, readonly=True)
    rows, ex = select(con, SitemapOptions())
    total_html = con.execute("SELECT count(*) FROM urls WHERE crawled=1 AND is_internal=1 AND type='HTML'").fetchone()[0]
    check(rows and len(rows) + sum(ex.values()) == total_html, "default selection + exclusions = all internal HTML", f"{len(rows)} in, {ex}")
    check(all(200 <= r["status_code"] < 300 and r["indexable"] == 1 for r in rows), "defaults: only 2xx indexable")
    rows2, ex2 = select(con, SitemapOptions(include_noindex=True, include_canonicalised=True, include_paginated=True, only_2xx=False))
    rows3, _ = select(con, SitemapOptions(include_noindex=True, include_canonicalised=True))   # what the modal shows after ticking those two
    check(len(rows2) == total_html, "everything included when all options are on", str(len(rows2)))
    files, summary = build(con, SitemapOptions(), site)
    check(len(files) == 1 and files[0][0] == "sitemap.xml", "single file for a small crawl")
    root = ET.fromstring(files[0][1])
    urls = root.findall("s:url", NS)
    check(len(urls) == summary["urls"] == len(rows), "XML parses, one <url> per row", str(len(urls)))
    first = urls[0]
    check(first.find("s:loc", NS) is not None and first.find("s:lastmod", NS) is not None and first.find("s:priority", NS).text == "1.0" and first.find("s:changefreq", NS).text == "daily", "home page: lastmod, priority 1.0, changefreq daily")
    lm = first.find("s:lastmod", NS).text
    check(len(lm) >= 10 and lm[4] == "-" and "T" in lm, "lastmod is W3C datetime", lm)
    files, _ = build(con, SitemapOptions(lastmod=False, priority=False, changefreq=False), site)
    root = ET.fromstring(files[0][1])
    check(root.find("s:url/s:lastmod", NS) is None and root.find("s:url/s:priority", NS) is None, "options off -> elements omitted")
    files, s_img = build(con, SitemapOptions(images=True), site)
    root = ET.fromstring(files[0][1])
    n_img = len(root.findall("s:url/image:image/image:loc", NS))
    check(n_img > 0 and 'xmlns:image="http://www.google.com/schemas/sitemap-image/1.1"' in files[0][1][:300].decode(), "image sitemap entries with the image namespace", f"{n_img} images")
    files, s_split = build(con, SitemapOptions(max_urls_per_file=5), site)
    names = [n for n, _ in files]
    check(names[-1] == "sitemap_index.xml" and len(files) == -(-len(rows) // 5) + 1, "split into numbered files + index", str(names))
    idx = ET.fromstring(files[-1][1])
    locs = [e.text for e in idx.findall("s:sitemap/s:loc", NS)]
    check(locs[0] == site + "/sitemap1.xml" and len(locs) == len(files) - 1, "index lists every part with the site root", locs[0])
    z = zipfile.ZipFile(io.BytesIO(to_zip(files)))
    check(sorted(z.namelist()) == sorted(names), "zip contains all files")
    con.close()

    # API + modal
    port = free_port()
    threading.Thread(target=lambda: uvicorn.run("spider.server:app", host="127.0.0.1", port=port, log_level="warning"), daemon=True).start()
    url = f"http://127.0.0.1:{port}"
    assert wait_ready(url)
    urllib.request.urlopen(urllib.request.Request(f"{url}/api/crawls/open", data=json.dumps({"path": str(db)}).encode(), headers={"Content-Type": "application/json"})).read()
    prev = json.loads(urllib.request.urlopen(f"{url}/api/sitemap/preview").read())
    check(prev["urls"] == len(rows) and prev["excluded"] == ex, "api preview matches module", str(prev["urls"]))
    prev2 = json.loads(urllib.request.urlopen(f"{url}/api/sitemap/preview?include_noindex=true&include_canonicalised=true&include_paginated=true&only_2xx=false").read())
    check(prev2["urls"] == total_html, "api preview honours options")
    r = urllib.request.urlopen(f"{url}/api/sitemap/generate")
    body = r.read()
    check(r.headers["Content-Type"].startswith("application/xml") and body.startswith(b'<?xml') and r.headers["X-Sitemap-URLs"] == str(len(rows)), "api download: xml with URL count header", r.headers.get("Content-Disposition", ""))
    from playwright.sync_api import sync_playwright
    out = str(Path(os.environ.get("TEMP", ".")) / "claude" / "sitemap_modal.png")
    with sync_playwright() as p:
        b = p.chromium.launch(); pg = b.new_page(viewport={"width": 1400, "height": 900})
        pg.goto(url); pg.wait_for_selector("#splash.hide", state="attached", timeout=20000); pg.wait_for_selector(".tabulator-row", timeout=20000); time.sleep(0.5)
        pg.click("#btn-export"); pg.click("#export-menu a[data-export='sitemap']"); pg.wait_for_function("document.querySelector('#sm-preview') && /URLs/.test(document.querySelector('#sm-preview').textContent)", timeout=10000)
        t1 = pg.inner_text("#sm-preview")
        pg.check("#sm-opts [data-o='include_noindex']"); pg.check("#sm-opts [data-o='include_canonicalised']"); time.sleep(0.8)
        t2 = pg.inner_text("#sm-preview")
        check(str(len(rows)) in t1.replace(",", "") and "URLs" in t2 and str(len(rows3)) in t2.replace(",", ""),
              "modal preview re-counts with options", t2.split("\n")[0][:80])
        check("include_noindex=true" in pg.get_attribute("#sm-download", "href"), "download link carries the options")
        pg.screenshot(path=out)
        b.close()
    print("screenshot:", out)
    print(f"{len(FAILS)} FAILED: {FAILS}" if FAILS else "all checks passed")
    sys.exit(1 if FAILS else 0)


if __name__ == "__main__":
    main()
