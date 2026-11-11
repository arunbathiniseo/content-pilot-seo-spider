"""Change detection between two crawls (Screaming Frog "Compare" mode): pages added / removed / changed, per-field
diffs and issue counts before vs after. Works on the saved crawl files, read-only; results are cached per file pair."""
from __future__ import annotations

import re
import sqlite3
from pathlib import Path
from typing import Any
from urllib.parse import urlsplit

from . import db as dbm
from .config import CrawlConfig
from .issues import TABS, all_filters

# fields compared per common URL (order = display order)
FIELDS = ["status_code", "indexable", "indexability_status", "title", "meta_desc", "h1", "canonical", "meta_robots",
          "word_count", "inlinks", "depth", "redirect_url"]
FIELD_LABELS = {"status_code": "Status", "indexable": "Indexable", "indexability_status": "Indexability", "title": "Title", "meta_desc": "Meta description",
                "h1": "H1", "canonical": "Canonical", "meta_robots": "Meta robots", "word_count": "Word count", "inlinks": "Inlinks", "depth": "Crawl depth",
                "redirect_url": "Redirect URL"}
_CACHE: dict[tuple[str, float, str, float], dict[str, Any]] = {}


def host_of(path: Path, meta: dict[str, Any] | None = None) -> str:
    if meta and meta.get("start_url"):
        return (urlsplit(meta["start_url"]).hostname or "").lower()
    return re.sub(r"_\d{8}_\d{6}$", "", path.stem).lower()


def _load(path: Path) -> dict[str, Any]:
    con = dbm.connect(path, readonly=True)
    try:
        meta = {r["key"]: dbm.get_meta(con, r["key"]) for r in con.execute("SELECT key FROM meta")}
        cols = ", ".join(FIELDS)
        pages = {r["url"]: dict(r) for r in con.execute(f"SELECT url, {cols} FROM urls WHERE crawled=1 AND is_internal=1 AND type='HTML'")}
        issues = {r[0]: r[1] for r in con.execute("SELECT key, count(*) FROM url_issues GROUP BY key")}
        total = con.execute("SELECT count(*) FROM urls WHERE crawled IN (1,3)").fetchone()[0]
    finally:
        con.close()
    return {"name": path.name, "path": str(path), "start_url": meta.get("start_url", ""), "started_at": meta.get("started_at", ""),
            "finished_at": meta.get("finished_at", ""), "state": meta.get("state", ""), "total": total, "pages": pages, "issues": issues, "host": host_of(path, meta)}


def _norm(v: Any) -> Any:
    if v is None:
        return ""
    if isinstance(v, str):
        return v.strip()
    return v


