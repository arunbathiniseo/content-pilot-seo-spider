"""Issue-specific fixes that need crawl evidence, not a language model.

The AI is good at writing titles, meta descriptions, H1s and alt text. Most other issues are mechanical: the fix is
determined by what the crawler saw (which resource is protocol-relative, which link lacks rel=noopener, what the
redirect's final URL is, which header is missing on every page). For those this module builds the proposal
deterministically from the stored HTML, headers and link graph, with exact before → after snippets. For issues that
still want prose, `evidence()` gives the model the same facts so its brief is about this page, not generic advice.

Kinds added here: code (page-level markup change), site (one proposal for the whole site), url (URL rewrite +
redirect), redirect_link (update links that point at a redirect), image (size / dimensions).
"""
from __future__ import annotations

import json
import re
import sqlite3
import unicodedata
from collections import Counter
from typing import Any
from urllib.parse import parse_qsl, quote, unquote, urlencode, urlsplit, urlunsplit

from . import db as dbm
from .config import CrawlConfig

RULE_KINDS = {"code", "site", "url", "redirect_link", "image"}
PUSHABLE_NOTE = "Developer change: export this fix as a brief (connectors push titles, metas, H1s, alt text, canonicals and redirects only)."

# filter key -> fix kind (everything not listed here keeps the engine's defaults: title / meta / h1 / alt / redirect / canonical / brief)
KIND_BY_KEY: dict[str, str] = {
    "security:protocol_relative": "code", "security:mixed_content": "code", "security:unsafe_cross_origin": "code", "security:form_insecure": "code",
    "url:broken_bookmark": "code", "canonicals:relative": "code", "canonicals:fragment": "code", "canonicals:multiple": "code",
    "canonicals:multiple_conflicting": "code", "canonicals:invalid_attr": "code", "canonicals:outside_head": "code",
    "page_titles:multiple": "code", "page_titles:outside_head": "code", "meta_description:multiple": "code", "meta_description:outside_head": "code",
    "h1:multiple": "code",
    "security:missing_hsts": "site", "security:missing_csp": "site", "security:missing_xcto": "site", "security:missing_xfo": "site",
    "security:missing_referrer_policy": "site", "security:http_urls": "site", "security:form_on_http": "site",
    "url:non_ascii": "url", "url:underscores": "url", "url:uppercase": "url", "url:multiple_slashes": "url", "url:repetitive_path": "url",
    "url:space": "url", "url:parameters": "url", "url:ga_params": "url", "url:over_length": "url", "url:internal_search": "url",
    "response_codes:int_3xx": "redirect_link", "response_codes:int_redirect_chain": "redirect_link", "response_codes:int_meta_refresh": "redirect_link",
    "response_codes:int_http_refresh": "redirect_link", "response_codes:int_redirect_loop": "redirect_link",
    "images:missing_size": "image", "images:over_size": "image", "images:incorrectly_sized": "image",
}

HEADERS = {
    "security:missing_hsts": ("Strict-Transport-Security", "max-age=31536000; includeSubDomains; preload"),
    "security:missing_csp": ("Content-Security-Policy", "default-src 'self'; img-src 'self' data: https:; script-src 'self' 'unsafe-inline' https:; style-src 'self' 'unsafe-inline' https:; frame-ancestors 'self'; upgrade-insecure-requests"),
    "security:missing_xcto": ("X-Content-Type-Options", "nosniff"),
    "security:missing_xfo": ("X-Frame-Options", "SAMEORIGIN"),
    "security:missing_referrer_policy": ("Referrer-Policy", "strict-origin-when-cross-origin"),
}
_RES_ATTRS = ("src", "href", "data", "poster", "srcset", "action")
_RES_TAGS = {"img", "script", "link", "iframe", "video", "audio", "source", "embed", "object", "track", "input"}


def _html(con: sqlite3.Connection, uid: int) -> str:
    row = con.execute("SELECT html FROM bodies WHERE url_id=?", (uid,)).fetchone()
    return dbm.decompress(row["html"]) if row and row["html"] else ""


def _doc(html: str):
    if not html.strip():
        return None
    import lxml.html
    try:
        return lxml.html.document_fromstring(html)
    except Exception:  # noqa: BLE001
        return None


