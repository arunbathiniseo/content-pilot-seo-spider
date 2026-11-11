import json, sqlite3, re, zlib
from collections import Counter
from pathlib import Path

con = sqlite3.connect(str(Path(__file__).with_name("smoke.db")))
print("blocked resources:")
c = Counter()
for (t,) in con.execute("SELECT js_blocked_text FROM urls WHERE rendered=1"):
    for u in json.loads(t or "[]"):
        c[re.sub(r"\?.*", "", u)] += 1
for u, n in c.most_common(8):
    print(f"  {n:3} {u}")
print("console errors:")
c = Counter()
for (t,) in con.execute("SELECT js_errors_text FROM urls WHERE rendered=1"):
    for e in json.loads(t or "[]"):
        c[e[:110]] += 1
for e, n in c.most_common(8):
    print(f"  {n:3} {e}")

con2 = sqlite3.connect(str(Path(__file__).with_name("smoke2.db")))
print("\nuncrawlable reasons:", con2.execute("SELECT reason, count(*) FROM uncrawlable GROUP BY reason").fetchall())
print("uncrawlable samples:", [r for r in con2.execute("SELECT href, anchor FROM uncrawlable LIMIT 6")])
row = con2.execute("SELECT b.html FROM bodies b JOIN urls u ON u.id=b.url_id WHERE u.url_broken_bookmark=1 LIMIT 1").fetchone()
html = zlib.decompress(row[0]).decode("utf-8", "replace")
frags = Counter(re.findall(r'href="#([^"]+)"', html))
ids = set(re.findall(r'\sid="([^"]+)"', html))
print("fragments on page:", [(f, n, f in ids) for f, n in frags.most_common(12)])
print("protocol-relative sample:", re.findall(r'(?:src|href)="(//[^"]{0,60})', html)[:4])
