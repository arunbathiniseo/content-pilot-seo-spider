"""Prominent words per page, for the app's Internal links module (topic overlap, docs/LINKS-STEPS-3-6.md section 2.2).

Yoast's prominent-words method: the stemmed terms of each indexable page's text, with the words of the title, H1,
meta description and H2 counted three times, kept when they occur at least `min_occ` times, the top `top` by weight.
Two pages that share three or more of these terms are candidates for a link; the app does that join, the Spider only
scans its own text, so page text never crosses to the Worker. Same tokenizer as links.py (and the app's pageText.ts).
"""
from __future__ import annotations

import sqlite3
from collections import Counter
from typing import Any

from .links import STOP, tokens

HEAD_WEIGHT = 3
MIN_LEN = 3


def page_terms(text: str, heads: list[str | None], min_occ: int = 4, top: int = 100) -> list[list[Any]]:
    """[[term, weight], ...] by weight, the title/H1/meta/H2 words weighted HEAD_WEIGHT times."""
    c: Counter = Counter()
    for t in tokens(text or ""):
        if t in STOP or len(t) < MIN_LEN or t.isdigit():
            continue
        c[t] += 1
    for h in heads:
        for t in tokens(h or ""):
            if t in STOP or len(t) < MIN_LEN or t.isdigit():
                continue
            c[t] += HEAD_WEIGHT
    rows = [[t, n] for t, n in c.items() if n >= min_occ]
    rows.sort(key=lambda r: (-r[1], r[0]))
    return rows[:top]


def prominent(con: sqlite3.Connection, min_occ: int = 4, top: int = 100, min_words: int = 150) -> dict[str, Any]:
    """Per indexable HTML page (200, crawled, at least min_words words): its prominent terms."""
    rows = con.execute(
        "SELECT u.id, u.url, u.title, u.h1, u.meta_desc, u.h2, u.h2_2, b.text FROM urls u JOIN bodies b ON b.url_id=u.id "
        "WHERE u.is_internal=1 AND u.type='HTML' AND u.status_code=200 AND u.crawled IN (1,3) AND COALESCE(u.indexable,1)=1 "
        "AND COALESCE(u.word_count,0)>=? AND b.text IS NOT NULL ORDER BY u.id",
        (min_words,),
    ).fetchall()
    out = []
    for uid, url, title, h1, md, h2, h2b, text in rows:
        if isinstance(text, (bytes, bytearray)):
            text = text.decode("utf-8", "replace")
        terms = page_terms(str(text or ""), [title, h1, md, h2, h2b], min_occ, top)
        if terms:
            out.append({"url_id": uid, "url": url, "terms": terms})
    return {"pages": out, "min_occurrences": min_occ, "top": top}
