import sqlite3, sys, glob, os
from pathlib import Path

files = sorted(glob.glob(str(Path(__file__).resolve().parents[1] / "crawls" / "*.db")), key=os.path.getmtime)
con = sqlite3.connect(sys.argv[1] if len(sys.argv) > 1 else files[-1])
print("indexability (internal):")
for r in con.execute("SELECT type, crawled, indexable, indexability_status, count(*) FROM urls WHERE is_internal=1 AND crawled IN (1,3) GROUP BY 1,2,3,4 ORDER BY 5 DESC"):
    print("  ", r)
print("blocked:", con.execute("SELECT url FROM urls WHERE crawled=3").fetchall())
print("redirects:", con.execute("SELECT url, status_code, redirect_url, redirect_chain_len FROM urls WHERE redirect_url<>''").fetchall())
print("bad content type:", con.execute("SELECT url, content_type FROM urls WHERE bad_content_type=1").fetchall()[:6])
print("broken bookmark:", con.execute("SELECT url FROM urls WHERE url_broken_bookmark=1").fetchall())
print("external 4xx:", con.execute("SELECT url, status_code FROM urls WHERE is_internal=0 AND status_code>=400").fetchall())
print("timing: analysis rows", con.execute("SELECT count(*) FROM url_issues").fetchone())
