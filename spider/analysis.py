"""Crawl analysis: everything that needs the whole link graph rather than a single page.
Runs incrementally during the crawl and once more at the end (= Screaming Frog 'Crawl Analysis')."""
from __future__ import annotations

import json
import sqlite3
from collections import defaultdict

from .config import CrawlConfig


def run_analysis(con: sqlite3.Connection, cfg: CrawlConfig, final: bool = False) -> None:
    _link_counts(con, cfg)
    _duplicates(con, cfg)
    _redirect_chains(con, cfg)
    _canonical_targets(con)
    from .amp import analyse_crawl as _amp
    _amp(con)
    _pagination(con)
    _hreflang(con)
    _images(con, cfg)
    _sitemaps(con)
    pages = _count(con, "SELECT count(*) FROM urls WHERE crawled=1 AND is_internal=1 AND type='HTML'")
    if final or pages <= 5000:
        _link_score(con)
    if final or pages <= 1500:
        from .neardup import compute as near_duplicates
        try:
            near_duplicates(con, cfg)
        except Exception as exc:  # noqa: BLE001 - never let the optional step break analysis
            print(f"[analysis] near duplicates failed: {exc}")
    if final and (cfg.spell_check or cfg.grammar_check):
        from .spelling import compute as spelling
        try:
            spelling(con, cfg)
        except Exception as exc:  # noqa: BLE001
            print(f"[analysis] spelling/grammar failed: {exc}")
    from .issues import rebuild_issue_table
    rebuild_issue_table(con, cfg)
    con.commit()


def _count(con, sql, params=()) -> int:
    row = con.execute(sql, params).fetchone()
    return int(row[0]) if row and row[0] is not None else 0


# ----------------------------------------------------------------------------- links
def _link_counts(con: sqlite3.Connection, cfg: CrawlConfig) -> None:
    con.executescript("""
    DROP TABLE IF EXISTS temp.inl;
    CREATE TEMP TABLE inl AS
      SELECT l.to_id AS id,
             count(*) AS inlinks,
             count(DISTINCT l.from_id) AS unique_inlinks,
             sum(l.follow) AS inlinks_follow,
             sum(CASE WHEN l.follow=0 THEN 1 ELSE 0 END) AS inlinks_nofollow,
             sum(CASE WHEN f.indexable=1 THEN 1 ELSE 0 END) AS inlinks_from_indexable,
             count(DISTINCT CASE WHEN l.js_only=1 THEN l.from_id END) AS js_inlinks
      FROM links l JOIN urls f ON f.id = l.from_id
      WHERE l.kind='hyperlink' AND f.is_internal=1 AND f.crawled=1
      GROUP BY l.to_id;
    DROP TABLE IF EXISTS temp.outl;
    """)
    nd = ",".join("?" * len(cfg.non_descriptive_anchors)) or "''"
    con.execute(f"""
    CREATE TEMP TABLE outl AS
      SELECT l.from_id AS id,
             sum(CASE WHEN l.is_internal=1 THEN 1 ELSE 0 END) AS outlinks,
             count(DISTINCT CASE WHEN l.is_internal=1 THEN l.to_id END) AS unique_outlinks,
             sum(CASE WHEN l.is_internal=0 THEN 1 ELSE 0 END) AS ext_outlinks,
             count(DISTINCT CASE WHEN l.is_internal=0 THEN l.to_id END) AS unique_ext_outlinks,
             sum(CASE WHEN l.is_internal=1 AND l.follow=0 THEN 1 ELSE 0 END) AS outlinks_nofollow,
             sum(CASE WHEN l.is_internal=1 AND (l.anchor IS NULL OR l.anchor='') AND (l.alt IS NULL OR l.alt='') THEN 1 ELSE 0 END) AS outlinks_no_anchor,
             sum(CASE WHEN l.is_internal=1 AND lower(trim(l.anchor)) IN ({nd}) THEN 1 ELSE 0 END) AS outlinks_nondescriptive,
             count(DISTINCT CASE WHEN l.js_only=1 AND l.is_internal=1 THEN l.to_id END) AS js_outlinks
      FROM links l
      WHERE l.kind='hyperlink'
      GROUP BY l.from_id
    """, [a.lower() for a in cfg.non_descriptive_anchors])
    con.executescript("""
    UPDATE urls SET inlinks=0, unique_inlinks=0, inlinks_follow=0, inlinks_nofollow=0, inlinks_from_indexable=0,
                    outlinks=0, unique_outlinks=0, ext_outlinks=0, unique_ext_outlinks=0,
                    outlinks_nofollow=0, outlinks_no_anchor=0, outlinks_nondescriptive=0, js_inlinks=0, js_outlinks=0;
    UPDATE urls SET inlinks=inl.inlinks, unique_inlinks=inl.unique_inlinks, inlinks_follow=inl.inlinks_follow,
                    inlinks_nofollow=inl.inlinks_nofollow, inlinks_from_indexable=inl.inlinks_from_indexable, js_inlinks=inl.js_inlinks
      FROM inl WHERE urls.id = inl.id;
    UPDATE urls SET outlinks=outl.outlinks, unique_outlinks=outl.unique_outlinks, ext_outlinks=outl.ext_outlinks,
                    unique_ext_outlinks=outl.unique_ext_outlinks, outlinks_nofollow=outl.outlinks_nofollow,
                    outlinks_no_anchor=outl.outlinks_no_anchor, outlinks_nondescriptive=outl.outlinks_nondescriptive, js_outlinks=outl.js_outlinks
      FROM outl WHERE urls.id = outl.id;
    """)


