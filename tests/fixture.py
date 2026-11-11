"""The saved crawl the read-only suites test against, so they do not depend on one particular site's crawl file.

SPIDER_TEST_CRAWL names a file in crawls/ (the folder the suites' servers open crawls from); without it the largest
finished crawl there is used. Any finished crawl of a few hundred pages will do. On the hosted Spider the fixture is a copy of
a real crawl placed in /srv/spider/app/crawls (contentpilot/docs/SPIDER-HOSTING.md step 8).
"""
from __future__ import annotations

import json
import os
import sqlite3
from pathlib import Path
from urllib.parse import urlsplit

CRAWLS = Path(__file__).resolve().parents[1] / "crawls"


def fixture_crawl() -> Path:
    name = os.environ.get("SPIDER_TEST_CRAWL", "").strip()
    if name:
        p = CRAWLS / Path(name).name
        if not p.exists():
            raise SystemExit(f"SPIDER_TEST_CRAWL={name} is not in {CRAWLS}")
        return p
    dbs = list(CRAWLS.glob("*.db"))
    if not dbs:
        raise SystemExit(f"No saved crawl in {CRAWLS}: crawl a site of a few hundred pages first, or set SPIDER_TEST_CRAWL")
    done = [p for p in dbs if _state(p) == "finished"]
    if not done:
        raise SystemExit(f"No finished crawl in {CRAWLS}: let one crawl of a few hundred pages run to the end, or set SPIDER_TEST_CRAWL")
    return max(done, key=lambda p: p.stat().st_size)


def _state(db: Path) -> str:
    try:
        con = sqlite3.connect(f"file:{db.as_posix()}?mode=ro", uri=True)
        try:
            row = con.execute("SELECT value FROM meta WHERE key='state'").fetchone()
        finally:
            con.close()
        return json.loads(row[0]) if row else ""
    except (sqlite3.Error, ValueError):
        return ""


def fixture_start(db: Path | None = None) -> str:
    """The fixture crawl's start URL: a live site the operator has already crawled, for the suites that crawl live."""
    con = sqlite3.connect(f"file:{(db or fixture_crawl()).as_posix()}?mode=ro", uri=True)
    try:
        row = con.execute("SELECT value FROM meta WHERE key='start_url'").fetchone()
    finally:
        con.close()
    return json.loads(row[0]) if row else ""


def fixture_root(db: Path | None = None) -> str:
    """scheme://host of the fixture crawl's start URL, e.g. https://example.com"""
    u = urlsplit(fixture_start(db))
    return f"{u.scheme}://{u.netloc}"