def compare(path_a: Path, path_b: Path, cfg: CrawlConfig) -> dict[str, Any]:
    """A = older / before, B = newer / after. Full result (no limits); the API trims the lists."""
    key = (str(path_a), path_a.stat().st_mtime, str(path_b), path_b.stat().st_mtime)
    if key in _CACHE:
        return _CACHE[key]
    a, b = _load(path_a), _load(path_b)
    urls_a, urls_b = set(a["pages"]), set(b["pages"])
    added = sorted(urls_b - urls_a)
    removed = sorted(urls_a - urls_b)
    changed: list[dict[str, Any]] = []
    field_changes = {f: 0 for f in FIELDS}
    for url in sorted(urls_a & urls_b):
        pa, pb = a["pages"][url], b["pages"][url]
        diffs = {}
        for f in FIELDS:
            va, vb = _norm(pa.get(f)), _norm(pb.get(f))
            if va != vb:
                diffs[f] = [va, vb]
                field_changes[f] += 1
        if diffs:
            changed.append({"url": url, "changes": diffs})

    names = dict(TABS)
    meta = {f["key"]: f for f in all_filters(cfg)}
    issues = []
    for k in set(a["issues"]) | set(b["issues"]):
        f = meta.get(k)
        if not f or not f["kind"]:
            continue
        before, after = a["issues"].get(k, 0), b["issues"].get(k, 0)
        if before == after == 0:
            continue
        issues.append({"key": k, "name": f"{names.get(f['tab'], f['tab'])}: {f['name']}", "kind": f["kind"], "priority": f["priority"],
                       "before": before, "after": after, "delta": after - before})
    issues.sort(key=lambda x: (-abs(x["delta"]), -x["after"]))
    strip = lambda d: {k: v for k, v in d.items() if k not in ("pages", "issues")}  # noqa: E731
    result = {
        "a": strip(a), "b": strip(b),
        "summary": {"pages_before": len(urls_a), "pages_after": len(urls_b), "added": len(added), "removed": len(removed), "changed": len(changed),
                    "unchanged": len(urls_a & urls_b) - len(changed), "field_changes": field_changes,
                    "issues_up": sum(1 for i in issues if i["delta"] > 0), "issues_down": sum(1 for i in issues if i["delta"] < 0),
                    "issues_new": sum(1 for i in issues if i["before"] == 0 and i["after"] > 0), "issues_fixed": sum(1 for i in issues if i["after"] == 0 and i["before"] > 0),
                    "same_site": a["host"] == b["host"]},
        "issues": issues, "added": added, "removed": removed, "changed": changed,
    }
    if len(_CACHE) > 8:
        _CACHE.clear()
    _CACHE[key] = result
    return result


def previous_crawl(current: Path, crawls_dir: Path) -> Path | None:
    """The most recent completed crawl of the same site that is older than `current`."""
    cur_host = host_of(current)
    try:
        con = dbm.connect(current, readonly=True)
        cur_host = host_of(current, {"start_url": dbm.get_meta(con, "start_url", "")})
        con.close()
    except sqlite3.Error:
        pass
    best: Path | None = None
    for p in crawls_dir.glob("*.db"):
        if p.resolve() == current.resolve() or p.stat().st_mtime >= current.stat().st_mtime:
            continue
        try:
            con = dbm.connect(p, readonly=True)
            meta = {"start_url": dbm.get_meta(con, "start_url", ""), "state": dbm.get_meta(con, "state", "")}
            con.close()
        except sqlite3.Error:
            continue
        if meta["state"] not in ("finished", "stopped") or host_of(p, meta) != cur_host:
            continue
        if best is None or p.stat().st_mtime > best.stat().st_mtime:
            best = p
    return best


def to_sheets(result: dict[str, Any]) -> list[tuple[str, list[str], list[list[Any]]]]:
    s = result["summary"]
    summary = [["Before (A)", result["a"]["name"], result["a"]["start_url"], result["a"]["started_at"]],
               ["After (B)", result["b"]["name"], result["b"]["start_url"], result["b"]["started_at"]],
               [], ["HTML pages before", s["pages_before"]], ["HTML pages after", s["pages_after"]], ["Added", s["added"]], ["Removed", s["removed"]],
               ["Changed", s["changed"]], ["Unchanged", s["unchanged"]], ["Issues up", s["issues_up"]], ["Issues down", s["issues_down"]],
               ["New issues", s["issues_new"]], ["Fixed issues", s["issues_fixed"]], []]
    summary += [[f"Pages with changed {FIELD_LABELS[f]}", n] for f, n in s["field_changes"].items() if n]
    issues = [[i["name"], i["kind"], i["priority"], i["before"], i["after"], i["delta"]] for i in result["issues"]]
    changed = [[c["url"], FIELD_LABELS.get(f, f), v[0], v[1]] for c in result["changed"] for f, v in c["changes"].items()]
    return [("Summary", ["", "", "", ""], summary),
            ("Issues", ["Issue", "Type", "Priority", "Before", "After", "Change"], issues),
            ("Changed", ["URL", "Field", "Before", "After"], changed),
            ("Added", ["URL"], [[u] for u in result["added"]]),
            ("Removed", ["URL"], [[u] for u in result["removed"]])]