# ----------------------------------------------------------------------------- duplicates
def _duplicates(con: sqlite3.Connection, cfg: CrawlConfig) -> None:
    scope = "is_internal=1 AND type='HTML' AND crawled=1 AND status_code BETWEEN 200 AND 299"
    if cfg.ignore_non_indexable_for_issues:
        scope += " AND indexable=1"
    if cfg.ignore_paginated_for_duplicates:
        scope += " AND (rel_prev IS NULL OR rel_prev='')"
    for col, flag in (("title", "title_dup"), ("meta_desc", "md_dup"), ("meta_kw", "kw_dup"), ("h1", "h1_dup"), ("h2", "h2_dup")):
        con.execute(f"UPDATE urls SET {flag}=0")
        con.execute("DROP TABLE IF EXISTS temp.dup")
        con.execute(f"CREATE TEMP TABLE dup AS SELECT {col} AS v, count(*) AS c FROM urls WHERE {scope} AND {col} IS NOT NULL AND {col}<>'' GROUP BY {col} HAVING c>1")
        con.execute(f"UPDATE urls SET {flag}=dup.c FROM dup WHERE urls.{col}=dup.v AND {scope}")
    # exact duplicates by content hash (all internal 200 HTML, indexability respected via scope)
    con.execute("UPDATE urls SET exact_dup=0")
    con.execute("DROP TABLE IF EXISTS temp.dup")
    con.execute(f"CREATE TEMP TABLE dup AS SELECT hash AS v, count(*) AS c FROM urls WHERE {scope} AND hash IS NOT NULL AND hash<>'' GROUP BY hash HAVING c>1")
    con.execute(f"UPDATE urls SET exact_dup=dup.c FROM dup WHERE urls.hash=dup.v AND {scope}")


# ----------------------------------------------------------------------------- redirects
def _redirect_chains(con: sqlite3.Connection, cfg: CrawlConfig) -> None:
    rows = con.execute("SELECT id, url, status_code, redirect_url, crawled FROM urls WHERE redirect_url IS NOT NULL AND redirect_url<>''").fetchall()
    redirects = {r["url"]: r["redirect_url"] for r in rows}
    status = {r["url"]: r["status_code"] for r in con.execute("SELECT url, status_code FROM urls WHERE crawled=1")}
    updates = []
    for r in rows:
        hops, seen, cur, loop = 0, {r["url"]}, r["url"], 0
        while cur in redirects and hops < max(cfg.max_redirects, 1) + 5:
            nxt = redirects[cur]
            hops += 1
            if nxt in seen:
                loop = 1
                break
            seen.add(nxt)
            cur = nxt
        final_url = cur if not loop else r["url"]
        updates.append((hops, loop, final_url, status.get(final_url), r["id"]))
    con.execute("UPDATE urls SET redirect_chain_len=0, redirect_loop=0, final_url=NULL, final_status=NULL WHERE redirect_url IS NOT NULL AND redirect_url<>''")
    con.executemany("UPDATE urls SET redirect_chain_len=?, redirect_loop=?, final_url=?, final_status=? WHERE id=?", updates)


