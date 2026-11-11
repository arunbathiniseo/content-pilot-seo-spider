"""Prominent words per page (spider/prominent.py), for the app's topic-overlap evidence: the weighting of the head
fields, the minimum occurrences, the top cap, the stop words, and the per-page scan of tests/smoke.db.

    .venv\\Scripts\\python.exe tests\\check_prominent.py
"""
from __future__ import annotations

import os
import sqlite3
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
os.environ.setdefault("SPIDER_CRAWLS_DIR", str(ROOT / "tests"))

from spider import prominent as P  # noqa: E402

FAILS: list[str] = []


def check(cond: bool, label: str, extra: str = "") -> None:
    print(("  ok   " if cond else "  FAIL ") + label + (f"  ({extra})" if extra else ""))
    if not cond:
        FAILS.append(label)


def main() -> int:
    text = ("A website crawler reads every page. The crawler follows links and the crawler stores the pages. "
            "Crawlers report broken pages and redirect chains; a redirect chain checker follows each hop. "
            "The report lists the pages with the most links.") * 1
    terms = P.page_terms(text, ["Website crawler guide", "What a website crawler does", None, "Redirect chains"], min_occ=4, top=100)
    d = dict((t, n) for t, n in terms)
    check("crawler" in d, "a repeated body word is prominent", str(terms[:6]))
    check(d.get("website", 0) >= 3 * 2, "title and H1 words count three times each", str(d.get("website")))
    check("the" not in d and "a" not in d, "stop words never count")
    check(all(n >= 4 for _, n in terms), "every kept term has at least four occurrences")
    check(terms == sorted(terms, key=lambda r: (-r[1], r[0])), "terms are ordered by weight, then alphabetically")
    check(P.page_terms(text, [], min_occ=4, top=1) == terms[:1] or len(P.page_terms(text, [], min_occ=4, top=1)) <= 1, "top caps the list")
    check(P.page_terms("", [None, None], 4, 100) == [], "an empty page has no terms")

    db = ROOT / "tests" / "smoke.db"
    if db.exists():
        con = sqlite3.connect(str(db))
        con.row_factory = sqlite3.Row
        out = P.prominent(con, min_occ=2, top=50, min_words=1)
        con.close()
        check("pages" in out and isinstance(out["pages"], list), "prominent() answers pages for the smoke crawl", str(len(out.get("pages", []))))
        for pg in out["pages"][:3]:
            check(set(pg) == {"url_id", "url", "terms"} and all(len(t) == 2 for t in pg["terms"]), f"page row shape for {pg.get('url', '?')[:40]}")
        check(all(len(pg["terms"]) <= 50 for pg in out["pages"]), "top is honoured per page")
    else:
        print("  skip smoke.db not present")
    print("\nFAILED: " + "; ".join(FAILS) if FAILS else "\nALL CHECKS PASSED")
    return 1 if FAILS else 0


if __name__ == "__main__":
    sys.exit(main())