def _server(con: sqlite3.Connection) -> str:
    """Best guess at the web server / platform from response headers, to pick a config snippet."""
    rows = con.execute("SELECT headers FROM urls WHERE crawled=1 AND is_internal=1 AND type='HTML' AND headers IS NOT NULL LIMIT 20").fetchall()
    votes: Counter = Counter()
    for (h,) in rows:
        try:
            d = {k.lower(): str(v).lower() for k, v in json.loads(h).items()}
        except (ValueError, AttributeError):
            continue
        s = d.get("server", "") + " " + d.get("x-powered-by", "") + " " + d.get("via", "")
        for name in ("cloudflare", "nginx", "apache", "litespeed", "iis", "vercel", "netlify", "cloudfront"):
            if name in s:
                votes[name] += 1
        if "wordpress" in s or "wp-json" in h.lower():
            votes["wordpress"] += 1
    return votes.most_common(1)[0][0] if votes else ""


def header_snippet(server: str, header: str, value: str) -> str:
    v = value.replace('"', '\\"')
    out = []
    if server == "cloudflare":
        out.append(f"Cloudflare: Rules → Transform Rules → Modify Response Header → Add static header  {header}: {value}")
    if server in ("nginx", "", "cloudflare"):
        out.append(f'nginx (server block):\n  add_header {header} "{v}" always;')
    if server in ("apache", "litespeed", "wordpress", ""):
        out.append(f'Apache / LiteSpeed (.htaccess or vhost):\n  <IfModule mod_headers.c>\n    Header always set {header} "{v}"\n  </IfModule>')
    if server == "iis":
        out.append(f'IIS (web.config):\n  <httpProtocol><customHeaders><add name="{header}" value="{v}" /></customHeaders></httpProtocol>')
    if server in ("vercel", "netlify"):
        out.append(f'{server}: add to vercel.json "headers" / netlify.toml [[headers]]:  {header} = "{value}"')
    if server == "wordpress":
        out.append(f"WordPress without server access: a security plugin's custom-headers screen, or in functions.php:\n  add_action('send_headers', function() {{ header('{header}: {value}'); }});")
    return "\n".join(out)


