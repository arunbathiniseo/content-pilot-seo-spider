"""HTML extraction: everything Screaming Frog pulls out of a page, in one pass."""
from __future__ import annotations

import hashlib
import json
import re
from dataclasses import dataclass, field
from typing import Any
from urllib.parse import urlsplit, unquote

import lxml.html
from lxml import etree

from . import textmetrics as tm
from .urlnorm import normalize, host_of

_WS = re.compile(r"\s+")
_COMMENT = re.compile(r"<!--.*?-->", re.S)
_HEAD_ALLOWED = {"title", "meta", "link", "script", "style", "base", "noscript", "template"}
_TAG = re.compile(r"<\s*([a-zA-Z][a-zA-Z0-9-]*)")
_CSS_URL = re.compile(r"url\(\s*['\"]?([^'\")]+)['\"]?\s*\)", re.I)
_REFRESH = re.compile(r"^\s*(\d+)\s*(?:;\s*url\s*=\s*['\"]?([^'\"]+)['\"]?)?", re.I)
_LANG_CODE = re.compile(r"^(x-default|[a-z]{2,3}(-[a-z]{4})?(-([a-z]{2}|\d{3}))?)$", re.I)
_SKIP_TEXT = {"script", "style", "noscript", "template", "svg", "head", "iframe"}


def clean(s: str | None) -> str:
    return _WS.sub(" ", s or "").strip()


@dataclass
class LinkData:
    to_url: str
    kind: str
    anchor: str = ""
    alt: str | None = None
    rel: str = ""
    follow: int = 1
    target: str = ""
    position: str = ""
    path: str = ""
    has_size_attrs: int = 1
    is_bg: int = 0
    hreflang: str = ""
    js_only: int = 0


@dataclass
class PageData:
    fields: dict[str, Any] = field(default_factory=dict)
    links: list[LinkData] = field(default_factory=list)
    uncrawlable: list[tuple[str, str, str]] = field(default_factory=list)   # href, anchor, reason
    text: str = ""


def _in_head(el) -> bool:
    p = el.getparent()
    while p is not None:
        if p.tag == "head":
            return True
        if p.tag == "body":
            return False
        p = p.getparent()
    return False


def _position(el) -> str:
    p = el
    while p is not None:
        tag = p.tag if isinstance(p.tag, str) else ""
        if tag == "nav":
            return "Navigation"
        if tag == "header":
            return "Header"
        if tag == "footer":
            return "Footer"
        if tag == "aside":
            return "Sidebar"
        if tag in ("main", "article"):
            return "Content"
        ident = ((p.get("class") or "") + " " + (p.get("id") or "")).lower()
        if ident.strip():
            if "breadcrumb" in ident:
                return "Breadcrumb"
            if "footer" in ident:
                return "Footer"
            if "header" in ident or "masthead" in ident:
                return "Header"
            if "nav" in ident or "menu" in ident:
                return "Navigation"
            if "sidebar" in ident or "aside" in ident:
                return "Sidebar"
        p = p.getparent()
    return "Content"


def _rel_tokens(el) -> set[str]:
    return {t.strip().lower() for t in (el.get("rel") or "").split() if t.strip()}


def parse_link_header(value: str, base: str) -> list[dict[str, str]]:
    """Parse an HTTP Link header into [{url, rel, hreflang}]."""
    out = []
    for part in re.split(r",(?=\s*<)", value or ""):
        m = re.match(r"\s*<([^>]+)>\s*(.*)", part)
        if not m:
            continue
        url = normalize(m.group(1), base) or m.group(1)
        params = dict(re.findall(r';\s*([a-zA-Z-]+)\s*=\s*"?([^";]+)"?', m.group(2)))
        out.append({"url": url, "rel": params.get("rel", "").lower(), "hreflang": params.get("hreflang", "")})
    return out


