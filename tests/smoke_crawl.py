"""Headless smoke test: crawl a site with a URL cap and print tab counts.

Usage: python tests/smoke_crawl.py URL [max_urls] [--js] [--sitemap] [--list]
"""
import asyncio
import sys
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from spider import db as dbm
from spider.config import CrawlConfig
from spider.crawler import Crawler
from spider.issues import all_filters, counts


async def main():
    args = [a for a in sys.argv[1:] if not a.startswith("--")]
    flags = {a for a in sys.argv[1:] if a.startswith("--")}
    url = args[0]
    cap = int(args[1]) if len(args) > 1 else 40
    out = Path(__file__).resolve().parent / "smoke.db"
    for p in (out, Path(str(out) + "-wal"), Path(str(out) + "-shm")):
        if p.exists():
            p.unlink()
    cfg = CrawlConfig(start_url=url, max_urls=cap, max_threads=5)
    if "--js" in flags:
        cfg.rendering = "javascript"
    if "--sitemap" in flags:
        cfg.mode = "sitemap"
        cfg.sitemap_urls = [url]
    if "--list" in flags:
        cfg.mode = "list"
        cfg.list_urls = [url]
    cr = Crawler(out, cfg)
    t = time.time()
    await cr.run()
    print(f"state={cr.state} crawled={cr.crawled} pages={cr.pages} enqueued={cr.enqueued} in {time.time()-t:.1f}s error={cr.error}")
    con = dbm.connect(out, readonly=True)
    c = counts(con, cfg)
    for f in all_filters(cfg):
        n = c.get(f["key"], 0)
        if n:
            print(f"{f['tab']:>16} | {f['name']:<48} {n}")
    print("--- sample rows")
    cols = "status_code, type, depth, indexability_status, title_len, word_count, inlinks, rendered, render_time, orig_word_count, js_links_count, js_errors, url"
    for r in con.execute(f"SELECT {cols} FROM urls WHERE crawled=1 ORDER BY id LIMIT 12"):
        print(dict(r))
    if "--js" in flags:
        print("--- render stats:", dict(con.execute("SELECT count(*) AS rendered, avg(render_time) AS avg_s, sum(js_errors) AS errors, sum(js_blocked) AS blocked FROM urls WHERE rendered=1").fetchone()))
        print("--- render failures:", con.execute("SELECT url, render_error FROM urls WHERE render_error IS NOT NULL AND render_error<>''").fetchall()[:5])
        print("--- screenshots stored:", con.execute("SELECT count(*) FROM bodies WHERE screenshot IS NOT NULL").fetchone()[0])


asyncio.run(main())