# ----------------------------------------------------------------------------- evidence per family
def evidence(con: sqlite3.Connection, cfg: CrawlConfig, key: str, uid: int) -> dict[str, Any]:
    """Facts about this URL for this issue: what exactly is wrong, where, and the data a fix needs."""
    r = con.execute("SELECT * FROM urls WHERE id=?", (uid,)).fetchone()
    if r is None:
        return {}
    url = r["url"]
    page_https = url.lower().startswith("https://")
    ev: dict[str, Any] = {"url": url}
    tab, short = key.split(":", 1)

    if key in ("security:protocol_relative", "security:mixed_content", "security:unsafe_cross_origin", "security:form_insecure", "url:broken_bookmark",
               "canonicals:relative", "canonicals:fragment", "canonicals:multiple", "canonicals:multiple_conflicting", "canonicals:invalid_attr",
               "canonicals:outside_head", "page_titles:multiple", "page_titles:outside_head", "meta_description:multiple", "meta_description:outside_head", "h1:multiple"):
        doc = _doc(_html(con, uid))
        if doc is None:
            ev["note"] = "HTML not stored for this URL (Config → Advanced → Store HTML)"
            return ev
        if key == "security:protocol_relative":
            hits = []
            for el in doc.iter():
                if not isinstance(el.tag, str):
                    continue
                for a in _RES_ATTRS:
                    v = (el.get(a) or "").strip()
                    if v.startswith("//") or (a == "srcset" and " //" in " " + v):
                        hits.append({"tag": el.tag, "attr": a, "value": v[:200]})
            for st in doc.iter("style"):
                for m in re.finditer(r"url\(\s*['\"]?(//[^'\")]+)", st.text or ""):
                    hits.append({"tag": "style", "attr": "url()", "value": m.group(1)[:200]})
            ev["items"] = hits[:50]
            ev["scheme"] = "https:" if page_https else "http:"
        elif key == "security:mixed_content":
            hits = []
            for el in doc.iter():
                if not isinstance(el.tag, str) or el.tag not in _RES_TAGS:
                    continue
                if el.tag == "link" and "stylesheet" not in (el.get("rel") or "").lower() and "icon" not in (el.get("rel") or "").lower():
                    continue
                for a in ("src", "href", "data", "poster", "srcset"):
                    v = (el.get(a) or "").strip()
                    if v.lower().startswith("http://"):
                        https_v = "https://" + v[7:]
                        known = con.execute("SELECT status_code FROM urls WHERE url=?", (https_v,)).fetchone()
                        hits.append({"tag": el.tag, "attr": a, "value": v[:200], "https": https_v[:200], "https_status": known[0] if known else None})
            ev["items"] = hits[:50]
        elif key == "security:unsafe_cross_origin":
            host = urlsplit(url).hostname or ""
            hits = []
            for a in doc.iter("a"):
                if (a.get("target") or "").lower() != "_blank":
                    continue
                rels = {x.lower() for x in (a.get("rel") or "").split()}
                href = (a.get("href") or "").strip()
                h = urlsplit(href).hostname or ""
                if h and h != host and not ({"noopener", "noreferrer"} & rels):
                    hits.append({"href": href[:200], "anchor": (a.text_content() or "").strip()[:80], "rel": " ".join(sorted(rels))})
            ev["items"] = hits[:50]
        elif key == "security:form_insecure":
            ev["items"] = [{"action": (f.get("action") or "")[:200], "method": (f.get("method") or "get").upper()} for f in doc.iter("form") if (f.get("action") or "").lower().startswith("http://")]
        elif key == "url:broken_bookmark":
            ids = {el.get("id") for el in doc.iter() if isinstance(el.tag, str) and el.get("id")} | {el.get("name") for el in doc.iter("a") if el.get("name")}
            hits = []
            for a in doc.iter("a"):
                href = (a.get("href") or "").strip()
                if "#" not in href:
                    continue
                base, frag = href.split("#", 1)
                if frag and (not base or base == url or base.rstrip("/") == url.rstrip("/")) and frag not in ids and frag.lower() != "top":
                    hits.append({"href": href[:200], "anchor": (a.text_content() or "").strip()[:80], "fragment": frag})
            ev["items"] = hits[:50]
            ev["ids_on_page"] = sorted(ids)[:40]
        elif tab == "canonicals":
            links = [{"href": (l.get("href") or "")[:300], "rel": l.get("rel") or "", "in_head": any(p.tag == "head" for p in l.iterancestors())} for l in doc.iter("link") if "canonical" in (l.get("rel") or "").lower()]
            ev.update({"canonicals": links, "page_url": url, "http_canonical": r["canonical_http"] or "", "indexable": r["indexable"], "indexability_status": r["indexability_status"]})
        elif tab in ("page_titles", "meta_description", "h1"):
            if tab == "page_titles":
                els = [{"text": (t.text_content() or "").strip()[:200], "in_head": any(p.tag == "head" for p in t.iterancestors())} for t in doc.iter("title") if not any(p.tag == "svg" for p in t.iterancestors())]
            elif tab == "meta_description":
                els = [{"text": (m.get("content") or "")[:300], "in_head": any(p.tag == "head" for p in m.iterancestors())} for m in doc.iter("meta") if (m.get("name") or "").lower() == "description"]
            else:
                els = [{"text": (h.text_content() or "").strip()[:200]} for h in doc.iter("h1")]
            ev["elements"] = els
            ev["title"], ev["h1"] = r["title"] or "", r["h1"] or ""
        return ev

    if tab == "url":
        ev["suggested_url"] = clean_url(url, cfg)
        ev["inlinks"] = [{"from": f, "anchor": a or ""} for f, a in con.execute("SELECT f.url, l.anchor FROM links l JOIN urls f ON f.id=l.from_id WHERE l.to_id=? AND l.kind='hyperlink' LIMIT 20", (uid,))]
        ev["inlink_count"] = con.execute("SELECT count(*) FROM links WHERE to_id=? AND kind='hyperlink'", (uid,)).fetchone()[0]
        ev["status_code"], ev["canonical"] = r["status_code"], r["canonical"] or ""
        return ev

    if tab == "response_codes":
        ev.update({"status_code": r["status_code"], "redirect_url": r["redirect_url"] or "", "redirect_type": r["redirect_type"] or "", "final_url": r["final_url"] or r["redirect_url"] or "",
                   "final_status": r["final_status"], "chain_length": r["redirect_chain_len"], "loop": bool(r["redirect_loop"])})
        ev["inlinks"] = [{"from": f, "anchor": a or "", "position": p or ""} for f, a, p in con.execute(
            "SELECT f.url, l.anchor, l.position FROM links l JOIN urls f ON f.id=l.from_id WHERE l.to_id=? AND l.kind='hyperlink' ORDER BY f.url LIMIT 50", (uid,))]
        ev["inlink_count"] = con.execute("SELECT count(*) FROM links WHERE to_id=? AND kind='hyperlink'", (uid,)).fetchone()[0]
        return ev

    if tab == "images":
        ev.update({"size_kb": round((r["size"] or 0) / 1024, 1), "width": r["img_width"], "height": r["img_height"], "content_type": r["content_type"] or "", "limit_kb": cfg.image_max_kb})
        ev["pages"] = [{"page": f, "alt": alt, "has_size_attrs": bool(hs)} for f, alt, hs in con.execute(
            "SELECT f.url, l.alt, l.has_size_attrs FROM links l JOIN urls f ON f.id=l.from_id WHERE l.to_id=? AND l.kind='image' ORDER BY f.url LIMIT 30", (uid,))]
        return ev

    # generic context for AI briefs on other tabs
    ev.update({"status_code": r["status_code"], "indexability_status": r["indexability_status"], "title": r["title"] or "", "h1": r["h1"] or "", "h2": r["h2"] or "",
               "word_count": r["word_count"], "canonical": r["canonical"] or "", "meta_robots": r["meta_robots"] or "", "depth": r["depth"], "inlinks": r["inlinks"]})
    if tab == "content":
        ev["readability"], ev["flesch"], ev["near_dup_count"], ev["closest_match"], ev["closest_similarity"] = r["readability"], r["flesch"], r["near_dup_count"], r["closest_match"], r["closest_similarity"]
        try:
            ev["spelling"] = [dict(x) for x in con.execute("SELECT kind, text, suggestion FROM spelling WHERE url_id=? LIMIT 20", (uid,))]
        except sqlite3.OperationalError:
            pass
    if tab == "h2":
        try:
            ev["headings"] = json.loads(r["headings"] or "[]")[:30]
        except ValueError:
            pass
    if tab in ("pagination", "hreflang", "directives", "sitemaps"):
        ev.update({"rel_next": r["rel_next"] or "", "rel_prev": r["rel_prev"] or "", "hreflang": r["hreflang"] or "", "x_robots": r["x_robots"] or "", "in_sitemap": r["in_sitemap"]})
    return ev