def _raw_validation(html: str) -> dict[str, int]:
    src = _COMMENT.sub("", html)
    low = src.lower()
    heads = [m.start() for m in re.finditer(r"<head[\s>]", low)]
    bodies = [m.start() for m in re.finditer(r"<body[\s>]", low)]
    html_pos = [m.start() for m in re.finditer(r"<html[\s>]", low)]
    out = {
        "head_missing": int(len(heads) == 0),
        "head_multiple": int(len(heads) > 1),
        "body_missing": int(len(bodies) == 0),
        "body_multiple": int(len(bodies) > 1),
        "body_before_html": int(bool(bodies) and (not html_pos or bodies[0] < html_pos[0])),
        "head_not_first": 0,
        "invalid_head_elements": 0,
    }
    if html_pos:
        after = low[html_pos[0]:]
        end = after.find(">")
        m = _TAG.search(after[end + 1:]) if end >= 0 else None
        if m and m.group(1) != "head":
            out["head_not_first"] = 1
    if heads:
        start = heads[0]
        end = low.find("</head>", start)
        region = low[start + 5: end if end > 0 else start + 200000]
        # drop script/style bodies so their contents are not scanned as tags
        region = re.sub(r"<script\b.*?</script>", "<script></script>", region, flags=re.S)
        region = re.sub(r"<style\b.*?</style>", "<style></style>", region, flags=re.S)
        bad = [t for t in _TAG.findall(region) if t.lower() not in _HEAD_ALLOWED]
        out["invalid_head_elements"] = int(bool(bad))
    return out


_BLOCK = {"p", "div", "h1", "h2", "h3", "h4", "h5", "h6", "li", "br", "tr", "td", "th", "section", "article", "header", "footer", "nav", "aside", "main",
          "blockquote", "pre", "hr", "table", "ul", "ol", "dl", "dd", "dt", "figcaption", "figure", "label", "button", "option", "summary", "details", "form", "address"}
_HWS = re.compile(r"[ \t\r\f\v ]+")
_NL = re.compile(r"\s*\n\s*")


def clean_text(s: str) -> str:
    """Like clean(), but keeps one newline between text blocks so sentence-level checks see the real boundaries."""
    s = _HWS.sub(" ", s or "")
    return _NL.sub("\n", s).strip()


def _text_of(body) -> str:
    if body is None:
        return ""
    parts: list[str] = []
    for el in body.iter():
        if not isinstance(el.tag, str):
            continue
        if el.tag in _SKIP_TEXT:
            continue
        skip = False
        p = el.getparent()
        while p is not None and p is not body:
            if p.tag in _SKIP_TEXT:
                skip = True
                break
            p = p.getparent()
        if skip:
            continue
        block = el.tag in _BLOCK
        if block:
            parts.append("\n")              # a block starts a new line; its tail (text after it closes) does too
        if el.text:
            parts.append(el.text)
        if el.tail and el.tag not in _SKIP_TEXT:
            parts.append(("\n" if block else "") + el.tail)
    return clean_text(" ".join(parts))