# ----------------------------------------------------------------------------- canonicals
def _canonical_targets(con: sqlite3.Connection) -> None:
    con.executescript("""
    UPDATE urls SET canonical_target_nonindexable=0, canonical_unlinked=0;
    UPDATE urls SET canonical_target_nonindexable=1
      WHERE canonical IS NOT NULL AND canonical<>'' AND canonical<>url
        AND EXISTS (SELECT 1 FROM urls t WHERE t.url=urls.canonical AND t.crawled IN (1,3) AND (t.indexable=0 OR t.status_code<>200));
    UPDATE urls SET canonical_unlinked=1
      WHERE canonical IS NOT NULL AND canonical<>'' AND canonical<>url AND is_internal=1
        AND NOT EXISTS (SELECT 1 FROM links l JOIN urls t ON t.id=l.to_id WHERE t.url=urls.canonical AND l.kind='hyperlink');
    """)


# ----------------------------------------------------------------------------- pagination
def _pagination(con: sqlite3.Connection) -> None:
    con.execute("UPDATE urls SET pag_non200=0, pag_unlinked=0, pag_loop=0, pag_sequence_error=0 WHERE pagination_count>0")
    rows = con.execute("SELECT id, url, rel_next, rel_prev FROM urls WHERE pagination_count>0 AND crawled=1").fetchall()
    if not rows:
        return
    info = {r["url"]: (r["status_code"], r["rel_next"], r["rel_prev"], r["crawled"]) for r in
            con.execute("SELECT url, status_code, rel_next, rel_prev, crawled FROM urls WHERE crawled IN (1,3) OR pagination_count>0")}
    linked = {r[0] for r in con.execute("SELECT DISTINCT t.url FROM links l JOIN urls t ON t.id=l.to_id WHERE l.kind='hyperlink'")}
    nexts = {r["url"]: r["rel_next"] for r in rows if r["rel_next"]}
    for r in rows:
        non200 = unlinked = loop = seq = 0
        for p in (r["rel_next"], r["rel_prev"]):
            if not p:
                continue
            st = info.get(p)
            if st and st[3] == 1 and st[0] != 200:
                non200 = 1
            if p not in linked:
                unlinked = 1
        if r["rel_next"]:
            nxt = info.get(r["rel_next"])
            if nxt and nxt[2] and nxt[2] != r["url"]:
                seq = 1
            cur, seen = r["url"], set()
            while cur in nexts and len(seen) < 100:
                seen.add(cur)
                cur = nexts[cur]
                if cur == r["url"]:
                    loop = 1
                    break
        if r["rel_prev"]:
            prv = info.get(r["rel_prev"])
            if prv and prv[1] and prv[1] != r["url"]:
                seq = 1
        con.execute("UPDATE urls SET pag_non200=?, pag_unlinked=?, pag_loop=?, pag_sequence_error=? WHERE id=?", (non200, unlinked, loop, seq, r["id"]))


# ----------------------------------------------------------------------------- hreflang
def _hreflang(con: sqlite3.Connection) -> None:
    con.execute("""UPDATE urls SET hl_non200=0, hl_unlinked=0, hl_missing_return=0, hl_inconsistent=0, hl_noncanonical_return=0,
                   hl_noindex_return=0, hl_multiple=0, hl_missing_self=0, hl_not_canonical=0, hl_missing_xdefault=0 WHERE hreflang_count>0""")
    rows = con.execute("SELECT id, url, hreflang, canonical FROM urls WHERE hreflang_count>0 AND crawled=1").fetchall()
    if not rows:
        return
    pages: dict[str, list[dict]] = {}
    for r in rows:
        try:
            pages[r["url"]] = json.loads(r["hreflang"] or "[]")
        except json.JSONDecodeError:
            pages[r["url"]] = []
    meta = {r["url"]: r for r in con.execute(
        "SELECT url, status_code, indexable, indexability_status, canonical, crawled, is_internal FROM urls WHERE crawled IN (1,3)")}
    linked = {r[0] for r in con.execute("SELECT DISTINCT t.url FROM links l JOIN urls t ON t.id=l.to_id WHERE l.kind='hyperlink'")}
    for r in rows:
        url = r["url"]
        entries = pages[url]
        flags = defaultdict(int)
        by_lang: dict[str, set[str]] = defaultdict(set)
        self_langs = {e["lang"].lower() for e in entries if e["url"] == url}
        for e in entries:
            lang, target = e["lang"].lower(), e["url"]
            by_lang[lang].add(target)
            t = meta.get(target)
            if t is not None and t["crawled"] == 1 and t["status_code"] != 200:
                flags["hl_non200"] = 1
            if target != url and target not in linked and (t is None or t["is_internal"]):
                flags["hl_unlinked"] = 1
            if target != url and target in pages:
                back = [b for b in pages[target] if b["url"] == url]
                if not back:
                    flags["hl_missing_return"] = 1
                elif self_langs and not any(b["lang"].lower() in self_langs for b in back):
                    flags["hl_inconsistent"] = 1
            if t is not None and target != url:
                if t["canonical"] and t["canonical"] != target:
                    flags["hl_noncanonical_return"] = 1
                if t["indexability_status"] == "Noindex":
                    flags["hl_noindex_return"] = 1
        if any(len(v) > 1 for v in by_lang.values()):
            flags["hl_multiple"] = 1
        if not self_langs:
            flags["hl_missing_self"] = 1
        if "x-default" not in by_lang:
            flags["hl_missing_xdefault"] = 1
        if r["canonical"] and r["canonical"] != url:
            flags["hl_not_canonical"] = 1
        if flags:
            sets = ", ".join(f"{k}=1" for k in flags)
            con.execute(f"UPDATE urls SET {sets} WHERE id=?", (r["id"],))