def clean_url(url: str, cfg: CrawlConfig) -> str:
    """A tidy version of the URL: lowercase path, hyphens, single slashes, no tracking / search params, ASCII slugs."""
    p = urlsplit(url)
    path = unicodedata.normalize("NFKD", unquote(p.path)).encode("ascii", "ignore").decode()
    path = re.sub(r"/{2,}", "/", path).replace("_", "-").replace(" ", "-").lower()
    path = re.sub(r"-{2,}", "-", path)
    # repetitive segments: /blog/blog/x -> /blog/x
    segs = [s for s in path.split("/")]
    out: list[str] = []
    for s in segs:
        if s and out and out[-1] == s:
            continue
        out.append(s)
    path = "/".join(out) or "/"
    path = quote(path, safe="/-.~%")
    drop = {"utm_source", "utm_medium", "utm_campaign", "utm_term", "utm_content", "gclid", "fbclid", "msclkid", "_ga", "_gl", "mc_cid", "mc_eid", "ref", "source"} | set(cfg.remove_params or [])
    search = set(getattr(cfg, "internal_search_params", []) or []) | {"q", "s", "search", "query"}
    q = [(k, v) for k, v in parse_qsl(p.query, keep_blank_values=True) if k.lower() not in drop and k.lower() not in search]
    return urlunsplit((p.scheme.lower(), p.netloc.lower(), path, urlencode(q), ""))


