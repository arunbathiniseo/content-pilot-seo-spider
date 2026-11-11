"""Retention clean-up for the hosted Spider: delete crawl files older than N days, but always keep the newest finished
crawl of every host (the app's Internal links page reads it) and any crawl still running. Also clears exports older
than the same age. Dry run unless --apply; prints what it would delete or did delete.

    python tools/prune_crawls.py --days 30            (dry run)
    python tools/prune_crawls.py --days 30 --apply

The crawls folder is SPIDER_CRAWLS_DIR (default <app>/crawls), the same one the server uses. Run by the
spider-prune.timer systemd unit on the VPS (contentpilot/docs/SPIDER-HOSTING.md).
"""
from __future__ import annotations

import argparse
import json
import os
import sqlite3
import time
from pathlib import Path
from urllib.parse import urlsplit

ROOT = Path(__file__).resolve().parents[1]
CRAWLS_DIR = Path(os.environ.get("SPIDER_CRAWLS_DIR") or ROOT / "crawls")
EXPORTS_DIR = ROOT / "exports"


def crawl_meta(p: Path) -> dict:
    try:
        con = sqlite3.connect(f"file:{p.as_posix()}?mode=ro", uri=True, timeout=10)
        meta = {k: json.loads(v) for k, v in con.execute("SELECT key, value FROM meta WHERE key IN ('start_url', 'state')")}
        con.close()
        return meta
    except (sqlite3.Error, ValueError):
        return {}


def host_of(url: str) -> str:
    return (urlsplit(url).hostname or "").lower().removeprefix("www.")


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--days", type=int, default=30)
    ap.add_argument("--apply", action="store_true")
    a = ap.parse_args()
    cutoff = time.time() - a.days * 86400

    crawls = sorted(CRAWLS_DIR.glob("*.db"), key=lambda x: x.stat().st_mtime, reverse=True)
    keep_hosts: set[str] = set()
    delete: list[Path] = []
    for p in crawls:   # newest first, so the first finished crawl of a host is the one kept
        m = crawl_meta(p)
        state, host = str(m.get("state", "")), host_of(str(m.get("start_url", "")))
        if state == "running":
            continue
        if host and host not in keep_hosts:
            keep_hosts.add(host)
            continue
        if p.stat().st_mtime < cutoff:
            delete.append(p)
    if EXPORTS_DIR.exists():
        delete += [f for f in EXPORTS_DIR.rglob("*") if f.is_file() and f.stat().st_mtime < cutoff]

    freed = 0
    for f in delete:
        size = f.stat().st_size + sum(s.stat().st_size for s in (f.with_name(f.name + "-wal"), f.with_name(f.name + "-shm")) if s.exists())
        print(("deleted  " if a.apply else "would delete  ") + f"{f}  ({size // 1024} KB)")
        if a.apply:
            for side in (f, f.with_name(f.name + "-wal"), f.with_name(f.name + "-shm")):
                side.unlink(missing_ok=True)
        freed += size
    print(f"{'deleted' if a.apply else 'would delete'} {len(delete)} files, {freed // (1024 * 1024)} MB; kept the newest crawl of {len(keep_hosts)} hosts")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
