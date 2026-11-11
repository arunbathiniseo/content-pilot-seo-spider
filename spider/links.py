"""Internal links support for the Content Pilot app: the link graph of a crawl, where phrases are mentioned in page
text, and a one-page re-fetch to verify a link went live. The app (contentpilot.cloud) does the ranking data, the
scoring and the push; this module only reads the crawl and scans its stored HTML.

The tokenizer is a port of src/lib/perf/pageText.ts in the app, so a phrase the app finds in a Search Console query
is found the same way here: lower case, possessive 's dropped, punctuation to spaces, plurals and -ies stemmed,
stop words forgiven when the phrase has other words."""
from __future__ import annotations

import re
import sqlite3
from dataclasses import dataclass, field
from typing import Any, Iterable

from lxml import html as lxml_html

from . import db as dbm

STOP = {"a", "an", "the", "of", "on", "in", "for", "to", "and", "or", "is", "are", "what", "how", "does", "do", "my", "your", "with", "at", "by", "vs", "from", "be", "it", "that", "this", "i", "me", "can", "you"}
_POSS = re.compile(r"['’]s\b")
_NONWORD = re.compile(r"[^\w\s]+", re.UNICODE)
_WORD = re.compile(r"\w+", re.UNICODE)

# Blocks whose text a link may sit in, and blocks a link must never be added to.
_LINKABLE = {"p", "li", "td", "dd"}
_NEVER = {"h1", "h2", "h3", "h4", "h5", "h6", "blockquote", "figcaption", "pre", "code", "th", "summary", "label", "button", "option"}
_BLOCKS = _LINKABLE | _NEVER
_SKIP = {"script", "style", "noscript", "template", "svg", "head", "iframe", "nav", "header", "footer", "aside", "form"}
_KIND = {"li": "list", "td": "table", "dd": "list", "p": "paragraph", "blockquote": "quote", "figcaption": "caption", "pre": "code", "code": "code", "th": "table", "summary": "heading", "label": "control", "button": "control", "option": "control"}


def stem(w: str) -> str:
    if w.endswith("ies") and len(w) > 4:
        return w[:-3] + "y"
    if w.endswith("s") and not w.endswith("ss") and len(w) > 3:
        return w[:-1]
    return w


def tokens(s: str) -> list[str]:
    """Stemmed lower-case word tokens, the same as the app's tokenizer."""
    s = _POSS.sub("", (s or "").lower())
    s = _NONWORD.sub(" ", s)
    return [stem(w) for w in s.split() if w]


def phrase_tokens(phrase: str) -> list[str]:
    """The tokens a mention must contain in order: the meaningful words, or every word when they are all stop words."""
    all_t = tokens(phrase)
    meaningful = [t for t in all_t if t not in STOP]
    return meaningful or all_t


@dataclass
class Block:
    kind: str                 # paragraph, list, table, heading, quote, caption, code, control
    index: int                # index among the page's blocks, in document order
    text: str                 # the block's visible text, whitespace collapsed
    words: list[tuple[int, int, str]] = field(default_factory=list)   # (start, end, stemmed token) per word in text
    link_spans: list[tuple[int, int]] = field(default_factory=list)   # character ranges inside an <a>


def _block_kind(tag: str, el) -> str:
    if tag in _NEVER:
        return "heading" if tag[0] == "h" and tag[1:].isdigit() else _KIND.get(tag, "heading")
    return _KIND.get(tag, "paragraph")


def _skipped(el, body) -> bool:
    p = el.getparent()
    while p is not None and p is not body:
        ptag = p.tag if isinstance(p.tag, str) else ""
        if ptag in _SKIP:
            return True
        ident = ((p.get("class") or "") + " " + (p.get("id") or "")).lower()
        if ident.strip() and any(k in ident for k in ("breadcrumb", "footer", "masthead", "sidebar", "menu", "navbar", "navigation", "nav-", "-nav")):
            return True
        p = p.getparent()
    return False


