"""SQLite storage. One database file per crawl (= Screaming Frog database storage mode)."""
from __future__ import annotations

import json
import sqlite3
import zlib
from pathlib import Path
from typing import Any, Iterable

SCHEMA = """
CREATE TABLE IF NOT EXISTS meta (key TEXT PRIMARY KEY, value TEXT);

CREATE TABLE IF NOT EXISTS urls (
  id INTEGER PRIMARY KEY,
  url TEXT UNIQUE NOT NULL,
  is_internal INTEGER NOT NULL DEFAULT 1,
  type TEXT,                      -- HTML, JavaScript, CSS, Images, PDF, Media, Fonts, XML, Flash, Other, Unknown
  content_type TEXT,
  status_code INTEGER,
  status TEXT,
  blocked_robots INTEGER DEFAULT 0,
  error TEXT,
  crawled INTEGER DEFAULT 0,      -- 0 queued, 1 fetched, 2 skipped (not in scope)
  depth INTEGER,
  folder_depth INTEGER,
  discovered_from TEXT,
  response_time REAL,
  size INTEGER,
  transferred INTEGER,
  http_version TEXT,
  last_modified TEXT,
  crawl_ts TEXT,
  redirect_url TEXT,
  redirect_type TEXT,
  headers TEXT,
  cookies TEXT,
  -- page extraction
  title TEXT, title_count INTEGER DEFAULT 0, title_len INTEGER DEFAULT 0, title_px INTEGER DEFAULT 0, title_outside_head INTEGER DEFAULT 0,
  meta_desc TEXT, md_count INTEGER DEFAULT 0, md_len INTEGER DEFAULT 0, md_px INTEGER DEFAULT 0, md_outside_head INTEGER DEFAULT 0,
  meta_kw TEXT, kw_count INTEGER DEFAULT 0, kw_len INTEGER DEFAULT 0,
  h1 TEXT, h1_2 TEXT, h1_count INTEGER DEFAULT 0, h1_len INTEGER DEFAULT 0, h1_has_alt INTEGER DEFAULT 0, h1_nonseq INTEGER DEFAULT 0,
  h2 TEXT, h2_2 TEXT, h2_count INTEGER DEFAULT 0, h2_len INTEGER DEFAULT 0, h2_nonseq INTEGER DEFAULT 0,
  headings TEXT,
  meta_robots TEXT, x_robots TEXT, robots_outside_head INTEGER DEFAULT 0,
  meta_refresh TEXT, http_refresh TEXT,
  canonical TEXT, canonical_count INTEGER DEFAULT 0, canonical_http TEXT, canonical_conflict INTEGER DEFAULT 0,
  canonical_relative INTEGER DEFAULT 0, canonical_fragment INTEGER DEFAULT 0, canonical_invalid_attr INTEGER DEFAULT 0,
  canonical_outside_head INTEGER DEFAULT 0, canonical_unlinked INTEGER DEFAULT 0, canonical_target_nonindexable INTEGER DEFAULT 0,
  rel_next TEXT, rel_prev TEXT, pagination_count INTEGER DEFAULT 0, pagination_not_anchor INTEGER DEFAULT 0, pag_multiple INTEGER DEFAULT 0,
  pag_non200 INTEGER DEFAULT 0, pag_unlinked INTEGER DEFAULT 0, pag_loop INTEGER DEFAULT 0, pag_sequence_error INTEGER DEFAULT 0,
  hreflang TEXT, hreflang_count INTEGER DEFAULT 0, hreflang_outside_head INTEGER DEFAULT 0,
  hl_non200 INTEGER DEFAULT 0, hl_unlinked INTEGER DEFAULT 0, hl_missing_return INTEGER DEFAULT 0, hl_inconsistent INTEGER DEFAULT 0,
  hl_noncanonical_return INTEGER DEFAULT 0, hl_noindex_return INTEGER DEFAULT 0, hl_bad_codes INTEGER DEFAULT 0,
  hl_multiple INTEGER DEFAULT 0, hl_missing_self INTEGER DEFAULT 0, hl_not_canonical INTEGER DEFAULT 0, hl_missing_xdefault INTEGER DEFAULT 0,
  amp_url TEXT, mobile_alt TEXT, lang TEXT,
  indexable INTEGER, indexability_status TEXT,
  word_count INTEGER, sentence_count INTEGER, avg_words_sentence REAL, flesch REAL, readability TEXT, text_ratio REAL,
  hash TEXT, text_hash TEXT, lorem_ipsum INTEGER DEFAULT 0, soft_404 INTEGER DEFAULT 0,
  -- security / validation
  mixed_content INTEGER DEFAULT 0, form_insecure INTEGER DEFAULT 0, form_on_http INTEGER DEFAULT 0,
  unsafe_cross_origin INTEGER DEFAULT 0, protocol_relative INTEGER DEFAULT 0, bad_content_type INTEGER DEFAULT 0,
  invalid_head_elements INTEGER DEFAULT 0, body_before_html INTEGER DEFAULT 0, head_not_first INTEGER DEFAULT 0,
  head_missing INTEGER DEFAULT 0, head_multiple INTEGER DEFAULT 0, body_missing INTEGER DEFAULT 0, body_multiple INTEGER DEFAULT 0,
  js_links INTEGER DEFAULT 0, uncrawlable_outlinks INTEGER DEFAULT 0, localhost_outlinks INTEGER DEFAULT 0,
  -- URL checks
  url_non_ascii INTEGER DEFAULT 0, url_underscores INTEGER DEFAULT 0, url_uppercase INTEGER DEFAULT 0,
  url_multiple_slashes INTEGER DEFAULT 0, url_repetitive_path INTEGER DEFAULT 0, url_space INTEGER DEFAULT 0,
  url_internal_search INTEGER DEFAULT 0, url_parameters INTEGER DEFAULT 0, url_ga_params INTEGER DEFAULT 0,
  url_over_length INTEGER DEFAULT 0, url_broken_bookmark INTEGER DEFAULT 0,
  -- analysis populated
  inlinks INTEGER DEFAULT 0, unique_inlinks INTEGER DEFAULT 0, outlinks INTEGER DEFAULT 0, unique_outlinks INTEGER DEFAULT 0,
  ext_outlinks INTEGER DEFAULT 0, unique_ext_outlinks INTEGER DEFAULT 0, link_score REAL DEFAULT 0,
  inlinks_follow INTEGER DEFAULT 0, inlinks_nofollow INTEGER DEFAULT 0, inlinks_from_indexable INTEGER DEFAULT 0,
  outlinks_nofollow INTEGER DEFAULT 0, outlinks_no_anchor INTEGER DEFAULT 0, outlinks_nondescriptive INTEGER DEFAULT 0,
  title_dup INTEGER DEFAULT 0, md_dup INTEGER DEFAULT 0, kw_dup INTEGER DEFAULT 0, h1_dup INTEGER DEFAULT 0, h2_dup INTEGER DEFAULT 0,
  exact_dup INTEGER DEFAULT 0, near_dup_count INTEGER DEFAULT 0, closest_similarity REAL DEFAULT 0, closest_match TEXT,
  redirect_chain_len INTEGER DEFAULT 0, redirect_loop INTEGER DEFAULT 0, final_url TEXT, final_status INTEGER,
  in_sitemap INTEGER DEFAULT 0, sitemap_count INTEGER DEFAULT 0,
  img_alt_missing INTEGER DEFAULT 0, img_alt_attr_missing INTEGER DEFAULT 0, img_alt_over INTEGER DEFAULT 0,
  img_size_attr_missing INTEGER DEFAULT 0, img_background INTEGER DEFAULT 0, img_width INTEGER, img_height INTEGER,
  custom TEXT,
  -- JavaScript rendering (original-HTML values kept for comparison)
  rendered INTEGER DEFAULT 0, render_time REAL, render_error TEXT,
  orig_title TEXT, orig_meta_desc TEXT, orig_h1 TEXT, orig_canonical TEXT, orig_meta_robots TEXT, orig_word_count INTEGER,
  js_word_delta INTEGER DEFAULT 0, js_word_pct REAL DEFAULT 0, js_links_count INTEGER DEFAULT 0, js_content INTEGER DEFAULT 0,
  js_errors INTEGER DEFAULT 0, js_errors_text TEXT, js_blocked INTEGER DEFAULT 0, js_blocked_text TEXT,
  js_inlinks INTEGER DEFAULT 0, js_outlinks INTEGER DEFAULT 0, meta_fragment INTEGER DEFAULT 0,
  spelling_errors INTEGER DEFAULT 0, grammar_errors INTEGER DEFAULT 0,
  sd_count INTEGER DEFAULT 0, sd_jsonld INTEGER DEFAULT 0, sd_microdata INTEGER DEFAULT 0, sd_rdfa INTEGER DEFAULT 0,
  sd_errors INTEGER DEFAULT 0, sd_warnings INTEGER DEFAULT 0, sd_parse_errors INTEGER DEFAULT 0, sd_types TEXT,
  is_amp INTEGER DEFAULT 0, amp_issue_count INTEGER DEFAULT 0, amp_missing_script INTEGER DEFAULT 0, amp_missing_boilerplate INTEGER DEFAULT 0, amp_disallowed_html INTEGER DEFAULT 0, amp_missing_canonical INTEGER DEFAULT 0, amp_other_errors INTEGER DEFAULT 0, amp_non200 INTEGER DEFAULT 0, amp_missing_return INTEGER DEFAULT 0, amp_canonical_nonindexable INTEGER DEFAULT 0, amp_indexable INTEGER DEFAULT 0, amp_missing_html_tag INTEGER DEFAULT 0, amp_issues TEXT,
  gsc_clicks REAL, gsc_impressions REAL, gsc_ctr REAL, gsc_position REAL, gsc_orphan INTEGER DEFAULT 0, ga_sessions REAL, ga_engaged REAL, ga_engagement_rate REAL, ga_bounce_rate REAL, ga_views REAL, ga_key_events REAL, ga_orphan INTEGER DEFAULT 0, inspect_verdict TEXT, inspect_coverage TEXT, inspect_indexing_state TEXT, inspect_last_crawl TEXT, inspect_robots TEXT, inspect_fetch TEXT, inspect_google_canonical TEXT, inspect_user_canonical TEXT, inspect_mobile TEXT, inspect_at TEXT,
  lm_ahrefs_ur REAL, lm_ahrefs_dr REAL, lm_ahrefs_backlinks REAL, lm_ahrefs_refdomains REAL, lm_ahrefs_keywords REAL, lm_ahrefs_traffic REAL, lm_moz_pa REAL, lm_moz_da REAL, lm_moz_spam REAL, lm_moz_linking_domains REAL, lm_moz_backlinks REAL, lm_majestic_tf REAL, lm_majestic_cf REAL, lm_majestic_backlinks REAL, lm_majestic_refdomains REAL, lm_at TEXT, lm_providers TEXT,
  lh_perf INTEGER, lh_a11y INTEGER, lh_bp INTEGER, lh_seo INTEGER, lh_lcp REAL, lh_cls REAL, lh_tbt REAL, lh_fcp REAL, lh_si REAL, lh_tti REAL, lh_field_lcp REAL, lh_field_cls REAL, lh_field_inp REAL, lh_failed TEXT, lh_a11y_fails INTEGER, lh_perf_fails INTEGER, lh_at TEXT, lh_device TEXT, lh_source TEXT
);
CREATE INDEX IF NOT EXISTS idx_urls_crawled ON urls(crawled);
CREATE INDEX IF NOT EXISTS idx_urls_type ON urls(is_internal, type);
CREATE INDEX IF NOT EXISTS idx_urls_status ON urls(status_code);

CREATE TABLE IF NOT EXISTS links (
  id INTEGER PRIMARY KEY,
  from_id INTEGER NOT NULL,
  to_id INTEGER NOT NULL,
  kind TEXT NOT NULL,               -- hyperlink, image, css, js, canonical, hreflang, pagination, meta_refresh, iframe, form, amp, mobile_alt, other
  anchor TEXT,
  alt TEXT,                         -- NULL = attribute missing (images), '' = empty
  rel TEXT,
  follow INTEGER DEFAULT 1,
  target TEXT,
  position TEXT,
  path TEXT,
  is_internal INTEGER DEFAULT 1,
  has_size_attrs INTEGER DEFAULT 1,
  is_bg INTEGER DEFAULT 0,
  hreflang TEXT,
  js_only INTEGER DEFAULT 0
);
CREATE INDEX IF NOT EXISTS idx_links_from ON links(from_id);
CREATE INDEX IF NOT EXISTS idx_links_to ON links(to_id);
CREATE INDEX IF NOT EXISTS idx_links_kind ON links(kind);

CREATE TABLE IF NOT EXISTS bodies (
  url_id INTEGER PRIMARY KEY,
  html BLOB,
  text BLOB,
  rendered BLOB,
  screenshot BLOB
);

CREATE TABLE IF NOT EXISTS sitemaps (
  url TEXT PRIMARY KEY,
  status_code INTEGER,
  is_index INTEGER DEFAULT 0,
  url_count INTEGER DEFAULT 0,
  size INTEGER DEFAULT 0,
  parent TEXT
);
CREATE TABLE IF NOT EXISTS sitemap_urls (
  sitemap TEXT NOT NULL,
  url TEXT NOT NULL,
  lastmod TEXT,
  PRIMARY KEY (sitemap, url)
);
CREATE INDEX IF NOT EXISTS idx_sitemap_urls_url ON sitemap_urls(url);

CREATE TABLE IF NOT EXISTS url_issues (
  url_id INTEGER NOT NULL,
  key TEXT NOT NULL,
  PRIMARY KEY (url_id, key)
);
CREATE INDEX IF NOT EXISTS idx_url_issues_key ON url_issues(key);

CREATE TABLE IF NOT EXISTS fixes (
  id INTEGER PRIMARY KEY,
  url_id INTEGER NOT NULL,
  url TEXT NOT NULL,
  kind TEXT NOT NULL,               -- title, meta_description, h1, alt_text, canonical, redirect, brief
  field TEXT,
  issue_key TEXT,
  issue_name TEXT,
  current TEXT,
  proposed TEXT,
  rationale TEXT,
  status TEXT DEFAULT 'proposed',   -- proposed, approved, rejected, pushed, failed
  provider TEXT, model TEXT,
  target TEXT, result TEXT,
  created_at TEXT, updated_at TEXT, pushed_at TEXT,
  extra TEXT
);
CREATE INDEX IF NOT EXISTS idx_fixes_status ON fixes(status);
CREATE INDEX IF NOT EXISTS idx_fixes_url ON fixes(url_id, kind);

CREATE TABLE IF NOT EXISTS structured_data (
  id INTEGER PRIMARY KEY,
  url_id INTEGER NOT NULL,
  syntax TEXT,                      -- JSON-LD, Microdata, RDFa
  type TEXT,
  data TEXT,                        -- JSON of the node
  errors TEXT,                      -- JSON list
  warnings TEXT                     -- JSON list
);
CREATE INDEX IF NOT EXISTS idx_sd_url ON structured_data(url_id);

CREATE TABLE IF NOT EXISTS near_dups (
  url_id INTEGER NOT NULL,
  other_id INTEGER NOT NULL,
  similarity REAL NOT NULL,         -- percent, 0-100
  PRIMARY KEY (url_id, other_id)
);

CREATE TABLE IF NOT EXISTS uncrawlable (
  from_id INTEGER NOT NULL,
  href TEXT NOT NULL,
  anchor TEXT,
  reason TEXT
);
"""


