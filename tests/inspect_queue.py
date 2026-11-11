"""What is in the crawl? Breakdown of discovered URLs by type / host / pattern. Usage: check_queue.py [db]"""
import glob, os, re, sqlite3, sys
from collections import Counter
from pathlib import Path
from urllib.parse import urlsplit

files = sorted(glob.glob(str(Path(__file__).resolve().parents[1] / "crawls" / "*.db")), key=os.path.getmtime)
db = sys.argv[1] if len(sys.argv) > 1 else files[-1]
print("db:", db)
con = sqlite3.connect(f"file:{Path(db).as_posix()}?mode=ro", uri=True)
print("\nby crawled-state / internal / type:")
for r in con.execute("SELECT crawled, is_internal, type, count(*) FROM urls GROUP BY 1,2,3 ORDER BY 4 DESC LIMIT 25"):
    print("  ", r)
print("\nqueued (crawled=0) by host:")
hosts = Counter(urlsplit(r[0]).hostname for r in con.execute("SELECT url FROM urls WHERE crawled=0"))
for h, n in hosts.most_common(15):
    print(f"   {n:6}  {h}")
print("\nqueued internal by first path segment:")
segs = Counter()
params = Counter()
for (u,) in con.execute("SELECT url FROM urls WHERE crawled=0 AND is_internal=1"):
    p = urlsplit(u)
    segs["/" + (p.path.split("/")[1] if len(p.path.split("/")) > 1 else "")] += 1
    for k in re.findall(r"[?&]([^=&]+)=", "?" + p.query):
        params[k] += 1
for s, n in segs.most_common(20):
    print(f"   {n:6}  {s}")
print("\nqueued internal query parameters:")
for s, n in params.most_common(15):
    print(f"   {n:6}  {s}")
print("\nsample queued internal URLs:")
for (u,) in con.execute("SELECT url FROM urls WHERE crawled=0 AND is_internal=1 ORDER BY random() LIMIT 25"):
    print("   ", u)
print("\nsitemap URLs:", con.execute("SELECT count(*) FROM sitemap_urls").fetchone()[0], " sitemaps:", con.execute("SELECT count(*) FROM sitemaps").fetchone()[0])
print("start:", con.execute("SELECT value FROM meta WHERE key='start_url'").fetchone())