def blocks_of(html: str) -> list[Block]:
    """The content blocks of a page in document order, with every word's position and the ranges inside links.
    Blocks inside navigation, header, footer, sidebar or a form are left out. A nested block (a p inside an li) is its
    own block and is not repeated in its parent's text."""
    if not html:
        return []
    try:
        root = lxml_html.fromstring(html)
    except Exception:  # noqa: BLE001
        return []
    body = root.find("body")
    if body is None:
        body = root
    out: list[Block] = []
    for el in body.iter():
        tag = el.tag if isinstance(el.tag, str) else ""
        if tag not in _BLOCKS or _skipped(el, body):
            continue
        parts: list[str] = []
        spans: list[tuple[int, int]] = []
        pos = 0

        def add(text: str | None, in_link: bool) -> None:
            nonlocal pos
            if not text:
                return
            t = re.sub(r"\s+", " ", text)
            if not t:
                return
            parts.append(t)
            if in_link:
                spans.append((pos, pos + len(t)))
            pos += len(t)

        def walk(node, in_link: bool) -> None:
            ntag = node.tag if isinstance(node.tag, str) else ""
            here = in_link or ntag == "a"
            add(node.text, here)
            for child in node:
                ctag = child.tag if isinstance(child.tag, str) else ""
                if ctag in _SKIP or ctag in _BLOCKS:
                    add(" ", False)          # a nested block is its own block; keep a boundary
                else:
                    walk(child, here)
                add(child.tail, in_link)

        walk(el, False)
        raw = "".join(parts)
        lead = len(raw) - len(raw.lstrip())
        text = raw.strip()
        if not text:
            continue
        words = [(m.start() - lead, m.end() - lead, stem(_POSS.sub("", m.group(0).lower()))) for m in _WORD.finditer(raw) if m.start() >= lead and m.end() - lead <= len(text)]
        link_spans = [(max(0, a - lead), min(len(text), b - lead)) for a, b in spans]
        out.append(Block(kind=_block_kind(tag, el), index=len(out), text=text, words=words, link_spans=link_spans))
    return out


def _sentence_around(text: str, start: int, end: int) -> str:
    left = max(text.rfind(". ", 0, start), text.rfind("? ", 0, start), text.rfind("! ", 0, start))
    left = left + 2 if left >= 0 else 0
    m = re.search(r"[.!?](\s|$)", text[end:])
    right = end + m.end() if m else len(text)
    return text[left:right].strip()


def find_in_blocks(blocks: list[Block], need: list[str], max_hits: int = 5) -> list[dict[str, Any]]:
    """Every place the token sequence `need` appears in the page's blocks: exact runs only, in document order."""
    hits: list[dict[str, Any]] = []
    n = len(need)
    if not n:
        return hits
    first = need[0]
    for b in blocks:
        toks = b.words
        for i, (s, _, t) in enumerate(toks):
            if t != first or i + n > len(toks):
                continue
            if any(toks[i + j][2] != need[j] for j in range(1, n)):
                continue
            start, end = s, toks[i + n - 1][1]
            in_link = any(a <= start < bnd or a < end <= bnd for a, bnd in b.link_spans)
            hits.append({"block": b.index, "kind": b.kind, "occurrence": len(hits) + 1, "start": start, "end": end,
                         "anchor": b.text[start:end], "sentence": _sentence_around(b.text, start, end), "in_link": in_link,
                         "linkable": b.kind in ("paragraph", "list", "table") and not in_link})
            if len(hits) >= max_hits:
                return hits
    return hits