_RE_CACHE: dict[str, "re.Pattern[str]"] = {}


def _regexp(pattern: str, value: Any) -> int:
    import re
    if value is None:
        return 0
    rx = _RE_CACHE.get(pattern)
    if rx is None:
        rx = _RE_CACHE[pattern] = re.compile(pattern, re.I)
    return 1 if rx.search(str(value)) else 0


def connect(path: str | Path, readonly: bool = False) -> sqlite3.Connection:
    if readonly:
        con = sqlite3.connect(f"file:{Path(path).as_posix()}?mode=ro", uri=True, check_same_thread=False, timeout=30)
    else:
        con = sqlite3.connect(str(path), check_same_thread=False, timeout=30)
    con.row_factory = sqlite3.Row
    con.create_function("REGEXP", 2, _regexp, deterministic=True)
    con.execute("PRAGMA journal_mode=WAL")
    con.execute("PRAGMA synchronous=NORMAL")
    con.execute("PRAGMA temp_store=MEMORY")
    con.execute("PRAGMA cache_size=-65536")
    if not readonly:
        con.executescript(SCHEMA)
        _migrate(con)
    return con


# columns added after the first release; older crawl files get them on open
_MIGRATIONS = {
    "urls": {
        "rendered": "INTEGER DEFAULT 0", "render_time": "REAL", "render_error": "TEXT",
        "orig_title": "TEXT", "orig_meta_desc": "TEXT", "orig_h1": "TEXT", "orig_canonical": "TEXT", "orig_meta_robots": "TEXT",
        "orig_word_count": "INTEGER", "js_word_delta": "INTEGER DEFAULT 0", "js_word_pct": "REAL DEFAULT 0",
        "js_links_count": "INTEGER DEFAULT 0", "js_content": "INTEGER DEFAULT 0", "js_errors": "INTEGER DEFAULT 0", "js_errors_text": "TEXT",
        "js_blocked": "INTEGER DEFAULT 0", "js_blocked_text": "TEXT", "js_inlinks": "INTEGER DEFAULT 0", "js_outlinks": "INTEGER DEFAULT 0",
        "meta_fragment": "INTEGER DEFAULT 0", "pag_multiple": "INTEGER DEFAULT 0",
        "spelling_errors": "INTEGER DEFAULT 0", "grammar_errors": "INTEGER DEFAULT 0",
        "sd_count": "INTEGER DEFAULT 0", "sd_jsonld": "INTEGER DEFAULT 0", "sd_microdata": "INTEGER DEFAULT 0", "sd_rdfa": "INTEGER DEFAULT 0",
        "sd_errors": "INTEGER DEFAULT 0", "sd_warnings": "INTEGER DEFAULT 0", "sd_parse_errors": "INTEGER DEFAULT 0", "sd_types": "TEXT",
        "is_amp": "INTEGER DEFAULT 0", "amp_issue_count": "INTEGER DEFAULT 0", "amp_missing_script": "INTEGER DEFAULT 0", "amp_missing_boilerplate": "INTEGER DEFAULT 0", "amp_disallowed_html": "INTEGER DEFAULT 0", "amp_missing_canonical": "INTEGER DEFAULT 0", "amp_other_errors": "INTEGER DEFAULT 0", "amp_non200": "INTEGER DEFAULT 0", "amp_missing_return": "INTEGER DEFAULT 0", "amp_canonical_nonindexable": "INTEGER DEFAULT 0", "amp_indexable": "INTEGER DEFAULT 0", "amp_missing_html_tag": "INTEGER DEFAULT 0", "amp_issues": "TEXT",
        "lm_ahrefs_ur": "REAL", "lm_ahrefs_dr": "REAL", "lm_ahrefs_backlinks": "REAL", "lm_ahrefs_refdomains": "REAL", "lm_ahrefs_keywords": "REAL", "lm_ahrefs_traffic": "REAL", "lm_moz_pa": "REAL", "lm_moz_da": "REAL", "lm_moz_spam": "REAL", "lm_moz_linking_domains": "REAL", "lm_moz_backlinks": "REAL", "lm_majestic_tf": "REAL", "lm_majestic_cf": "REAL", "lm_majestic_backlinks": "REAL", "lm_majestic_refdomains": "REAL", "lm_at": "TEXT", "lm_providers": "TEXT",
        "gsc_clicks": "REAL", "gsc_impressions": "REAL", "gsc_ctr": "REAL", "gsc_position": "REAL", "gsc_orphan": "INTEGER DEFAULT 0", "ga_sessions": "REAL", "ga_engaged": "REAL", "ga_engagement_rate": "REAL", "ga_bounce_rate": "REAL", "ga_views": "REAL", "ga_key_events": "REAL", "ga_orphan": "INTEGER DEFAULT 0", "inspect_verdict": "TEXT", "inspect_coverage": "TEXT", "inspect_indexing_state": "TEXT", "inspect_last_crawl": "TEXT", "inspect_robots": "TEXT", "inspect_fetch": "TEXT", "inspect_google_canonical": "TEXT", "inspect_user_canonical": "TEXT", "inspect_mobile": "TEXT", "inspect_at": "TEXT",
        "lh_perf": "INTEGER", "lh_a11y": "INTEGER", "lh_bp": "INTEGER", "lh_seo": "INTEGER", "lh_lcp": "REAL", "lh_cls": "REAL", "lh_tbt": "REAL", "lh_fcp": "REAL", "lh_si": "REAL", "lh_tti": "REAL", "lh_field_lcp": "REAL", "lh_field_cls": "REAL", "lh_field_inp": "REAL", "lh_failed": "TEXT", "lh_a11y_fails": "INTEGER", "lh_perf_fails": "INTEGER", "lh_at": "TEXT", "lh_device": "TEXT", "lh_source": "TEXT",
    },
    "links": {"js_only": "INTEGER DEFAULT 0"},
    "bodies": {"rendered": "BLOB", "screenshot": "BLOB"},
}


