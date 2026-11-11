"""robots.txt handling (Google-compatible matching via protego) and XML sitemap parsing."""
from __future__ import annotations

import gzip
import re
from dataclasses import dataclass, field
from urllib.parse import urlsplit

from protego import Protego
from lxml import etree

from .urlnorm import normalize


@dataclass
class RobotsInfo:
    url: str
    status_code: int | None
    text: str = ""
    parser: Protego | None = None
    sitemaps: list[str] = field(default_factory=list)

    def allowed(self, url: str, ua: str) -> bool:
        if self.parser is None:
            return True
        try:
            return self.parser.can_fetch(url, ua)
        except Exception:
            return True


def robots_url_for(url: str) -> str:
    p = urlsplit(url)
    return f"{p.scheme}://{p.netloc}/robots.txt"


def build_robots(url: str, status_code: int | None, body: bytes | None) -> RobotsInfo:
    """A 4xx (or missing) robots.txt allows everything; 5xx is treated as allow-all too, like Googlebot after retries."""
    info = RobotsInfo(url=url, status_code=status_code)
    if status_code != 200 or not body:
        return info
    text = body.decode("utf-8", "replace")
    info.text = text
    try:
        info.parser = Protego.parse(text)
        info.sitemaps = [s for s in info.parser.sitemaps]
    except Exception:
        info.parser = None
    return info


def robots_token(user_agent: str) -> str:
    """Product token used for robots matching: first word of the UA string without version."""
    ua = user_agent or ""
    m = re.search(r"compatible;\s*([A-Za-z0-9_-]+)", ua)
    if m:
        return m.group(1)
    first = re.split(r"[\s/]", ua.strip())[0] if ua.strip() else "*"
    return first or "*"


@dataclass
class SitemapResult:
    is_index: bool
    children: list[str]                # child sitemap URLs (index) or nothing
    urls: list[tuple[str, str]]        # (loc, lastmod)
    size: int


MAX_SITEMAP_BYTES = 200 * 1024 * 1024   # decompressed; the protocol's own limit is 50 MB


def gunzip_capped(raw: bytes, cap: int = MAX_SITEMAP_BYTES) -> bytes:
    import zlib
    out = zlib.decompressobj(16 + zlib.MAX_WBITS).decompress(raw, cap + 1)
    if len(out) > cap:
        raise ValueError("sitemap is larger than the limit once decompressed")
    return out


def parse_sitemap(url: str, body: bytes) -> SitemapResult:
    raw = body
    if url.lower().endswith(".gz") or raw[:2] == b"\x1f\x8b":
        try:
            raw = gunzip_capped(raw)
        except ValueError:
            raw = b""   # a decompression bomb: treat as an empty sitemap
        except Exception:
            pass
    size = len(raw)
    try:
        root = etree.fromstring(raw, parser=etree.XMLParser(recover=True, huge_tree=True, resolve_entities=False, no_network=True))
    except Exception:
        root = None
    if root is None:
        # plain text sitemap: one URL per line
        lines = [ln.strip() for ln in raw.decode("utf-8", "replace").splitlines()]
        urls = [(normalize(ln) or ln, "") for ln in lines if ln.startswith("http")]
        return SitemapResult(False, [], urls, size)
    tag = etree.QName(root).localname.lower()
    if tag == "sitemapindex":
        children = []
        for sm in root.iter():
            if isinstance(sm.tag, str) and etree.QName(sm).localname.lower() == "loc" and sm.text:
                children.append(sm.text.strip())
        return SitemapResult(True, children, [], size)
    urls: list[tuple[str, str]] = []
    for u in root.iter():
        if not isinstance(u.tag, str) or etree.QName(u).localname.lower() != "url":
            continue
        loc = lastmod = ""
        for ch in u:
            if not isinstance(ch.tag, str):
                continue
            ln = etree.QName(ch).localname.lower()
            if ln == "loc" and ch.text:
                loc = ch.text.strip()
            elif ln == "lastmod" and ch.text:
                lastmod = ch.text.strip()
        if loc:
            urls.append((normalize(loc) or loc, lastmod))
    return SitemapResult(False, [], urls, size)
