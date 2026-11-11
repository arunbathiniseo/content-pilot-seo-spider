"""Internal links endpoints check: the tokenizer, the block walker, the graph, mentions and the one-page fetch,
against tests/smoke.db and a page served from this process.

    .venv\\Scripts\\python.exe tests\\check_links.py
"""
from __future__ import annotations

import http.server
import os
import sys
import threading
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
os.environ.setdefault("SPIDER_CRAWLS_DIR", str(ROOT / "tests"))

from fastapi.testclient import TestClient  # noqa: E402

from spider import links as L  # noqa: E402
from spider import server  # noqa: E402
from spider.instance import free_port  # noqa: E402

FAILS: list[str] = []


def check(cond: bool, label: str, extra: str = "") -> None:
    print(("  ok   " if cond else "  FAIL ") + label + (f"  ({extra})" if extra else ""))
    if not cond:
        FAILS.append(label)


PAGE = b"""<!doctype html><html><head><title>Crawling guide</title></head><body>
<nav><a href="/website-crawler">website crawler</a> in the menu</nav>
<main><h1>What a website crawler does</h1>
<p>Before you fix anything, run a <a href="/website-crawler">website crawler</a> over every URL. A site crawler's report lists broken pages.</p>
<ul><li>Redirect chains: a redirect chain checker follows each hop.</li><li><p>Nested paragraph with crawl budget notes.</p></li></ul>
<blockquote>Quote mentioning the website crawler.</blockquote>
<p>Merchant Category Codes explain the categories; MCC code lists are long.</p>
</main><footer><p>Footer says website crawler too.</p></footer></body></html>"""


class H(http.server.BaseHTTPRequestHandler):
    def do_GET(self):  # noqa: N802
        self.send_response(200)
        self.send_header("Content-Type", "text/html; charset=utf-8")
        self.end_headers()
        self.wfile.write(PAGE)

    def log_message(self, *a):  # noqa: D102
        pass


def main() -> None:
    print("tokenizer")
    check(L.tokens("Merchant Category Codes") == ["merchant", "category", "code"], "plurals stemmed")
    check(L.tokens("what is an MCC code's use") == ["what", "is", "an", "mcc", "code", "use"], "possessive dropped", str(L.tokens("what is an MCC code's use")))
    check(L.phrase_tokens("what is a website crawler") == ["website", "crawler"], "stop words forgiven")
    check(L.phrase_tokens("the a of") == ["the", "a", "of"], "all-stop phrase keeps its words")

    print("blocks")
    blocks = L.blocks_of(PAGE.decode())
    kinds = [b.kind for b in blocks]
    texts = [b.text for b in blocks]
    check("Footer says website crawler too." not in texts and not any("in the menu" in t for t in texts), "nav and footer left out", str(texts))
    check(kinds[0] == "heading", "h1 is a heading block", str(kinds))
    check(any(t.startswith("Nested paragraph") for t in texts) and not any("Nested paragraph" in t and t.startswith("Redirect") for t in texts), "nested p is its own block")
    hits = L.find_in_blocks(blocks, L.phrase_tokens("website crawler"), max_hits=10)
    check(len(hits) == 3, "three content mentions of the phrase", str([(h["kind"], h["in_link"]) for h in hits]))
    check(hits[0]["kind"] == "heading" and not hits[0]["linkable"], "the heading hit is not linkable")
    check(hits[1]["kind"] == "paragraph" and hits[1]["in_link"] and not hits[1]["linkable"], "the linked mention is marked in_link")
    check(hits[2]["kind"] == "quote" and not hits[2]["linkable"], "the quote hit is not linkable")
    sc = L.find_in_blocks(blocks, L.phrase_tokens("site crawlers"))
    check(len(sc) == 1 and sc[0]["linkable"] and sc[0]["anchor"] == "site crawler", "variant found before its possessive, linkable", str(sc))
    check("A site crawler's report lists broken pages." == sc[0]["sentence"], "sentence around the hit", sc[0]["sentence"])
    mcc = L.find_in_blocks(blocks, L.phrase_tokens("merchant category code"))
    check(len(mcc) == 1 and mcc[0]["anchor"] == "Merchant Category Codes", "case and plural forgiven in the anchor text", str(mcc))

    print("graph and mentions on tests/smoke.db")
    c = TestClient(server.app, base_url="http://127.0.0.1")   # the desktop app answers to this machine's own address only
    g = c.get("/api/graph", params={"crawl": "smoke.db", "size": 50}).json()
    check(g["total_pages"] > 0 and len(g["pages"]) <= 50 and "link_score" in g["pages"][0], "graph pages with link_score", str(g["total_pages"]))
    check(all({"from_id", "to_id", "anchor", "position", "follow"} <= set(l) for l in g["links"]), "links carry anchor, position, follow", str(len(g["links"])))
    # a phrase from a real page title in the crawl
    con = server.dbm.connect(ROOT / "tests" / "smoke.db", readonly=True)
    row = con.execute("SELECT u.id, u.title FROM urls u JOIN bodies b ON b.url_id=u.id WHERE u.title IS NOT NULL AND length(u.title)>8 ORDER BY u.inlinks DESC LIMIT 1").fetchone()
    con.close()
    words = [w for w in L.tokens(row["title"]) if w not in L.STOP][:2]
    m = c.post("/api/mentions", json={"crawl": "smoke.db", "phrases": [{"id": "t1", "text": " ".join(words), "target_id": row["id"], "variants": []}], "per_phrase": 5}).json()
    check(m["scanned"] > 0 and len(m["mentions"]) == 1, "mentions scanned the crawl", f"scanned {m['scanned']} pages for {' '.join(words)!r}")
    pages = m["mentions"][0]["pages"]
    check(all(p["url_id"] != row["id"] for p in pages), "the target page is never its own source")
    check(all(h["sentence"] and h["anchor"] for p in pages for h in p["hits"]), "every hit carries anchor and sentence")
    e = c.post("/api/mentions", json={"crawl": "smoke.db", "phrases": []}).json()
    check(e == {"scanned": 0, "mentions": []}, "no phrases, no scan")

    print("one-page fetch")
    port = free_port()
    srv = http.server.HTTPServer(("127.0.0.1", port), H)
    threading.Thread(target=srv.serve_forever, daemon=True).start()
    r = c.post("/api/crawl/url", json={"url": f"http://127.0.0.1:{port}/guide", "expect": [{"href": "/website-crawler", "anchor": "website crawler"}, {"href": "/pricing", "anchor": "pricing"}]}).json()
    srv.shutdown()
    check(r["status_code"] == 200 and len(r["hyperlinks"]) >= 1, "page fetched with its hyperlinks", str(r.get("error")))
    check(r["expect"][0]["found"] is True and r["expect"][1]["found"] is False, "expected link found, missing link reported", str(r["expect"]))

    print("auth")
    os.environ["SPIDER_API_KEY"] = "k"
    try:
        check(c.get("/api/graph", params={"crawl": "smoke.db"}).status_code == 401, "key required when set")
        check(c.get("/api/graph", params={"crawl": "smoke.db"}, headers={"X-Spider-Key": "k"}).status_code == 200, "key accepted")
    finally:
        del os.environ["SPIDER_API_KEY"]

    print(f"\n{len(FAILS)} failures" if FAILS else "\nall ok")
    sys.exit(1 if FAILS else 0)


if __name__ == "__main__":
    main()
