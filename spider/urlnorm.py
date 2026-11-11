"""URL normalisation, classification and the URL tab checks."""
from __future__ import annotations

import posixpath
import re
from urllib.parse import urlsplit, urlunsplit, parse_qsl, urlencode, quote, unquote

from .config import INTERNAL_SEARCH_PARAMS

_DEFAULT_PORTS = {"http": "80", "https": "443"}

IMAGE_EXT = {".jpg", ".jpeg", ".png", ".gif", ".webp", ".avif", ".svg", ".bmp", ".ico", ".tif", ".tiff"}
JS_EXT = {".js", ".mjs"}
CSS_EXT = {".css"}
PDF_EXT = {".pdf"}
MEDIA_EXT = {".mp4", ".webm", ".mp3", ".ogg", ".wav", ".mov", ".m4a", ".m4v", ".avi"}
FONT_EXT = {".woff", ".woff2", ".ttf", ".otf", ".eot"}
FLASH_EXT = {".swf"}
XML_EXT = {".xml", ".rss", ".atom"}


def normalize(url: str, base: str | None = None, remove_params: list[str] | None = None) -> str | None:
    """Resolve against base, drop the fragment, lowercase scheme/host, strip default ports.
    Returns None for non-http(s) or unusable URLs."""
    from urllib.parse import urljoin

    url = (url or "").strip().replace("\n", "").replace("\r", "").replace("\t", "")
    if not url:
        return None
    if base:
        url = urljoin(base, url)
    try:
        parts = urlsplit(url)
    except ValueError:
        return None
    if parts.scheme not in ("http", "https") or not parts.netloc:
        return None
    host = parts.hostname or ""
    host = host.lower().rstrip(".")
    if not host:
        return None
    netloc = host
    if parts.port and str(parts.port) != _DEFAULT_PORTS.get(parts.scheme):
        netloc = f"{host}:{parts.port}"
    if parts.username:
        netloc = f"{parts.username}{':' + parts.password if parts.password else ''}@{netloc}"
    path = parts.path or "/"
    # percent-encode characters that must not appear raw (spaces, non-ascii)
    path = quote(unquote(path), safe="/%:@!$&'()*+,;=-._~")
    query = parts.query
    if remove_params and query:
        kept = [(k, v) for k, v in parse_qsl(query, keep_blank_values=True) if k not in remove_params]
        query = urlencode(kept, doseq=True)
    query = quote(unquote(query), safe="=&%:@!$'()*+,;/?-._~") if query else ""
    return urlunsplit((parts.scheme.lower(), netloc, path, query, ""))


def host_of(url: str) -> str:
    return (urlsplit(url).hostname or "").lower()


def registered_domain(host: str) -> str:
    """Cheap eTLD+1 (handles the common two-part public suffixes)."""
    parts = host.split(".")
    if len(parts) <= 2:
        return host
    two_part = {"co.uk", "org.uk", "ac.uk", "gov.uk", "me.uk", "ltd.uk", "plc.uk", "net.uk",
                "com.au", "net.au", "org.au", "co.nz", "co.in", "co.jp", "com.br", "co.za", "com.sg", "com.mx"}
    if ".".join(parts[-2:]) in two_part:
        return ".".join(parts[-3:])
    return ".".join(parts[-2:])


def is_internal(url: str, start_url: str, crawl_subdomains: bool = False, cdn_domains: list[str] | None = None) -> bool:
    h = host_of(url)
    s = host_of(start_url)
    if h == s:
        return True
    if h.startswith("www.") and h[4:] == s or s.startswith("www.") and s[4:] == h:
        return True
    if cdn_domains and any(h == d or h.endswith("." + d) for d in cdn_domains):
        return True
    if crawl_subdomains and registered_domain(h) == registered_domain(s):
        return True
    return False


def in_start_folder(url: str, start_url: str) -> bool:
    sp = urlsplit(start_url).path or "/"
    folder = sp if sp.endswith("/") else posixpath.dirname(sp) + "/"
    return (urlsplit(url).path or "/").startswith(folder)


def extension(url: str) -> str:
    path = urlsplit(url).path
    ext = posixpath.splitext(path)[1].lower()
    return ext


def classify(url: str, content_type: str | None) -> str:
    """Screaming Frog content type buckets."""
    ct = (content_type or "").split(";")[0].strip().lower()
    if ct:
        if "html" in ct or ct == "application/xhtml+xml":
            return "HTML"
        if "javascript" in ct or ct == "application/ecmascript" or ct == "text/jscript":
            return "JavaScript"
        if ct == "text/css":
            return "CSS"
        if ct.startswith("image/"):
            return "Images"
        if ct == "application/pdf":
            return "PDF"
        if ct.startswith("video/") or ct.startswith("audio/"):
            return "Media"
        if "font" in ct or ct in ("application/vnd.ms-fontobject",):
            return "Fonts"
        if "xml" in ct or ct in ("application/rss+xml", "application/atom+xml"):
            return "XML"
        if ct == "application/x-shockwave-flash":
            return "Flash"
        if ct == "text/plain":
            return "Other"
        return "Other"
    ext = extension(url)
    if ext in IMAGE_EXT:
        return "Images"
    if ext in JS_EXT:
        return "JavaScript"
    if ext in CSS_EXT:
        return "CSS"
    if ext in PDF_EXT:
        return "PDF"
    if ext in MEDIA_EXT:
        return "Media"
    if ext in FONT_EXT:
        return "Fonts"
    if ext in FLASH_EXT:
        return "Flash"
    if ext in XML_EXT:
        return "XML"
    return "Unknown"


def guess_type_from_url(url: str) -> str:
    """Pre-fetch guess (used to decide whether to crawl a resource)."""
    return classify(url, None)


def folder_depth(url: str) -> int:
    path = urlsplit(url).path or "/"
    segs = [s for s in path.split("/") if s]
    if path.endswith("/") or not segs:
        return len(segs)
    return len(segs) - 1


# --- URL tab checks -------------------------------------------------------

_GA_PARAMS = re.compile(r"(^|[?&])(utm_[a-z]+|gclid|fbclid|_ga|_gl|dclid|msclkid)=", re.I)


def url_checks(url: str, max_len: int = 115) -> dict[str, int]:
    parts = urlsplit(url)
    path = unquote(parts.path or "/")
    segs = [s for s in path.split("/") if s]
    query_keys = [k.lower() for k, _ in parse_qsl(parts.query, keep_blank_values=True)]
    return {
        "url_non_ascii": int(any(ord(c) > 127 for c in unquote(url))),
        "url_underscores": int("_" in path),
        "url_uppercase": int(any(c.isupper() for c in parts.path + parts.query)),
        "url_multiple_slashes": int("//" in path),
        "url_repetitive_path": int(len(segs) != len(set(segs))),
        "url_space": int(" " in path or "%20" in parts.path),
        "url_internal_search": int(any(k in INTERNAL_SEARCH_PARAMS for k in query_keys)),
        "url_parameters": int(bool(parts.query)),
        "url_ga_params": int(bool(_GA_PARAMS.search("?" + parts.query)) if parts.query else 0),
        "url_over_length": int(len(url) > max_len),
    }
