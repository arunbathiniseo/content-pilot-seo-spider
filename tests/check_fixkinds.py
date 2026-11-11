"""Evidence-based fixes: synthetic pages with known problems, then the real crawl through the API (no AI key).

    .venv\\Scripts\\python.exe tests\\check_fixkinds.py
"""
from __future__ import annotations

import asyncio
import json
import os
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
from spider.fixes import FixJob, generate, kind_for  # noqa: E402
from spider.fixkinds import clean_url, evidence, rule_fix, site_fix  # noqa: E402
from spider.instance import free_port, wait_ready  # noqa: E402
from spider.issues import rebuild_issue_table  # noqa: E402
from spider.parse import parse_html  # noqa: E402

FAILS: list[str] = []


def check(cond: bool, label: str, extra: str = "") -> None:
    print(("  ok   " if cond else "  FAIL ") + label + (f"  ({extra})" if extra else ""))
    if not cond:
        FAILS.append(label)


def post(url: str, body: dict) -> dict:
    r = urllib.request.urlopen(urllib.request.Request(url, data=json.dumps(body).encode(), headers={"Content-Type": "application/json"}))
    return json.loads(r.read())


PAGE = """<!doctype html><html><head><title>One</title><title>Two</title><link rel="canonical" href="/page/"><meta name="viewport" content="width=device-width">
<script src="//cdn.example.com/app.js"></script><link rel="stylesheet" href="http://static.example.com/site.css"></head>
<body><h1>A</h1><h1>B</h1><img src="//cdn.example.com/hero.jpg"><a href="https://other.example/x" target="_blank">Partner</a>
<a href="#pricing">See pricing</a><div id="features">f</div><form action="http://forms.example.com/go" method="post"></form>
<style>.bg{background:url(//cdn.example.com/bg.png)}</style></body></html>"""