def parse_html(html: str, url: str, headers: dict[str, str], cfg, status_code: int = 200) -> PageData:
    pd = PageData()
    f = pd.fields
    page_https = url.lower().startswith("https://")
    html_bytes = html.encode("utf-8", "replace")
    f["hash"] = hashlib.md5(html_bytes).hexdigest()

    try:
        doc = lxml.html.document_fromstring(html if html.strip() else "<html></html>")
    except (etree.ParserError, ValueError):
        doc = lxml.html.document_fromstring("<html></html>")
    tree = doc.getroottree()
    head = doc.find("head")
    body = doc.find("body")

    base = url
    base_el = doc.find(".//base")
    if base_el is not None and base_el.get("href"):
        base = normalize(base_el.get("href"), url) or url

    f.update(_raw_validation(html))
    f["lang"] = clean(doc.get("lang") or "")
    from .amp import analyse_page as _amp_page
    f.update(_amp_page(html, doc))

    # --- titles ----------------------------------------------------------
    titles = list(doc.iter("title"))
    titles = [t for t in titles if not any(a.tag == "svg" for a in t.iterancestors())]
    f["title"] = clean(titles[0].text_content()) if titles else ""
    f["title_count"] = len(titles)
    f["title_len"] = len(f["title"])
    f["title_px"] = tm.title_px(f["title"])
    f["title_outside_head"] = int(any(not _in_head(t) for t in titles))

    # --- metas -----------------------------------------------------------
    descs, kws, robots_metas, refresh = [], [], [], None
    md_outside = rb_outside = 0
    for m in doc.iter("meta"):
        name = (m.get("name") or m.get("property") or "").strip().lower()
        http_equiv = (m.get("http-equiv") or "").strip().lower()
        content = m.get("content") or ""
        if name == "description":
            descs.append(clean(content))
            md_outside |= int(not _in_head(m))
        elif name == "keywords":
            kws.append(clean(content))
        elif name in ("robots", "googlebot", "googlebot-news", "bingbot"):
            robots_metas.append(clean(content))
            rb_outside |= int(not _in_head(m))
        elif http_equiv == "refresh" and refresh is None:
            refresh = content
        elif name == "fragment" and content.strip() == "!":
            f["meta_fragment"] = 1
    f["meta_desc"] = descs[0] if descs else ""
    f["md_count"] = len(descs)
    f["md_len"] = len(f["meta_desc"])
    f["md_px"] = tm.desc_px(f["meta_desc"])
    f["md_outside_head"] = md_outside
    f["meta_kw"] = kws[0] if kws else ""
    f["kw_count"] = len(kws)
    f["kw_len"] = len(f["meta_kw"])
    f["meta_robots"] = ", ".join(r for r in robots_metas if r)
    f["robots_outside_head"] = rb_outside
    f["meta_refresh"] = ""
    if refresh:
        m = _REFRESH.match(refresh)
        if m and m.group(2):
            target = normalize(m.group(2), base)
            f["meta_refresh"] = target or m.group(2)
            if target and cfg.crawl_meta_refresh:
                pd.links.append(LinkData(target, "meta_refresh", position="Head"))
        elif m:
            f["meta_refresh"] = f"{m.group(1)}s (self)"
    f["http_refresh"] = headers.get("refresh", "")

    # --- headings --------------------------------------------------------
    headings: list[tuple[int, str]] = []
    h1s, h2s = [], []
    h1_has_alt = 0
    for el in doc.iter("h1", "h2", "h3", "h4", "h5", "h6"):
        if not isinstance(el.tag, str):
            continue
        level = int(el.tag[1])
        text = clean(el.text_content())
        if level == 1:
            if not text:
                alts = [clean(i.get("alt")) for i in el.iter("img") if clean(i.get("alt"))]
                if alts:
                    text = alts[0]
                    h1_has_alt = 1
            h1s.append(text)
        elif level == 2:
            h2s.append(text)
        headings.append((level, text))
    f["h1"] = h1s[0] if h1s else ""
    f["h1_2"] = h1s[1] if len(h1s) > 1 else ""
    f["h1_count"] = len(h1s)
    f["h1_len"] = len(f["h1"])
    f["h1_has_alt"] = h1_has_alt
    f["h2"] = h2s[0] if h2s else ""
    f["h2_2"] = h2s[1] if len(h2s) > 1 else ""
    f["h2_count"] = len(h2s)
    f["h2_len"] = len(f["h2"])
    f["headings"] = json.dumps(headings[:200])
    f["h1_nonseq"] = int(bool(h1s) and headings and headings[0][0] != 1)
    first_h1 = next((i for i, (lv, _) in enumerate(headings) if lv == 1), None)
    first_h2 = next((i for i, (lv, _) in enumerate(headings) if lv == 2), None)
    f["h2_nonseq"] = int(first_h2 is not None and first_h1 is not None and first_h2 < first_h1)

    # --- link elements in head: canonical, pagination, hreflang, amp -----
    css_hrefs: list[str] = []          # stylesheet hrefs, checked for mixed / protocol-relative content below
    canon_hrefs: list[str] = []
    canon_relative = canon_fragment = canon_invalid = canon_outside = 0
    nexts, prevs, hreflangs = [], [], []
    pag_count = hl_outside = 0
    amp_url, mobile_alt = "", ""
    for ln in doc.iter("link"):
        rels = _rel_tokens(ln)
        href = (ln.get("href") or "").strip()
        if "canonical" in rels:
            if not href:
                canon_invalid = 1
                continue
            canon_outside |= int(not _in_head(ln))
            if "#" in href:
                canon_fragment = 1
            if not re.match(r"^[a-z][a-z0-9+.-]*:", href, re.I):
                canon_relative = 1
            n = normalize(href, base)
            if n:
                canon_hrefs.append(n)
            else:
                canon_invalid = 1
        elif "next" in rels or "prev" in rels or "previous" in rels:
            n = normalize(href, base)
            if n:
                pag_count += 1
                if "next" in rels:
                    nexts.append(n)
                else:
                    prevs.append(n)
                if cfg.crawl_pagination:
                    pd.links.append(LinkData(n, "pagination", rel="next" if "next" in rels else "prev", position="Head"))
        elif "alternate" in rels:
            hl = (ln.get("hreflang") or "").strip()
            media = (ln.get("media") or "").lower()
            if hl:
                n = normalize(href, base)
                hreflangs.append({"lang": hl, "url": n or href, "src": "link"})
                hl_outside |= int(not _in_head(ln))
                if n and cfg.crawl_hreflang:
                    pd.links.append(LinkData(n, "hreflang", hreflang=hl, position="Head"))
            elif "max-width" in media or "handheld" in media:
                n = normalize(href, base)
                mobile_alt = n or href
                if n:
                    pd.links.append(LinkData(n, "mobile_alt", position="Head"))
        elif "amphtml" in rels:
            n = normalize(href, base)
            amp_url = n or href
            if n and cfg.crawl_amp:
                pd.links.append(LinkData(n, "amp", position="Head"))
        elif "stylesheet" in rels and href:
            css_hrefs.append(href.strip())
            n = normalize(href, base)
            if n:
                pd.links.append(LinkData(n, "css", position="Head"))

    # HTTP header canonical / hreflang
    link_hdr = headers.get("link", "")
    canonical_http = ""
    if link_hdr:
        for item in parse_link_header(link_hdr, base):
            if item["rel"] == "canonical":
                canonical_http = item["url"]
                canon_hrefs.append(item["url"])
            elif item["rel"] == "alternate" and item["hreflang"]:
                hreflangs.append({"lang": item["hreflang"], "url": item["url"], "src": "http"})
                if cfg.crawl_hreflang:
                    pd.links.append(LinkData(item["url"], "hreflang", hreflang=item["hreflang"], position="HTTP"))
    f["canonical"] = canon_hrefs[0] if canon_hrefs else ""
    f["canonical_count"] = len(canon_hrefs)
    f["canonical_http"] = canonical_http
    f["canonical_conflict"] = int(len(set(canon_hrefs)) > 1)
    f["canonical_relative"] = canon_relative
    f["canonical_fragment"] = canon_fragment
    f["canonical_invalid_attr"] = canon_invalid
    f["canonical_outside_head"] = canon_outside
    if f["canonical"] and cfg.crawl_canonicals:
        pd.links.append(LinkData(f["canonical"], "canonical", position="Head"))
    f["rel_next"] = nexts[0] if nexts else ""
    f["rel_prev"] = prevs[0] if prevs else ""
    f["pagination_count"] = pag_count
    f["pag_multiple"] = int(len(nexts) > 1 or len(prevs) > 1)
    f["hreflang"] = json.dumps(hreflangs)
    f["hreflang_count"] = len(hreflangs)
    f["hreflang_outside_head"] = hl_outside
    f["hl_bad_codes"] = int(any(not _LANG_CODE.match(h["lang"]) for h in hreflangs))
    f["amp_url"] = amp_url
    f["mobile_alt"] = mobile_alt

    # --- hyperlinks -------------------------------------------------------
    js_links = uncrawlable = localhost_links = unsafe_xo = broken_bookmark = 0
    ids = {el.get("id") for el in doc.iter() if isinstance(el.tag, str) and el.get("id")}
    names = {el.get("name") for el in doc.iter("a") if el.get("name")}
    hyperlink_targets: set[str] = set()
    for a in doc.iter("a", "area"):
        href = (a.get("href") or "").strip()
        if not href:
            continue
        anchor = clean(a.text_content())
        alt = None
        if not anchor:
            img_alts = [clean(i.get("alt")) for i in a.iter("img") if i.get("alt") is not None]
            if img_alts:
                alt = img_alts[0]
        low = href.lower()
        if low.startswith("#"):
            frag = unquote(href[1:])
            if frag and frag not in ids and frag not in names:
                broken_bookmark = 1
            continue
        if low.startswith("javascript:"):
            js_links += 1
            uncrawlable += 1
            pd.uncrawlable.append((href, anchor, "javascript"))
            continue
        if low.startswith(("mailto:", "tel:", "sms:", "data:", "ftp:", "file:", "callto:", "whatsapp:", "skype:")):
            continue
        n = normalize(href, base)
        if not n:
            uncrawlable += 1
            pd.uncrawlable.append((href, anchor, "malformed"))
            continue
        h = host_of(n)
        if h in ("localhost", "127.0.0.1") or h.endswith(".local"):
            localhost_links += 1
        rels = _rel_tokens(a)
        follow = 0 if "nofollow" in rels else 1
        target = (a.get("target") or "").strip()
        internal_guess = host_of(n) == host_of(url)
        if target == "_blank" and not internal_guess and not ({"noopener", "noreferrer"} & rels):
            unsafe_xo = 1
        hyperlink_targets.add(n)
        pd.links.append(LinkData(n, "hyperlink", anchor=anchor, alt=alt, rel=" ".join(sorted(rels)), follow=follow,
                                 target=target, position=_position(a), path=tree.getpath(a)))
    f["js_links"] = js_links
    f["uncrawlable_outlinks"] = uncrawlable
    f["localhost_outlinks"] = localhost_links
    f["unsafe_cross_origin"] = unsafe_xo
    f["url_broken_bookmark"] = broken_bookmark
    f["pagination_not_anchor"] = int(any(p not in hyperlink_targets for p in nexts + prevs))

    # --- resources --------------------------------------------------------
    mixed = proto_rel = 0
    for raw in css_hrefs:
        if raw.startswith("//"):
            proto_rel = 1
        n = normalize(raw, base)
        if page_https and n and n.startswith("http://"):
            mixed = 1

    def add_resource(raw: str, kind: str, el, alt=None, size_attrs=1, is_bg=0):
        nonlocal mixed, proto_rel
        raw = (raw or "").strip()
        if not raw or raw.lower().startswith(("data:", "blob:", "javascript:", "about:")):
            return
        if raw.startswith("//"):
            proto_rel = 1
        n = normalize(raw, base)
        if not n:
            return
        if page_https and n.startswith("http://"):
            mixed = 1
        pd.links.append(LinkData(n, kind, alt=alt, position=_position(el) if el is not None else "", has_size_attrs=size_attrs, is_bg=is_bg,
                                 path=tree.getpath(el) if el is not None else ""))

    for img in doc.iter("img"):
        alt = img.get("alt")
        alt = clean(alt) if alt is not None else None
        size_attrs = int(bool(img.get("width")) and bool(img.get("height")))
        add_resource(img.get("src") or img.get("data-src") or "", "image", img, alt=alt, size_attrs=size_attrs)
        if cfg.extract_srcset and img.get("srcset"):
            for cand in img.get("srcset").split(","):
                u = cand.strip().split(" ")[0]
                add_resource(u, "image", img, alt=alt, size_attrs=size_attrs)
    for src in doc.iter("source"):
        if src.get("srcset"):
            parent = src.getparent()
            if parent is not None and parent.tag == "picture":
                for cand in src.get("srcset").split(","):
                    add_resource(cand.strip().split(" ")[0], "image", src, alt="")
        elif src.get("src"):
            add_resource(src.get("src"), "media", src)
    for inp in doc.iter("input"):
        if (inp.get("type") or "").lower() == "image" and inp.get("src"):
            add_resource(inp.get("src"), "image", inp, alt=clean(inp.get("alt")) if inp.get("alt") is not None else None)
    for s in doc.iter("script"):
        if s.get("src"):
            add_resource(s.get("src"), "js", s)
    for fr in doc.iter("iframe"):
        if fr.get("src") and cfg.crawl_iframes:
            add_resource(fr.get("src"), "iframe", fr)
    for v in doc.iter("video", "audio", "embed", "object"):
        add_resource(v.get("src") or v.get("data") or "", "media", v)
    form_insecure = form_on_http = 0
    for fm in doc.iter("form"):
        action = (fm.get("action") or "").strip()
        n = normalize(action, base) if action else None
        if n:
            pd.links.append(LinkData(n, "form", position=_position(fm)))
            if page_https and n.startswith("http://"):
                form_insecure = 1
        if not page_https:
            form_on_http = 1
    # background images: style attributes + <style> blocks
    for el in doc.iter():
        if not isinstance(el.tag, str):
            continue
        st = el.get("style")
        if st and "url(" in st:
            for u in _CSS_URL.findall(st):
                add_resource(u, "image", el, alt="", is_bg=1)
    for st in doc.iter("style"):
        if st.text and "url(" in st.text:
            for u in _CSS_URL.findall(st.text):
                if not u.lower().endswith((".woff", ".woff2", ".ttf", ".otf", ".eot")):
                    add_resource(u, "image", st, alt="", is_bg=1)
    f["mixed_content"] = mixed
    f["protocol_relative"] = proto_rel
    f["form_insecure"] = form_insecure
    f["form_on_http"] = form_on_http

    # --- content metrics --------------------------------------------------
    text = _text_of(body)
    pd.text = text
    wc = tm.word_count(text)
    sc = tm.sentence_count(text)
    f["word_count"] = wc
    f["sentence_count"] = sc
    f["avg_words_sentence"] = round(wc / sc, 2) if sc else 0
    score, band = tm.readability(text)
    f["flesch"] = score
    f["readability"] = band
    f["text_ratio"] = round(100.0 * len(text.encode("utf-8")) / max(1, len(html_bytes)), 2)
    f["text_hash"] = hashlib.md5(text.encode("utf-8")).hexdigest() if text else ""
    f["lorem_ipsum"] = int(tm.has_lorem_ipsum(text) or tm.has_lorem_ipsum(f["title"]))
    f["soft_404"] = int(status_code == 200 and tm.looks_like_soft_404(f["title"], f["h1"], text, wc))

    # --- custom search / extraction --------------------------------------
    custom: dict[str, Any] = {}
    for cs in cfg.custom_search or []:
        name = cs.get("name") or cs.get("text", "")
        hay = html if cs.get("source", "html") == "html" else text
        if cs.get("regex"):
            try:
                hit = bool(re.search(cs.get("text", ""), hay, re.I))
            except re.error:
                hit = False
        else:
            hit = (cs.get("text", "").lower() in hay.lower())
        custom[name] = int(hit if cs.get("contains", True) else not hit)
    for ce in cfg.custom_extraction or []:
        name = ce.get("name") or ce.get("expr", "")
        vals: list[str] = []
        try:
            if ce.get("type") == "regex":
                vals = [m if isinstance(m, str) else m[0] for m in re.findall(ce.get("expr", ""), html, re.S)][:10]
            elif ce.get("type") == "css":
                vals = [clean(e.text_content()) if hasattr(e, "text_content") else str(e) for e in doc.cssselect(ce.get("expr", ""))][:10]
            else:
                res = doc.xpath(ce.get("expr", ""))
                if not isinstance(res, list):
                    res = [res]
                vals = [clean(r.text_content()) if hasattr(r, "text_content") else clean(str(r)) for r in res][:10]
        except Exception as exc:  # bad expression
            vals = [f"ERROR: {exc}"]
        custom[name] = vals
    f["custom"] = json.dumps(custom) if custom else None
    return pd
