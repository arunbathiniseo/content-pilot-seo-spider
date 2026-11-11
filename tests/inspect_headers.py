import json, sqlite3, sys
from pathlib import Path

db = sys.argv[1] if len(sys.argv) > 1 else str(Path(__file__).with_name("smoke.db"))
con = sqlite3.connect(db)
r = con.execute("SELECT url, status_code, headers, cookies FROM urls WHERE crawled=1 AND type='HTML' AND status_code=200 LIMIT 1").fetchone()
print(r[0], r[1])
for k, v in json.loads(r[2]).items():
    print(f"  {k}: {v[:90]}")
print("cookies:", r[3])
print("links:", con.execute("SELECT kind, count(*) FROM links GROUP BY kind").fetchall())
print("positions:", con.execute("SELECT position, count(*) FROM links WHERE kind='hyperlink' GROUP BY position").fetchall())
print("types:", con.execute("SELECT type, crawled, count(*) FROM urls GROUP BY type, crawled").fetchall())