def main() -> None:
    cfg = CrawlConfig()
    check(kind_for("security:protocol_relative") == "code" and kind_for("security:missing_hsts") == "site" and kind_for("url:uppercase") == "url"
          and kind_for("response_codes:int_redirect_chain") == "redirect_link" and kind_for("page_titles:over_chars") == "title" and kind_for("content:low_content") == "brief", "filter -> fix kind mapping")
    check(clean_url("https://X.test/Blog//My_Post%20Here/?utm_source=x&page=2", cfg) == "https://x.test/blog/my-post-here/?page=2", "clean_url", clean_url("https://X.test/Blog//My_Post%20Here/?utm_source=x&page=2", cfg))

    tmp = Path(os.environ.get("TEMP", ".")) / "claude" / "fixkinds_test.db"
    for suf in ("", "-wal", "-shm"):
        p = Path(str(tmp) + suf)
        if p.exists():
            p.unlink()
    con = dbm.connect(tmp)
    pd = parse_html(PAGE, "https://x.test/page/", {"server": "nginx"}, cfg)
    f = pd.fields
    con.execute("INSERT INTO urls(id, url, is_internal, type, status_code, crawled, indexable, headers, depth) VALUES (1,'https://x.test/page/',1,'HTML',200,1,1,?,0)", (json.dumps({"server": "nginx"}),))
    dbm.update_url(con, 1, f)
    con.execute("INSERT INTO bodies(url_id, html) VALUES (1, ?)", (dbm.compress(PAGE),))
    # a redirecting URL with inlinks, an ugly URL, an oversized image
    con.execute("INSERT INTO urls(id, url, is_internal, type, status_code, crawled, redirect_url, final_url, final_status, redirect_chain_len) VALUES (2,'https://x.test/old/',1,'HTML',301,1,'https://x.test/mid/','https://x.test/new/',200,2)")
    con.execute("INSERT INTO urls(id, url, is_internal, type, status_code, crawled, indexable, url_uppercase) VALUES (3,'https://x.test/Blog/My_Post/',1,'HTML',200,1,1,1)")
    con.execute("INSERT INTO urls(id, url, is_internal, type, status_code, crawled, size, img_width, img_height, content_type) VALUES (4,'https://x.test/big.jpg',1,'Images',200,1,480000,2400,1600,'image/jpeg')")
    con.executemany("INSERT INTO links(from_id, to_id, kind, anchor, has_size_attrs) VALUES (?,?,?,?,?)", [(1, 2, "hyperlink", "old page", 1), (3, 2, "hyperlink", "see", 1), (1, 4, "image", "", 0), (1, 3, "hyperlink", "blog", 1)])
    con.commit()
    check(f["protocol_relative"] == 1 and f["mixed_content"] == 1 and f["unsafe_cross_origin"] == 1 and f["form_insecure"] == 1 and f["url_broken_bookmark"] == 1, "parser flags the synthetic page")

    ev = evidence(con, cfg, "security:protocol_relative", 1)
    fx = rule_fix(con, cfg, "security:protocol_relative", 1, ev)
    check(len(ev["items"]) == 3 and {i["value"] for i in ev["items"]} == {"//cdn.example.com/app.js", "//cdn.example.com/hero.jpg", "//cdn.example.com/bg.png"}, "protocol-relative evidence lists the exact resources (script, img, css url())", str([i["value"] for i in ev["items"]]))
    check(fx and "https://cdn.example.com/app.js" in fx[2] and "3 protocol-relative" in fx[2], "protocol-relative proposal with before -> after")
    ev = evidence(con, cfg, "security:mixed_content", 1); fx = rule_fix(con, cfg, "security:mixed_content", 1, ev)
    check(ev["items"][0]["value"].startswith("http://static") and "https://static.example.com/site.css" in fx[2] and "upgrade-insecure-requests" in fx[2], "mixed content proposal")
    ev = evidence(con, cfg, "security:unsafe_cross_origin", 1); fx = rule_fix(con, cfg, "security:unsafe_cross_origin", 1, ev)
    check(ev["items"][0]["anchor"] == "Partner" and 'rel="noopener noreferrer"' in fx[2], "unsafe cross-origin proposal names the link")
    ev = evidence(con, cfg, "url:broken_bookmark", 1); fx = rule_fix(con, cfg, "url:broken_bookmark", 1, ev)
    check(ev["items"][0]["fragment"] == "pricing" and "features" in fx[2], "broken bookmark: missing id named, existing ids listed")
    ev = evidence(con, cfg, "security:form_insecure", 1); fx = rule_fix(con, cfg, "security:form_insecure", 1, ev)
    check("https://forms.example.com/go" in fx[2], "insecure form action proposal")
    ev = evidence(con, cfg, "page_titles:multiple", 1); fx = rule_fix(con, cfg, "page_titles:multiple", 1, ev)
    check(len(ev["elements"]) == 2 and "Two" in fx[2] and "Keep one <title>" in fx[2], "multiple titles listed")
    ev = evidence(con, cfg, "canonicals:relative", 1); fx = rule_fix(con, cfg, "canonicals:relative", 1, ev)
    check('href="https://x.test/page/"' in fx[2], "relative canonical made absolute", fx[2][:80])
    ev = evidence(con, cfg, "response_codes:int_redirect_chain", 2); fx = rule_fix(con, cfg, "response_codes:int_redirect_chain", 2, ev)
    check(ev["inlink_count"] == 2 and "https://x.test/new/" in fx[2] and "chain of 2" in fx[2] and "https://x.test/page/" in fx[2], "redirect chain: final URL + the pages to update")
    ev = evidence(con, cfg, "url:uppercase", 3); fx = rule_fix(con, cfg, "url:uppercase", 3, ev)
    check(fx and "https://x.test/blog/my-post/" in fx[2] and "301" in fx[2], "URL rewrite proposal")
    ev = evidence(con, cfg, "images:over_size", 4); fx = rule_fix(con, cfg, "images:over_size", 4, ev)
    check(fx and "468.8 KB" in fx[2] and "2400×1600" in fx[2] and "WebP" in fx[2], "oversized image proposal with size and dimensions", fx[2][:70])
    ev = evidence(con, cfg, "images:missing_size", 4); fx = rule_fix(con, cfg, "images:missing_size", 4, ev)
    check('width="2400" height="1600"' in fx[2], "missing size attributes: exact dimensions")
    s = site_fix(con, CrawlConfig(start_url="https://x.test/"), "security:missing_hsts", [1, 2, 3])
    check(s[0] == "https://x.test/" and "Strict-Transport-Security" in s[3] and "add_header" in s[3] and "3 URLs" in s[2], "site-level header fix with nginx snippet", s[3][:80])
    s = site_fix(con, CrawlConfig(start_url="https://x.test/"), "security:http_urls", [1])
    check("301" in s[3] and "Always Use HTTPS" in s[3], "site-level HTTP -> HTTPS fix")

    # engine: rule kinds need no API key; site kind creates exactly one proposal
    rebuild_issue_table(con, cfg); con.commit()
    job = FixJob(); asyncio.run(generate(con, cfg, "security:protocol_relative", None, job))
    check(job.state == "done" and job.created == 1 and not job.errors, "engine builds protocol-relative fix without a provider", str(job.status()))
    job = FixJob(); asyncio.run(generate(con, cfg, "security:missing_hsts", [1, 2, 3], job))
    job2 = FixJob(); asyncio.run(generate(con, cfg, "security:missing_hsts", [1, 2, 3], job2))
    n = con.execute("SELECT count(*) FROM fixes WHERE kind='site'").fetchone()[0]
    check(job.created == 1 and job2.created == 0 and n == 1, "site fix created once, not duplicated")
    row = con.execute("SELECT provider, field, url FROM fixes WHERE kind='site'").fetchone()
    check(row["provider"] == "rule" and row["field"] == "header", "site fix row attributes", f"{row['url']} {row['field']}")
    # evidence rides along into AI briefs
    ev = evidence(con, cfg, "content:low_content", 1)
    check("word_count" in ev and "title" in ev, "AI brief evidence has page facts")
    con.close()
    for suf in ("", "-wal", "-shm"):
        p = Path(str(tmp) + suf)
        if p.exists():
            p.unlink()

    # real crawl through the API: protocol-relative links on the fixture crawl (no key configured -> must still work)
    port = free_port()
    threading.Thread(target=lambda: uvicorn.run("spider.server:app", host="127.0.0.1", port=port, log_level="warning"), daemon=True).start()
    url = f"http://127.0.0.1:{port}"
    assert wait_ready(url)
    crawls = json.loads(urllib.request.urlopen(f"{url}/api/crawls").read())["crawls"]
    target = next(c for c in crawls if c["name"] == fixture_crawl().name)
    post(f"{url}/api/crawls/open", {"path": target["path"]})
    tabs = json.loads(urllib.request.urlopen(f"{url}/api/tabs").read())["tabs"]
    sec = {f["key"]: f for t in tabs if t["key"] == "security" for f in t["filters"]}
    check(sec["security:protocol_relative"]["fixKind"] == "code" and sec["security:missing_hsts"]["fixKind"] == "site", "tabs API exposes fixKind")
    key = "security:protocol_relative" if sec["security:protocol_relative"]["count"] else next(k for k, f in sec.items() if f["count"] and f["fixKind"] in ("code", "site"))
    r = post(f"{url}/api/fixes/generate", {"key": key, "ids": []})
    check(r["rule"] is True, "generate reports rule-based", str(r))
    for _ in range(60):
        st = json.loads(urllib.request.urlopen(f"{url}/api/fixes/status").read())
        if st["state"] in ("done", "error"):
            break
        time.sleep(0.5)
    fixes = json.loads(urllib.request.urlopen(f"{url}/api/fixes?size=500").read())["data"]
    mine = [x for x in fixes if x["issue_key"] == key]
    check(st["state"] == "done" and mine, f"{key}: {len(mine)} fixes built on the real crawl", f"{st['created']} created, errors {st['errors'][:1]}")
    distinct = len({x["proposed"] for x in mine})
    template = [x for x in mine if x["field"] == "template"]
    check(distinct == len(mine), "no identical copies: shared markup became one template fix", f"{distinct} distinct of {len(mine)}; template fixes: {len(template)}")
    if template:
        print("  template fix:", template[0]["current"], "\n   ", template[0]["proposed"][:260].replace("\n", "\n    "))
    if mine:
        print("  sample:", mine[0]["url"], "\n   ", mine[0]["proposed"][:300].replace("\n", "\n    "))
    # clean up the proposals this test added to the saved crawl
    post(f"{url}/api/fixes/bulk", {"ids": [x["id"] for x in mine], "delete": True})
    print(f"{len(FAILS)} FAILED: {FAILS}" if FAILS else "all checks passed")
    sys.exit(1 if FAILS else 0)


if __name__ == "__main__":
    main()
