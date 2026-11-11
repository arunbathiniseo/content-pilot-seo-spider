"""XML sitemap generation from a crawl (Screaming Frog "Sitemaps > XML Sitemap" export).

Pages are selected from the crawl with the usual exclusions (non-200, non-indexable, canonicalised, paginated, PDFs),
written as sitemap.xml with optional lastmod / priority / changefreq / image entries, and split into numbered files
plus a sitemap index when they exceed the protocol limits (50,000 URLs or 50 MB per file)."""
from __future__ import annotations

import io
import sqlite3
import zipfile
from dataclasses import dataclass, asdict
from datetime import datetime, timezone
from typing import Any
from xml.sax.saxutils import escape

MAX_URLS = 50000
MAX_BYTES = 50 * 1024 * 1024 - 1_000_000      # leave room for the envelope


@dataclass
class SitemapOptions:
    include_noindex: bool = False
    include_canonicalised: bool = False
    include_paginated: bool = False
    include_pdfs: bool = False
    only_2xx: bool = True
    lastmod: bool = True                     # from the Last-Modified response header, else the crawl time
    lastmod_fallback_crawl_time: bool = True
    priority: bool = True                    # by crawl depth: 1.0, 0.9, 0.8 … min 0.5
    changefreq: bool = True                  # by crawl depth: daily / weekly / monthly
    images: bool = False                     # <image:image> entries for each page's internal images
    max_urls_per_file: int = MAX_URLS        # lower in tests to exercise the split
    base_url: str = ""                       # where the sitemap files will live (for <loc> in the index); defaults to the site root

    @classmethod
    def from_dict(cls, d: dict[str, Any]) -> "SitemapOptions":
        o = cls()
        for k, v in (d or {}).items():
            if hasattr(o, k):
                cur = getattr(o, k)
                if isinstance(cur, bool):
                    v = str(v).lower() in ("1", "true", "yes", "on") if not isinstance(v, bool) else v
                elif isinstance(cur, int):
                    v = int(v)
                setattr(o, k, v)
        return o


def select(con: sqlite3.Connection, opts: SitemapOptions) -> tuple[list[dict[str, Any]], dict[str, int]]:
    """Rows to include and the counts of what was excluded and why."""
    types = "('HTML'" + (", 'PDF'" if opts.include_pdfs else "") + ")"
    rows = con.execute(f"SELECT id, url, type, status_code, indexable, indexability_status, canonical, rel_prev, depth, last_modified, crawl_ts "
                       f"FROM urls WHERE crawled=1 AND is_internal=1 AND type IN {types} ORDER BY depth, url").fetchall()
    out, excluded = [], {"non_2xx": 0, "noindex": 0, "canonicalised": 0, "paginated": 0}
    for r in rows:
        sc = r["status_code"] or 0
        if opts.only_2xx and not (200 <= sc < 300):
            excluded["non_2xx"] += 1
            continue
        canonicalised = bool(r["canonical"]) and r["canonical"].rstrip("/") != r["url"].rstrip("/")
        if canonicalised and not opts.include_canonicalised:
            excluded["canonicalised"] += 1
            continue
        if not canonicalised and r["indexable"] == 0 and not opts.include_noindex:
            excluded["noindex"] += 1
            continue
        if r["rel_prev"] and not opts.include_paginated:
            excluded["paginated"] += 1
            continue
        out.append(dict(r))
    return out, excluded


def _lastmod(row: dict[str, Any], opts: SitemapOptions) -> str:
    v = (row.get("last_modified") or "").strip()
    if v:
        try:
            from email.utils import parsedate_to_datetime
            return parsedate_to_datetime(v).astimezone(timezone.utc).strftime("%Y-%m-%dT%H:%M:%S+00:00")
        except (TypeError, ValueError):
            pass
    if opts.lastmod_fallback_crawl_time and row.get("crawl_ts"):
        try:
            return datetime.fromisoformat(str(row["crawl_ts"]).replace(" ", "T")).strftime("%Y-%m-%dT%H:%M:%S+00:00")
        except ValueError:
            return str(row["crawl_ts"])[:10]
    return ""