# ----------------------------------------------------------------------------- rule-based proposals
def rule_fix(con: sqlite3.Connection, cfg: CrawlConfig, key: str, uid: int, ev: dict[str, Any]) -> tuple[str, str, str, str] | None:
    """(field, current, proposed, rationale) for one URL, or None to fall back to an AI brief."""
    url = ev.get("url", "")
    kind = KIND_BY_KEY.get(key, "")
    items = ev.get("items", [])
    if key == "security:protocol_relative":
        if not items:
            return None
        scheme = ev.get("scheme", "https:")
        lines = [f'<{i["tag"]} {i["attr"]}="{i["value"]}">  →  {i["attr"]}="{scheme}{i["value"]}"' if not i["value"].startswith("http") else f'{i["attr"]}: {i["value"]} → {scheme}//…' for i in items]
        prop = (f"Replace {len(items)} protocol-relative reference{'s' if len(items) > 1 else ''} (//host/…) with absolute https:// URLs:\n" + "\n".join(lines[:25]) +
                ("\n…" if len(items) > 25 else "") + "\n\nSite-wide: search the theme / templates and CSS for  //  at the start of src, href and url() values and prefix https:.")
        return ("markup", f"{len(items)} protocol-relative resource(s)", prop, "Protocol-relative URLs inherit the page scheme; on http pages or in emails/AMP/feeds they break or load insecurely. Absolute https URLs are unambiguous.")
    if key == "security:mixed_content":
        if not items:
            return None
        lines = []
        for i in items[:25]:
            st = i.get("https_status")
            ok = " (https version returned 200 in this crawl)" if st == 200 else f" (https version returned {st}, check it)" if st else " (https version not crawled, verify it loads)"
            lines.append(f'<{i["tag"]} {i["attr"]}="{i["value"]}">  →  {i["attr"]}="{i["https"]}"{ok}')
        prop = (f"Load {len(items)} resource{'s' if len(items) > 1 else ''} over HTTPS:\n" + "\n".join(lines) + ("\n…" if len(items) > 25 else "") +
                "\n\nIf a third-party host has no HTTPS, self-host the file. As a safety net add the response header  Content-Security-Policy: upgrade-insecure-requests")
        return ("markup", f"{len(items)} http:// resource(s) on an https page", prop, "Browsers block mixed-content scripts/styles and warn on images; Google treats the page as not fully secure.")
    if key == "security:unsafe_cross_origin":
        if not items:
            return None
        lines = [f'<a href="{i["href"]}" target="_blank"{" rel=" + chr(34) + i["rel"] + chr(34) if i["rel"] else ""}>{i["anchor"] or "…"}</a>  →  rel="{(i["rel"] + " noopener noreferrer").strip()}"' for i in items[:25]]
        prop = (f"Add rel=\"noopener noreferrer\" to {len(items)} external link{'s' if len(items) > 1 else ''} opening in a new tab:\n" + "\n".join(lines) + ("\n…" if len(items) > 25 else "") +
                "\n\nWordPress adds this automatically in the block editor; for theme/menu links set it in the template. Modern browsers imply noopener for target=_blank, but older ones and the referrer leak still justify the attribute.")
        return ("markup", f"{len(items)} unsafe target=_blank link(s)", prop, "Without noopener the opened page can access window.opener and redirect the original tab (reverse tabnabbing); noreferrer also hides the referrer.")
    if key == "security:form_insecure":
        if not items:
            return None
        lines = [f'<form action="{i["action"]}" method="{i["method"]}">  →  action="https://{i["action"][7:]}"' for i in items]
        return ("markup", f"{len(items)} form(s) posting to http://", "Change the form action(s) to HTTPS:\n" + "\n".join(lines) + "\n\nMake sure the endpoint answers over HTTPS first.", "Form data submitted to an http:// action travels in clear text and browsers show a 'not secure' warning on the field.")
    if key == "url:broken_bookmark":
        if not items:
            return None
        lines = [f'<a href="{i["href"]}">{i["anchor"] or "…"}</a>  →  no element with id="{i["fragment"]}" on this page' for i in items[:25]]
        ids = ev.get("ids_on_page", [])
        prop = f"Fix {len(items)} broken bookmark link{'s' if len(items) > 1 else ''}:\n" + "\n".join(lines) + (f"\n\nIds that do exist on the page: {', '.join(ids[:20])}" if ids else "\n\nThe page has no ids; add id attributes to the target sections.")
        return ("markup", f"{len(items)} broken #fragment link(s)", prop, "A link to #fragment that matches no id just scrolls to the top, which looks broken to users and wastes the anchor text.")
    if key.startswith("canonicals:"):
        cans = ev.get("canonicals", [])
        page = ev.get("page_url", url)
        if key == "canonicals:relative":
            lines = [f'<link rel="canonical" href="{c["href"]}">  →  href="{urlsplit(page).scheme}://{urlsplit(page).netloc}{c["href"] if c["href"].startswith("/") else "/" + c["href"]}"' for c in cans if c["href"] and not c["href"].startswith("http")]
            return ("canonical", "; ".join(c["href"] for c in cans), "Use an absolute canonical URL:\n" + "\n".join(lines or [f'<link rel="canonical" href="{page}">']), "Google recommends absolute canonicals; relative ones are resolved differently across mirrors, staging and protocol variants.")
        if key == "canonicals:fragment":
            lines = [f'<link rel="canonical" href="{c["href"]}">  →  href="{c["href"].split("#")[0]}"' for c in cans if "#" in c["href"]]
            return ("canonical", "; ".join(c["href"] for c in cans), "Remove the #fragment from the canonical:\n" + "\n".join(lines), "Canonical URLs must not contain fragments; Google ignores a canonical with one.")
        if key in ("canonicals:multiple", "canonicals:multiple_conflicting"):
            keep = page
            lines = [f'<link rel="canonical" href="{c["href"]}">' for c in cans]
            http_c = ev.get("http_canonical") or ""
            prop = (f"Keep exactly one canonical:\n<link rel=\"canonical\" href=\"{keep}\">\n\nRemove the others:\n" + "\n".join(lines) +
                    (f"\n\nAlso the HTTP Link header declares rel=canonical {http_c}; keep it identical to the HTML one or drop it." if http_c else "") +
                    "\n\nUsually one comes from the SEO plugin and one from the theme or a second plugin: disable canonical output in one of them.")
            return ("canonical", "; ".join(c["href"] for c in cans), prop, "With several canonicals (especially conflicting ones) Google ignores them all and picks its own.")
        if key == "canonicals:invalid_attr":
            return ("canonical", "; ".join(c["href"] for c in cans), f'Fix the canonical element so it is exactly:\n<link rel="canonical" href="{page}">\n\nFound: ' + "; ".join(f'rel="{c["rel"]}" href="{c["href"]}"' for c in cans), "Extra or malformed attributes (rel values, missing href, href on the wrong element) stop the canonical from being read.")
        if key == "canonicals:outside_head":
            return ("canonical", "; ".join(c["href"] for c in cans), f'Move the canonical into <head>:\n<link rel="canonical" href="{cans[0]["href"] if cans else page}">\n\nIt currently appears in <body>, often because a plugin prints it after an unclosed <head> or a stray <body> tag; check for invalid HTML before it (the Validation tab lists head/body problems).', "Only <link> elements inside <head> are honoured; in <body> the canonical is ignored.")
    if key in ("page_titles:multiple", "page_titles:outside_head", "meta_description:multiple", "meta_description:outside_head", "h1:multiple"):
        els = ev.get("elements", [])
        what = {"page_titles": "<title>", "meta_description": '<meta name="description">', "h1": "<h1>"}[key.split(":")[0]]
        found = "\n".join(f"  {i + 1}. {e.get('text', '')[:120] or '(empty)'}{'' if e.get('in_head', True) else '   ← in <body>'}" for i, e in enumerate(els))
        if key.endswith(":multiple"):
            prop = f"Keep one {what} and remove the other {max(0, len(els) - 1)}:\n{found}\n\nKeep #1 unless another is the intended one. Multiple copies usually mean the theme and an SEO plugin both output it — turn one off."
            return ("markup", f"{len(els)} × {what}", prop, "Search engines pick one of several elements unpredictably; a single element puts you in control.")
        prop = f"Move {what} into <head>:\n{found}\n\nIt is rendered after <head> closed — look for an element printed before <head> or a stray tag that closes it early (Validation tab)."
        return ("markup", f"{what} outside <head>", prop, "Elements outside <head> are treated as body content and ignored as metadata.")
    if kind == "url":
        new = ev.get("suggested_url", "")
        n = ev.get("inlink_count", 0)
        if not new or new == url:
            return None
        inl = "\n".join(f"  {i['from']}  ({i['anchor'][:40] or 'no anchor'})" for i in ev.get("inlinks", [])[:10])
        prop = (f"Rename:\n  {url}\n→ {new}\n\n301 redirect the old URL to the new one, update the canonical, then update the {n} internal link{'s' if n != 1 else ''} to the new address" +
                (f":\n{inl}" + ("\n  …" if n > 10 else "") if inl else "") + "\n\nIf the URL cannot change (e.g. a parameter the page needs), keep it and make sure the canonical points at the clean version.")
        return ("url", url, prop, {"url:non_ascii": "Non-ASCII characters are percent-encoded in links and logs, which is ugly and error-prone.", "url:underscores": "Google treats hyphens as word separators but not underscores.",
                                   "url:uppercase": "URLs are case-sensitive; mixed case creates duplicate variants.", "url:multiple_slashes": "Repeated slashes create duplicate URLs for the same page.",
                                   "url:parameters": "Parameter URLs multiply crawlable variants; keep one clean URL.", "url:ga_params": "Tracking parameters should never be linked internally; they create duplicates.",
                                   "url:space": "Spaces are encoded as %20 and break when shared.", "url:over_length": f"Over {cfg.url_max_chars} characters; shorter URLs are easier to share and display.",
                                   "url:repetitive_path": "Repeated path segments usually mean a routing or linking bug.", "url:internal_search": "Internal search result pages should not be crawlable or linked."}.get(key, "Cleaner URL."))
    if kind == "redirect_link":
        final = ev.get("final_url") or ev.get("redirect_url") or ""
        n = ev.get("inlink_count", 0)
        if not final:
            return None
        inl = "\n".join(f"  {i['from']}  ({i['anchor'][:40] or 'no anchor'}{', ' + i['position'] if i.get('position') else ''})" for i in ev.get("inlinks", [])[:15])
        chain = f" via a chain of {ev['chain_length']} redirects" if ev.get("chain_length", 0) > 1 else ""
        if ev.get("loop"):
            prop = f"{url} redirects in a loop. Fix the redirect rule so it resolves to one final 200 page, then update the {n} link(s) pointing at it:\n{inl}"
            return ("redirect_to", url, prop, "Redirect loops never resolve; browsers and crawlers give up.")
        fs = ev.get("final_status")
        warn = f"\n\nNote: the final URL returned {fs} — fix or remove the link instead." if fs and fs != 200 else ""
        prop = f"Point {n} internal link{'s' if n != 1 else ''} directly at the final URL{chain}:\n  {url}  →  {final}\n\nLinks to update:\n{inl}" + ("\n  …" if n > 15 else "") + warn
        return ("redirect_to", url, prop, "Linking to a redirect costs a hop per visit and dilutes link equity; link straight to the destination.")
    if kind == "image":
        pages = ev.get("pages", [])
        w, h, kb = ev.get("width"), ev.get("height"), ev.get("size_kb", 0)
        if key == "images:missing_size":
            dims = f' width="{w}" height="{h}"' if w and h else ' width="…" height="…" (intrinsic size unknown: read it from the file)'
            lines = [f"  {p['page']}" for p in pages if not p.get("has_size_attrs")][:15]
            prop = f"Add explicit dimensions to <img src=\"{url}\"{dims}>\n\nPages embedding it without width/height:\n" + "\n".join(lines) + ("\n  …" if len(pages) > 15 else "")
            return ("markup", url, prop, "Width and height let the browser reserve space, preventing layout shift (CLS).")
        if key == "images:over_size":
            prop = (f"{url} is {kb} KB ({ev.get('content_type')}{', ' + str(w) + '×' + str(h) if w and h else ''}); target ≤ {ev.get('limit_kb')} KB.\n"
                    f"Convert to WebP/AVIF, compress (quality ~80), and resize to the largest displayed size" + (f" (currently {w}×{h})" if w and h else "") +
                    f". Serve responsive sizes with srcset. Used on {len(pages)} page(s):\n" + "\n".join(f"  {p['page']}" for p in pages[:10]) + ("\n  …" if len(pages) > 10 else ""))
            return ("image", f"{kb} KB", prop, "Large images are the most common cause of slow LCP; most can shrink 60–80% without visible loss.")
        if key == "images:incorrectly_sized":
            prop = (f"{url} is {w}×{h} but displayed at a different size on {len(pages)} page(s). Export it at the displayed size (and 2× for retina) and use srcset/sizes so each device gets the right file.\n" + "\n".join(f"  {p['page']}" for p in pages[:10]))
            return ("image", f"{w}×{h}", prop, "Scaling images in the browser wastes bytes (too big) or looks blurry (too small).")
    return None


