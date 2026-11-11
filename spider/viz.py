"""Data for the crawl visualisations (Screaming Frog "Visualisations" menu): crawl tree (shortest click path from
the start page over internal hyperlinks), directory tree (URL folders with pages as leaves), and word frequencies for
the inlink-anchor-text and body-text clouds. The browser lays them out with D3."""
from __future__ import annotations

import re
import sqlite3
from collections import Counter, deque
from typing import Any
from urllib.parse import urlsplit

from . import db as dbm
from .config import CrawlConfig

_WORD = re.compile(r"[a-z][a-z'’-]{2,}")
STOP = set("""a about above after again against all also am an and any are as at be because been before being below between both but by can could did do does doing
down during each few for from further had has have having he her here hers herself him himself his how i if in into is it its itself just let me more most my
myself no nor not now of off on once only or other our ours ourselves out over own same she should so some such than that the their theirs them themselves then
there these they this those through to too under until up very was we were what when where which while who whom why will with would you your yours yourself
yourselves get got one two new use used using via per via etc like make made take takes way well will www http https com co uk us re ve ll don doesn didn isn
aren wasn weren won wouldn couldn shouldn can't it's that's here's there's what's let's i'm you're we're they're i've you've we've""".split())


def _node(r: sqlite3.Row) -> dict[str, Any]:
    return {"id": r["id"], "url": r["url"], "name": (urlsplit(r["url"]).path.rstrip("/").rsplit("/", 1)[-1] or urlsplit(r["url"]).netloc)[:60],
            "status": r["status_code"], "indexable": r["indexable"], "inlinks": r["inlinks"] or 0, "depth": r["depth"], "title": (r["title"] or "")[:120]}


def crawl_tree(con: sqlite3.Connection, cfg: CrawlConfig, max_nodes: int = 1000) -> dict[str, Any]:
    """Shortest-path tree over internal hyperlinks from the start page (independent of the crawl mode)."""
    pages = {r["id"]: r for r in con.execute("SELECT id, url, status_code, indexable, inlinks, depth, title FROM urls WHERE crawled=1 AND is_internal=1 AND type='HTML'")}
    if not pages:
        return {"nodes": 0, "total": 0, "tree": None, "orphans": 0}
    start = cfg.start_url or ""
    root_id = next((i for i, r in pages.items() if r["url"].rstrip("/") == start.rstrip("/")), None)
    if root_id is None:
        host = urlsplit(start or next(iter(pages.values()))["url"]).netloc
        root_id = next((i for i, r in pages.items() if urlsplit(r["url"]).netloc == host and urlsplit(r["url"]).path in ("", "/")), None)
    if root_id is None:
        root_id = max(pages, key=lambda i: pages[i]["inlinks"] or 0)
    adj: dict[int, list[int]] = {}
    for f, t in con.execute("SELECT from_id, to_id FROM links WHERE kind='hyperlink' AND from_id<>to_id"):
        if f in pages and t in pages:
            adj.setdefault(f, []).append(t)
    parent: dict[int, int | None] = {root_id: None}
    order = [root_id]
    dq = deque([root_id])
    while dq and len(order) < max_nodes:
        u = dq.popleft()
        for v in sorted(adj.get(u, []), key=lambda i: -(pages[i]["inlinks"] or 0)):
            if v not in parent:
                parent[v] = u
                order.append(v)
                dq.append(v)
                if len(order) >= max_nodes:
                    break
    children: dict[int, list[int]] = {}
    for v, p in parent.items():
        if p is not None:
            children.setdefault(p, []).append(v)

    def build(i: int, d: int) -> dict[str, Any]:
        n = _node(pages[i])
        n["depth"] = d
        kids = children.get(i, [])
        if kids:
            n["children"] = [build(k, d + 1) for k in kids]
        return n
    reached = set(parent)
    return {"nodes": len(order), "total": len(pages), "truncated": len(order) >= max_nodes and len(reached) < len(pages), "tree": build(root_id, 0),
            "orphans": sum(1 for i in pages if i not in reached), "max_depth": max((len(_path(parent, i)) for i in order), default=0)}


def _path(parent: dict[int, int | None], i: int) -> list[int]:
    out = []
    while i is not None and len(out) < 100:
        out.append(i)
        i = parent.get(i)
    return out


def directory_tree(con: sqlite3.Connection, max_nodes: int = 1500) -> dict[str, Any]:
    """URL folder hierarchy with pages as leaves; folders carry page counts."""
    rows = con.execute("SELECT id, url, status_code, indexable, inlinks, depth, title FROM urls WHERE crawled=1 AND is_internal=1 AND type='HTML' ORDER BY url").fetchall()
    root: dict[str, Any] = {"name": "/", "path": "/", "pages": 0, "folders": {}, "leaves": []}
    for r in rows:
        p = urlsplit(r["url"])
        segs = [s for s in p.path.split("/") if s]
        is_folder_url = p.path.endswith("/") or not segs
        folders, leaf = (segs, None) if is_folder_url else (segs[:-1], segs[-1])
        node = root
        node["pages"] += 1
        for s in folders:
            node = node["folders"].setdefault(s, {"name": s, "path": node["path"].rstrip("/") + "/" + s + "/", "pages": 0, "folders": {}, "leaves": []})
            node["pages"] += 1
        n = _node(r)
        n["name"] = leaf or (folders[-1] if folders else p.netloc)
        node["leaves"].append(n)
    count = 0

    def pack(n: dict[str, Any]) -> dict[str, Any]:
        nonlocal count
        count += 1
        kids = [pack(c) for c in sorted(n["folders"].values(), key=lambda c: -c["pages"])] + (n["leaves"] if count < max_nodes else [])
        count += len(n["leaves"])
        return {"name": n["name"], "path": n["path"], "pages": n["pages"], "folder": True, "children": kids}
    return {"nodes": min(count, max_nodes), "total": len(rows), "tree": pack(root)}


def word_frequencies(con: sqlite3.Connection, kind: str = "anchor", limit: int = 150, min_len: int = 3) -> dict[str, Any]:
    """kind=anchor: words in internal inlink anchor text; kind=body: words in page text (indexable HTML)."""
    c: Counter = Counter()
    if kind == "anchor":
        src = con.execute("SELECT l.anchor FROM links l JOIN urls t ON t.id=l.to_id WHERE l.kind='hyperlink' AND t.is_internal=1 AND l.anchor IS NOT NULL AND l.anchor<>''")
        for (a,) in src:
            for w in _WORD.findall(a.lower()):
                if w not in STOP and len(w) >= min_len:
                    c[w] += 1
        unit = "links"
    else:
        rows = con.execute("SELECT b.text FROM bodies b JOIN urls u ON u.id=b.url_id WHERE u.crawled=1 AND u.is_internal=1 AND u.type='HTML' AND u.indexable=1 AND b.text IS NOT NULL").fetchall()
        for (t,) in rows:
            for w in _WORD.findall(dbm.decompress(t).lower()):
                if w not in STOP and len(w) >= min_len:
                    c[w] += 1
        unit = "words"
    top = c.most_common(max(10, min(500, limit)))
    return {"kind": kind, "unit": unit, "total_words": sum(c.values()), "distinct": len(c), "words": [{"text": w, "count": n} for w, n in top]}
