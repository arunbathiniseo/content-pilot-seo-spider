"""AMP checks: page-level validation through the parser, cross-page analysis on a synthetic crawl, filters.

    .venv\\Scripts\\python.exe tests\\check_amp.py
"""
from __future__ import annotations

import json
import os
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from spider import db as dbm  # noqa: E402
from spider.amp import analyse_crawl  # noqa: E402
from spider.config import CrawlConfig  # noqa: E402
from spider.issues import TABS, all_filters, counts, rebuild_issue_table  # noqa: E402
from spider.parse import parse_html  # noqa: E402

FAILS: list[str] = []


def check(cond: bool, label: str, extra: str = "") -> None:
    print(("  ok   " if cond else "  FAIL ") + label + (f"  ({extra})" if extra else ""))
    if not cond:
        FAILS.append(label)


VALID_AMP = """<!doctype html><html amp lang="en"><head><meta charset="utf-8"><script async src="https://cdn.ampproject.org/v0.js"></script>
<title>AMP</title><link rel="canonical" href="https://x.test/page/"><meta name="viewport" content="width=device-width,minimum-scale=1">
<style amp-boilerplate>body{-webkit-animation:-amp-start 8s steps(1,end) 0s 1 normal both}</style><noscript><style amp-boilerplate>body{-webkit-animation:none}</style></noscript>
<style amp-custom>h1{color:red}</style><script type="application/ld+json">{"@type":"Article","headline":"x"}</script></head>
<body><h1>Hello</h1><amp-img src="a.jpg" width="1" height="1"></amp-img></body></html>"""
BAD_AMP = """<html ⚡><head><title>Bad</title><script src="/app.js"></script><style>p{color:blue}</style></head>
<body><img src="a.jpg"><iframe src="x"></iframe><video src="v.mp4"></video><p style="color:red !important">x</p></body></html>"""
CANON = """<!doctype html><html><head><title>Page</title><link rel="amphtml" href="https://x.test/amp/page/"><link rel="canonical" href="https://x.test/page/"></head><body>hi</body></html>"""


def main() -> None:
    cfg = CrawlConfig()
    f = parse_html(VALID_AMP, "https://x.test/amp/page/", {}, cfg).fields
    check(f["is_amp"] == 1 and f["amp_issue_count"] == 0, "valid AMP page: no issues", f.get("amp_issues", ""))
    f = parse_html(BAD_AMP, "https://x.test/amp/bad/", {}, cfg).fields
    msgs = json.loads(f["amp_issues"])
    check(f["is_amp"] == 1 and f["amp_missing_script"] == 1 and f["amp_missing_boilerplate"] == 1 and f["amp_missing_canonical"] == 1, "bad AMP page: missing script / boilerplate / canonical")
    check(f["amp_disallowed_html"] == 4 and any("<img>" in m and "<iframe>" in m for m in msgs) and any("Custom JavaScript" in m for m in msgs), "disallowed HTML + custom JS counted", str(f["amp_disallowed_html"]))
    check(f["amp_other_errors"] >= 4 and any("doctype" in m.lower() for m in msgs) and any("viewport" in m for m in msgs) and any("!important" in m for m in msgs) and any("<style> without" in m for m in msgs), "doctype / charset / viewport / style / !important flagged", f"{f['amp_other_errors']} other")
    f = parse_html(CANON, "https://x.test/page/", {}, cfg).fields
    check(f["is_amp"] == 0 and f["amp_url"] == "https://x.test/amp/page/" and f["amp_issue_count"] == 0, "non-AMP page: amphtml link recorded, no AMP flags")

    # cross-page analysis on a synthetic crawl
    tmp = Path(os.environ.get("TEMP", ".")) / "claude" / "amp_test.db"
    for suf in ("", "-wal", "-shm"):
        p = Path(str(tmp) + suf)
        if p.exists():
            p.unlink()
    con = dbm.connect(tmp)
    rows = [
        # id, url, is_amp, canonical, amp_url, status, indexable
        (1, "https://x.test/page/", 0, "https://x.test/page/", "https://x.test/amp/page/", 200, 1),
        (2, "https://x.test/amp/page/", 1, "https://x.test/page/", "", 200, 0),             # good AMP page
        (3, "https://x.test/broken/", 0, "https://x.test/broken/", "https://x.test/amp/broken/", 200, 1),
        (4, "https://x.test/amp/broken/", 1, "", "", 404, 0),                               # non-200 AMP
        (5, "https://x.test/noamp/", 0, "https://x.test/noamp/", "https://x.test/amp/noamp/", 200, 1),
        (6, "https://x.test/amp/noamp/", 0, "https://x.test/amp/noamp/", "", 200, 1),       # linked as AMP, no <html amp>
        (7, "https://x.test/amp/self/", 1, "https://x.test/amp/self/", "", 200, 1),         # AMP canonical to itself -> indexable, missing return
        (8, "https://x.test/amp/tononidx/", 1, "https://x.test/gone/", "", 200, 0),
        (9, "https://x.test/gone/", 0, "", "", 404, 0),
    ]
    for r in rows:
        con.execute("INSERT INTO urls(id, url, is_internal, type, crawled, is_amp, canonical, amp_url, status_code, indexable) VALUES (?,?,?,?,?,?,?,?,?,?)",
                    (r[0], r[1], 1, "HTML", 1, r[2], r[3], r[4], r[5], r[6]))
    con.commit()
    analyse_crawl(con)
    flags = {r["url"]: dict(r) for r in con.execute("SELECT url, amp_non200, amp_missing_return, amp_canonical_nonindexable, amp_indexable, amp_missing_html_tag FROM urls")}
    check(flags["https://x.test/broken/"]["amp_non200"] == 1 and flags["https://x.test/page/"]["amp_non200"] == 0, "non-200 AMP response flagged on the linking page")
    check(flags["https://x.test/amp/noamp/"]["amp_missing_html_tag"] == 1, "linked AMP URL without <html amp> flagged")
    check(flags["https://x.test/amp/self/"]["amp_missing_return"] == 1 and flags["https://x.test/amp/self/"]["amp_indexable"] == 1, "self-canonical AMP: missing return link + indexable")
    check(flags["https://x.test/amp/page/"]["amp_missing_return"] == 0 and flags["https://x.test/amp/page/"]["amp_indexable"] == 0, "good AMP page clean")
    check(flags["https://x.test/amp/tononidx/"]["amp_canonical_nonindexable"] == 1, "canonical to a 404 flagged")
    # filters
    assert ("amp", "AMP") in TABS
    rebuild_issue_table(con, cfg); con.commit()
    c = counts(con, cfg)
    check(c.get("amp:all") == 7, "AMP tab 'All' = AMP pages + pages linking to AMP (the page without <html amp> is not AMP)", str(c.get("amp:all")))
    check(c.get("amp:non_200") == 1 and c.get("amp:missing_html_tag") == 1 and c.get("amp:indexable_amp") == 1 and c.get("amp:nonindexable_canonical") == 1, "filter counts", str({k: v for k, v in c.items() if k.startswith('amp:')}))
    names = {f["key"]: f["name"] for f in all_filters(cfg) if f["tab"] == "amp"}
    check(len(names) == 11, "11 AMP filters incl. All", str(len(names)))
    con.close()
    for suf in ("", "-wal", "-shm"):
        p = Path(str(tmp) + suf)
        if p.exists():
            p.unlink()
    print(f"{len(FAILS)} FAILED: {FAILS}" if FAILS else "all checks passed")
    sys.exit(1 if FAILS else 0)


if __name__ == "__main__":
    main()