# ----------------------------------------------------------------------------- images
def _images(con: sqlite3.Connection, cfg: CrawlConfig) -> None:
    con.execute("UPDATE urls SET img_alt_missing=0, img_alt_attr_missing=0, img_alt_over=0, img_size_attr_missing=0, img_background=0")
    con.execute("DROP TABLE IF EXISTS temp.imgagg")
    con.execute("""
    CREATE TEMP TABLE imgagg AS
      SELECT to_id AS id,
             max(CASE WHEN is_bg=0 AND alt='' THEN 1 ELSE 0 END) AS alt_missing,
             max(CASE WHEN is_bg=0 AND alt IS NULL THEN 1 ELSE 0 END) AS alt_attr_missing,
             max(CASE WHEN is_bg=0 AND length(alt) > ? THEN 1 ELSE 0 END) AS alt_over,
             max(CASE WHEN is_bg=0 AND has_size_attrs=0 THEN 1 ELSE 0 END) AS size_missing,
             max(is_bg) AS bg
      FROM links WHERE kind='image' GROUP BY to_id
    """, (cfg.alt_max_chars,))
    con.execute("""
    UPDATE urls SET img_alt_missing=imgagg.alt_missing, img_alt_attr_missing=imgagg.alt_attr_missing, img_alt_over=imgagg.alt_over,
                    img_size_attr_missing=imgagg.size_missing, img_background=imgagg.bg
      FROM imgagg WHERE urls.id=imgagg.id
    """)


# ----------------------------------------------------------------------------- sitemaps
def _sitemaps(con: sqlite3.Connection) -> None:
    con.executescript("""
    UPDATE urls SET in_sitemap=0, sitemap_count=0;
    DROP TABLE IF EXISTS temp.smc;
    CREATE TEMP TABLE smc AS SELECT url, count(DISTINCT sitemap) AS c FROM sitemap_urls GROUP BY url;
    UPDATE urls SET in_sitemap=1, sitemap_count=smc.c FROM smc WHERE urls.url=smc.url;
    """)


# ----------------------------------------------------------------------------- link score (PageRank, 0-100)
def _link_score(con: sqlite3.Connection) -> None:
    nodes = [r[0] for r in con.execute("SELECT id FROM urls WHERE is_internal=1 AND type='HTML' AND crawled=1 AND status_code=200")]
    if not nodes:
        return
    idx = {n: i for i, n in enumerate(nodes)}
    out: list[set[int]] = [set() for _ in nodes]
    for frm, to in con.execute("SELECT DISTINCT from_id, to_id FROM links WHERE kind='hyperlink' AND follow=1 AND is_internal=1"):
        if frm in idx and to in idx and frm != to:
            out[idx[frm]].add(idx[to])
    n = len(nodes)
    rank = [1.0 / n] * n
    d = 0.85
    for _ in range(25):
        new = [(1 - d) / n] * n
        dangling = sum(rank[i] for i in range(n) if not out[i])
        for i in range(n):
            if out[i]:
                share = d * rank[i] / len(out[i])
                for j in out[i]:
                    new[j] += share
        add = d * dangling / n
        rank = [v + add for v in new]
    mx = max(rank) or 1.0
    con.executemany("UPDATE urls SET link_score=? WHERE id=?", [(round(100.0 * rank[i] / mx, 2), nodes[i]) for i in range(n)])