def _priority(depth: int | None) -> str:
    d = depth or 0
    return f"{max(0.5, 1.0 - 0.1 * d):.1f}"


def _changefreq(depth: int | None) -> str:
    d = depth or 0
    return "daily" if d == 0 else "weekly" if d <= 2 else "monthly"


def _images(con: sqlite3.Connection, url_id: int) -> list[str]:
    return [r[0] for r in con.execute(
        "SELECT DISTINCT t.url FROM links l JOIN urls t ON t.id=l.to_id WHERE l.from_id=? AND l.kind='image' AND t.is_internal=1 "
        "AND (t.status_code IS NULL OR t.status_code BETWEEN 200 AND 299) ORDER BY t.url LIMIT 1000", (url_id,))]


def _entry(con: sqlite3.Connection, row: dict[str, Any], opts: SitemapOptions) -> str:
    parts = [f"  <url>\n    <loc>{escape(row['url'])}</loc>\n"]
    if opts.lastmod:
        lm = _lastmod(row, opts)
        if lm:
            parts.append(f"    <lastmod>{lm}</lastmod>\n")
    if opts.changefreq:
        parts.append(f"    <changefreq>{_changefreq(row.get('depth'))}</changefreq>\n")
    if opts.priority:
        parts.append(f"    <priority>{_priority(row.get('depth'))}</priority>\n")
    if opts.images:
        for img in _images(con, row["id"]):
            parts.append(f"    <image:image>\n      <image:loc>{escape(img)}</image:loc>\n    </image:image>\n")
    parts.append("  </url>\n")
    return "".join(parts)


def build(con: sqlite3.Connection, opts: SitemapOptions, site_root: str = "") -> tuple[list[tuple[str, bytes]], dict[str, Any]]:
    """[(filename, xml bytes), ...] plus a summary. One file when it fits; otherwise sitemap1.xml… + sitemap_index.xml."""
    rows, excluded = select(con, opts)
    ns = 'xmlns="http://www.sitemaps.org/schemas/sitemap/0.9"' + (' xmlns:image="http://www.google.com/schemas/sitemap-image/1.1"' if opts.images else "")
    head = f'<?xml version="1.0" encoding="UTF-8"?>\n<urlset {ns}>\n'
    tail = "</urlset>\n"
    files: list[tuple[str, bytes]] = []
    chunk: list[str] = []
    size = len(head) + len(tail)
    limit = max(1, min(MAX_URLS, opts.max_urls_per_file))

    def flush() -> None:
        nonlocal chunk, size
        if chunk:
            files.append(("", (head + "".join(chunk) + tail).encode("utf-8")))
            chunk, size = [], len(head) + len(tail)

    for r in rows:
        e = _entry(con, r, opts)
        if chunk and (len(chunk) >= limit or size + len(e) > MAX_BYTES):
            flush()
        chunk.append(e)
        size += len(e)
    flush()
    if not files:
        files.append(("sitemap.xml", (head + tail).encode("utf-8")))
    elif len(files) == 1:
        files[0] = ("sitemap.xml", files[0][1])
    else:
        files = [(f"sitemap{i + 1}.xml", data) for i, (_, data) in enumerate(files)]
        base = (opts.base_url or site_root or "").rstrip("/")
        now = datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%S+00:00")
        idx = ['<?xml version="1.0" encoding="UTF-8"?>\n<sitemapindex xmlns="http://www.sitemaps.org/schemas/sitemap/0.9">\n']
        idx += [f"  <sitemap>\n    <loc>{escape(base + '/' + name)}</loc>\n    <lastmod>{now}</lastmod>\n  </sitemap>\n" for name, _ in files]
        idx.append("</sitemapindex>\n")
        files.append(("sitemap_index.xml", "".join(idx).encode("utf-8")))
    summary = {"urls": len(rows), "files": [n for n, _ in files], "bytes": sum(len(d) for _, d in files), "excluded": excluded, "options": asdict(opts)}
    return files, summary


def to_zip(files: list[tuple[str, bytes]]) -> bytes:
    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w", zipfile.ZIP_DEFLATED) as z:
        for name, data in files:
            z.writestr(name, data)
    return buf.getvalue()