def graph(con: sqlite3.Connection, page: int = 1, size: int = 1000) -> dict[str, Any]:
    """Internal HTML pages the crawl fetched and the hyperlinks between them, paged. Pages carry what the app's scorer
    and rules need; links carry the anchor, where on the page it sits, and follow."""
    size = max(1, min(size, 5000))
    page = max(1, page)
    off = (page - 1) * size
    cols = ["id", "url", "status_code", "indexable", "indexability_status", "canonical", "depth", "unique_inlinks", "inlinks", "outlinks", "ext_outlinks",
            "link_score", "word_count", "in_sitemap", "gsc_orphan", "ga_orphan", "title", "h1", "redirect_url", "gsc_impressions", "gsc_clicks", "gsc_position"]
    have = {r[1] for r in con.execute("PRAGMA table_info(urls)")}
    cols = [c for c in cols if c in have]
    where = "is_internal=1 AND type='HTML' AND crawled IN (1,3)"
    total = con.execute(f"SELECT count(*) FROM urls WHERE {where}").fetchone()[0]
    rows = con.execute(f"SELECT {', '.join(cols)} FROM urls WHERE {where} ORDER BY id LIMIT ? OFFSET ?", (size, off)).fetchall()
    pages = [dict(zip(cols, r)) for r in rows]
    ids = [p["id"] for p in pages]
    links: list[dict[str, Any]] = []
    if ids:
        q = ",".join("?" * len(ids))
        links = [{"from_id": r[0], "to_id": r[1], "anchor": r[2] or "", "position": r[3] or "", "follow": int(r[4] or 0)}
                 for r in con.execute(f"SELECT from_id, to_id, anchor, position, follow FROM links WHERE kind='hyperlink' AND is_internal=1 AND from_id IN ({q}) ORDER BY from_id, id", ids)]
    meta = {r["key"]: r["value"] for r in con.execute("SELECT key, value FROM meta WHERE key IN ('start_url','started_at','finished_at','state')")}
    return {"page": page, "size": size, "total_pages": total, "last_page": max(1, -(-total // size)), "meta": meta, "pages": pages, "links": links}


def mentions(con: sqlite3.Connection, phrases: list[dict[str, Any]], exclude_pairs: Iterable[tuple[int, int]] = (), per_phrase: int = 50, min_words: int = 150) -> dict[str, Any]:
    """Where each phrase (and its variants) is mentioned in the stored HTML of the crawl's indexable pages.

    phrases: [{"id": any, "text": "website crawler", "variants": ["site crawler"], "target_id": 12}]. A page that is the
    phrase's own target, or that is in exclude_pairs with it, is skipped. Results per phrase are capped at per_phrase
    pages, the pages with the highest link_score first; within a page the hits come in document order."""
    excl = {(int(a), int(b)) for a, b in exclude_pairs}
    wants = []
    for ph in phrases:
        forms = [ph.get("text") or ""] + list(ph.get("variants") or [])
        needs = []
        seen: set[tuple[str, ...]] = set()
        for f in forms:
            need = tuple(phrase_tokens(f))
            if need and need not in seen:
                seen.add(need)
                needs.append((f, list(need)))
        wants.append((ph, needs, {t for _, need in needs for t in need}))
    pages = con.execute("SELECT u.id, u.url, u.link_score, u.word_count FROM urls u JOIN bodies b ON b.url_id=u.id "
                        "WHERE u.is_internal=1 AND u.type='HTML' AND u.status_code=200 AND u.crawled IN (1,3) AND COALESCE(u.indexable,1)=1 AND COALESCE(u.word_count,0)>=? "
                        "ORDER BY u.link_score DESC, u.id", (min_words,)).fetchall()
    out: dict[Any, list[dict[str, Any]]] = {ph.get("id"): [] for ph, _, _ in wants}
    scanned = 0
    for row in pages:
        uid, url, ls = row[0], row[1], row[2]
        open_ = [(ph, needs) for ph, needs, vocab in wants if len(out[ph.get("id")]) < per_phrase]
        if not open_:
            break     # every phrase has its cap of pages
        active = [(ph, needs) for ph, needs in open_ if ph.get("target_id") != uid and (ph.get("target_id") is None or (uid, int(ph["target_id"])) not in excl)]
        if not active:
            continue
        html = dmp(con, uid)
        if not html:
            continue
        scanned += 1
        blocks = blocks_of(html)
        vocab = {t for b in blocks for _, _, t in b.words}
        for ph, needs in active:
            for form, need in needs:
                if not set(need) <= vocab:
                    continue
                hits = find_in_blocks(blocks, need)
                if hits:
                    out[ph.get("id")].append({"url_id": uid, "url": url, "link_score": ls, "form": form, "exact": form == (ph.get("text") or ""), "hits": hits})
                    break     # one form per page is enough; the first (the phrase itself) wins
    return {"scanned": scanned, "mentions": [{"id": ph.get("id"), "pages": out[ph.get("id")]} for ph, _, _ in wants]}


def dmp(con: sqlite3.Connection, uid: int) -> str:
    r = con.execute("SELECT html, rendered FROM bodies WHERE url_id=?", (uid,)).fetchone()
    if not r:
        return ""
    return dbm.decompress(r[1]) or dbm.decompress(r[0])