_SIG = {"security:protocol_relative": "value", "security:mixed_content": "value", "security:unsafe_cross_origin": "href", "security:form_insecure": "action", "url:broken_bookmark": "href"}


def split_shared(key: str, evs: dict[int, dict[str, Any]]) -> tuple[list[dict[str, Any]], dict[str, int]]:
    """Items that recur across many affected pages come from the template, not the page. Returns the shared items
    (each with `pages` = how many pages carry it) and strips them from every page's evidence in place."""
    sig = _SIG.get(key)
    if not sig or len(evs) < 3:
        return [], {}
    count: Counter = Counter()
    sample: dict[str, dict[str, Any]] = {}
    for ev in evs.values():
        for it in {i.get(sig): i for i in ev.get("items", [])}.values():
            count[it.get(sig)] += 1
            sample.setdefault(it.get(sig), it)
    threshold = max(3, int(0.3 * len(evs)))
    shared_keys = {k for k, n in count.items() if n >= threshold}
    if not shared_keys:
        return [], {}
    for ev in evs.values():
        ev["items"] = [i for i in ev.get("items", []) if i.get(sig) not in shared_keys]
    shared = [{**sample[k], "pages": count[k]} for k in sorted(shared_keys, key=lambda k: -count[k])]
    return shared, {k: count[k] for k in shared_keys}