def _migrate(con: sqlite3.Connection) -> None:
    global URL_COLUMNS
    for table, cols in _MIGRATIONS.items():
        have = {r[1] for r in con.execute(f"PRAGMA table_info({table})")}
        for name, decl in cols.items():
            if name not in have:
                con.execute(f"ALTER TABLE {table} ADD COLUMN {name} {decl}")
    URL_COLUMNS = None


def set_meta(con: sqlite3.Connection, key: str, value: Any) -> None:
    con.execute("INSERT OR REPLACE INTO meta(key, value) VALUES (?, ?)", (key, json.dumps(value)))


def get_meta(con: sqlite3.Connection, key: str, default: Any = None) -> Any:
    row = con.execute("SELECT value FROM meta WHERE key = ?", (key,)).fetchone()
    return json.loads(row[0]) if row else default


def compress(data: str | bytes | None) -> bytes | None:
    if data is None:
        return None
    if isinstance(data, str):
        data = data.encode("utf-8", "replace")
    return zlib.compress(data, 6)


def decompress(blob: bytes | None) -> str:
    if not blob:
        return ""
    return zlib.decompress(blob).decode("utf-8", "replace")


URL_COLUMNS: list[str] | None = None


def url_columns(con: sqlite3.Connection) -> list[str]:
    global URL_COLUMNS
    if URL_COLUMNS is None:
        URL_COLUMNS = [r[1] for r in con.execute("PRAGMA table_info(urls)")]
    return URL_COLUMNS


def update_url(con: sqlite3.Connection, url_id: int, fields: dict[str, Any]) -> None:
    if not fields:
        return
    cols = url_columns(con)
    items = [(k, v) for k, v in fields.items() if k in cols]
    sets = ", ".join(f"{k} = ?" for k, _ in items)
    con.execute(f"UPDATE urls SET {sets} WHERE id = ?", [v for _, v in items] + [url_id])


def insert_links(con: sqlite3.Connection, rows: Iterable[tuple]) -> None:
    """Rows: (from_id, to_id, kind, anchor, alt, rel, follow, target, position, path, is_internal, has_size_attrs, is_bg, hreflang[, js_only])."""
    con.executemany(
        "INSERT INTO links(from_id, to_id, kind, anchor, alt, rel, follow, target, position, path, is_internal, has_size_attrs, is_bg, hreflang, js_only)"
        " VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)",
        [tuple(r) + ((0,) if len(r) == 14 else ()) for r in rows],
    )
