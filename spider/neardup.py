"""Near-duplicate content detection (Screaming Frog "Near Duplicates"): MinHash over word shingles of the extracted
page text + LSH to find candidate pairs, Jaccard estimate kept when it reaches the configured similarity.

Writes the `near_dups` pair table and urls.near_dup_count / closest_similarity (%) / closest_match."""
from __future__ import annotations

import re
import sqlite3
from typing import Iterable

from datasketch import MinHash, MinHashLSH

from . import db as dbm
from .config import CrawlConfig

NUM_PERM = 128
SHINGLE = 3                 # words per shingle
MIN_WORDS = 30              # pages shorter than this are too small to call "similar" meaningfully
_WORD = re.compile(r"[a-z0-9À-ɏ]+")


def shingles(text: str, k: int = SHINGLE) -> Iterable[bytes]:
    words = _WORD.findall(text.lower())
    if len(words) < k:
        return [" ".join(words).encode("utf-8")] if words else []
    return (" ".join(words[i:i + k]).encode("utf-8") for i in range(len(words) - k + 1))


def minhash(text: str) -> MinHash:
    m = MinHash(num_perm=NUM_PERM)
    sh = list(shingles(text))
    if sh:
        m.update_batch(sh)          # vectorised: far faster than update() per shingle
    return m


def _scope(cfg: CrawlConfig) -> str:
    scope = "u.is_internal=1 AND u.type='HTML' AND u.crawled=1 AND u.status_code BETWEEN 200 AND 299"
    if cfg.ignore_non_indexable_for_issues:
        scope += " AND u.indexable=1"
    if cfg.ignore_paginated_for_duplicates:
        scope += " AND (u.rel_prev IS NULL OR u.rel_prev='')"
    return scope


def compute(con: sqlite3.Connection, cfg: CrawlConfig) -> int:
    """Recompute near duplicates for the whole crawl. Returns the number of pairs found."""
    con.execute("CREATE TABLE IF NOT EXISTS near_dups (url_id INTEGER NOT NULL, other_id INTEGER NOT NULL, similarity REAL NOT NULL, PRIMARY KEY (url_id, other_id))")
    con.execute("DELETE FROM near_dups")
    con.execute("UPDATE urls SET near_dup_count=0, closest_similarity=0, closest_match=NULL")
    thr = max(0.3, min(0.99, (cfg.near_duplicate_threshold or 90) / 100.0))
    rows = con.execute(f"SELECT u.id, u.url, b.text FROM urls u JOIN bodies b ON b.url_id=u.id WHERE {_scope(cfg)} AND b.text IS NOT NULL").fetchall()
    hashes: dict[int, MinHash] = {}
    urls: dict[int, str] = {}
    # LSH a little below the threshold so borderline pairs become candidates; the exact estimate decides
    lsh = MinHashLSH(threshold=max(0.2, thr - 0.1), num_perm=NUM_PERM)
    for r in rows:
        text = dbm.decompress(r["text"])
        if len(_WORD.findall(text)) < MIN_WORDS:
            continue
        m = minhash(text)
        hashes[r["id"]] = m
        urls[r["id"]] = r["url"]
        lsh.insert(str(r["id"]), m)
    pairs: dict[tuple[int, int], float] = {}
    for uid, m in hashes.items():
        for other in lsh.query(m):
            oid = int(other)
            if oid == uid:
                continue
            a, b = (uid, oid) if uid < oid else (oid, uid)
            if (a, b) in pairs:
                continue
            sim = m.jaccard(hashes[oid])
            if sim >= thr:
                pairs[(a, b)] = sim
    counts: dict[int, int] = {}
    best: dict[int, tuple[float, int]] = {}
    out = []
    for (a, b), sim in pairs.items():
        for x, y in ((a, b), (b, a)):
            counts[x] = counts.get(x, 0) + 1
            if x not in best or sim > best[x][0]:
                best[x] = (sim, y)
            out.append((x, y, round(sim * 100, 1)))
    con.executemany("INSERT OR REPLACE INTO near_dups(url_id, other_id, similarity) VALUES (?,?,?)", out)
    con.executemany("UPDATE urls SET near_dup_count=?, closest_similarity=?, closest_match=? WHERE id=?",
                    [(counts[x], round(best[x][0] * 100, 1), urls.get(best[x][1]), x) for x in counts])
    return len(pairs)


def duplicates_of(con: sqlite3.Connection, url_id: int) -> dict[str, list]:
    """Exact (same content hash) and near duplicates of one URL, for the URL Details pane."""
    row = con.execute("SELECT hash FROM urls WHERE id=?", (url_id,)).fetchone()
    exact = []
    if row and row["hash"]:
        exact = [r[0] for r in con.execute("SELECT url FROM urls WHERE hash=? AND id<>? AND crawled=1 AND type='HTML' ORDER BY url", (row["hash"], url_id))]
    try:
        near = [{"url": r[0], "similarity": r[1]} for r in con.execute(
            "SELECT u.url, n.similarity FROM near_dups n JOIN urls u ON u.id=n.other_id WHERE n.url_id=? ORDER BY n.similarity DESC, u.url", (url_id,))]
    except sqlite3.OperationalError:
        near = []
    return {"exact": exact, "near": near}