def template_fix(con: sqlite3.Connection, cfg: CrawlConfig, key: str, shared: list[dict[str, Any]], n_pages: int) -> tuple[str, str, str, str, str]:
    """One proposal for items that appear on many pages: fix the template / theme once."""
    root = cfg.start_url or (con.execute("SELECT url FROM urls WHERE is_internal=1 AND crawled=1 ORDER BY depth, id LIMIT 1").fetchone() or ["site"])[0]
    p = urlsplit(root)
    site = f"{p.scheme}://{p.netloc}/" if p.netloc else root
    scheme = "https:"
    if key == "security:protocol_relative":
        lines = [f'<{i["tag"]} {i["attr"]}="{i["value"]}">  →  {i["attr"]}="{scheme}{i["value"]}"   (on {i["pages"]} of {n_pages} pages)' for i in shared]
        prop = (f"These protocol-relative references appear on most affected pages, so they live in the theme / template (header, footer or a plugin), not in page content. Fix them once:\n"
                + "\n".join(lines) + "\n\nWhere to look: the theme's header.php / footer.php, a widget or tag-manager snippet, enqueued scripts in functions.php, and the CSS files for url(// …).")
        return (site, "template", f"{len(shared)} template resource(s) on {n_pages} pages", prop, "Protocol-relative URLs inherit the page scheme; absolute https is unambiguous. One template edit fixes every page.")
    if key == "security:mixed_content":
        lines = [f'<{i["tag"]} {i["attr"]}="{i["value"]}">  →  {i["attr"]}="{i["https"]}"   (on {i["pages"]} pages)' for i in shared]
        return (site, "template", f"{len(shared)} template resource(s) on {n_pages} pages", "These http:// resources are loaded by the template on most affected pages. Switch them to https once in the theme / plugin:\n" + "\n".join(lines), "Mixed content on every page is a template problem; fix the source, not the pages.")
    if key == "security:unsafe_cross_origin":
        lines = [f'<a href="{i["href"]}" target="_blank">{i["anchor"] or "…"}</a>  →  add rel="noopener noreferrer"   (on {i["pages"]} pages)' for i in shared]
        return (site, "template", f"{len(shared)} template link(s) on {n_pages} pages", "These new-tab links appear on most pages (menu, footer or social links in the template). Add rel=\"noopener noreferrer\" once:\n" + "\n".join(lines), "Template links repeat on every page; one edit fixes all of them.")
    if key == "security:form_insecure":
        lines = [f'<form action="{i["action"]}">  →  action="https://{i["action"][7:]}"   (on {i["pages"]} pages)' for i in shared]
        return (site, "template", f"{len(shared)} template form(s)", "A form in the template posts to http://. Change its action once:\n" + "\n".join(lines), "Template form on every page.")
    lines = [f'<a href="{i["href"]}">{i.get("anchor") or "…"}</a>   (on {i["pages"]} pages)' for i in shared]
    return (site, "template", f"{len(shared)} template link(s)", "These broken #fragment links are in the template (menu / footer). Fix the target ids or the hrefs once:\n" + "\n".join(lines), "Template links repeat on every page.")


def site_fix(con: sqlite3.Connection, cfg: CrawlConfig, key: str, url_ids: list[int]) -> tuple[str, str, str, str, str]:
    """One proposal for the whole site: (url, field, current, proposed, rationale)."""
    root = cfg.start_url or (con.execute("SELECT url FROM urls WHERE is_internal=1 AND crawled=1 ORDER BY depth, id LIMIT 1").fetchone() or ["site"])[0]
    p = urlsplit(root)
    site = f"{p.scheme}://{p.netloc}/" if p.netloc else root
    n = len(url_ids)
    server = _server(con)
    where = f" ({server})" if server else ""
    if key in HEADERS:
        header, value = HEADERS[key]
        notes = {"security:missing_csp": "\n\nStart in report-only mode (Content-Security-Policy-Report-Only) and tighten the sources once the console shows what the site actually loads; a too-strict CSP breaks scripts and styles.",
                 "security:missing_hsts": "\n\nOnly after every subdomain works over HTTPS; preload is permanent.", "security:missing_xfo": "\n\nUse 'frame-ancestors' in CSP for finer control; DENY if nothing should frame the site."}.get(key, "")
        prop = f"Send the response header on every URL ({n} URLs lack it):\n  {header}: {value}\n\n{header_snippet(server, header, value)}{notes}"
        return (site, "header", f"{n} URLs without {header}", prop, f"One server-level setting fixes all {n} URLs; this is not a per-page change.")
    if key == "security:http_urls":
        prop = (f"{n} URLs are served over http://. Redirect all HTTP to HTTPS (301) at the server/CDN level{where}, then update internal links, canonicals and the sitemap to https.\n\n"
                "nginx:\n  server { listen 80; return 301 https://$host$request_uri; }\nApache (.htaccess):\n  RewriteEngine On\n  RewriteCond %{HTTPS} off\n  RewriteRule ^ https://%{HTTP_HOST}%{REQUEST_URI} [L,R=301]\n"
                "Cloudflare: SSL/TLS → Edge Certificates → Always Use HTTPS.\n\nThen add Strict-Transport-Security.")
        return (site, "redirect", f"{n} http:// URLs", prop, "HTTP pages are flagged 'not secure', split ranking signals with their https twins and can be tampered with in transit.")
    if key == "security:form_on_http":
        prop = f"{n} pages served over http:// contain forms. Serve the whole site over HTTPS (see HTTP URLs) — forms on http pages are marked 'Not secure' by browsers even when the action is https."
        return (site, "redirect", f"{n} http pages with forms", prop, "Browsers warn users typing into forms on insecure pages.")
    return (site, "brief", f"{n} URLs", f"{n} URLs affected by {key}. See each URL's details.", "Site-level issue.")
