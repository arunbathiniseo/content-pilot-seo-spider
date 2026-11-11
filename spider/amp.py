"""AMP page checks (Screaming Frog "AMP" tab). Page-level validation happens at parse time for pages whose <html>
carries the amp / ⚡ attribute; cross-page checks (non-200 AMP responses, return links, indexability) run in analysis.
This is the structural subset of the AMP validator, not the full amphtml-validator rule set."""
from __future__ import annotations

import json
import re
import sqlite3
from typing import Any

_AMP_RUNTIME = re.compile(r"<script\b[^>]*\bsrc=[\"']https://cdn\.ampproject\.org/v0\.js[\"']", re.I)
_SCRIPT = re.compile(r"<script\b([^>]*)>", re.I)
_STYLE = re.compile(r"<style\b([^>]*)>(.*?)</style>", re.I | re.S)
_DOCTYPE = re.compile(r"^\s*(?:<!--.*?-->\s*)*<!doctype\s+html\s*>", re.I | re.S)
_DISALLOWED = ("img", "video", "audio", "iframe", "embed", "object", "frame", "frameset", "applet", "param")
_EMPTY: dict[str, Any] = {"is_amp": 0, "amp_issues": None, "amp_issue_count": 0, "amp_missing_script": 0, "amp_missing_boilerplate": 0,
                          "amp_disallowed_html": 0, "amp_missing_canonical": 0, "amp_other_errors": 0}


def is_amp_doc(doc) -> bool:
    try:
        return doc is not None and ("amp" in doc.attrib or "⚡" in doc.attrib or "⚡️" in doc.attrib)
    except AttributeError:
        return False


def analyse_page(html: str, doc) -> dict[str, Any]:
    """Fields for the urls row. Non-AMP pages get zeros."""
    # lxml drops the non-ASCII ⚡ attribute name, so also look at the raw <html> tag
    if not (is_amp_doc(doc) or re.search(r"<html\b[^>]*\s(?:amp|\u26a1)(?:\ufe0f)?[\s=>/]", html[:3000], re.I)):
        return dict(_EMPTY)
    low = html.lower()
    issues: list[str] = []
    other = 0
    if not _DOCTYPE.match(html):
        issues.append("Missing or invalid doctype: AMP requires <!doctype html>"); other += 1
    if doc.find("head") is None:
        issues.append("Missing <head>"); other += 1
    if doc.find("body") is None:
        issues.append("Missing <body>"); other += 1
    if not re.search(r"<meta\b[^>]*charset\s*=\s*[\"']?utf-8", low):
        issues.append("Missing <meta charset=\"utf-8\"> (must be the first child of <head>)"); other += 1
    if not re.search(r"<meta\b[^>]*name\s*=\s*[\"']viewport[\"'][^>]*content\s*=\s*[\"'][^\"']*width=device-width", low):
        issues.append("Missing <meta name=\"viewport\" content=\"width=device-width…\">"); other += 1
    missing_canonical = int(not re.search(r"<link\b[^>]*rel\s*=\s*[\"'][^\"']*\bcanonical\b", low))
    if missing_canonical:
        issues.append("Missing <link rel=\"canonical\"> to the non-AMP page")
    missing_script = int(not _AMP_RUNTIME.search(html))
    if missing_script:
        issues.append("Missing AMP runtime <script async src=\"https://cdn.ampproject.org/v0.js\">")
    missing_boilerplate = int("amp-boilerplate" not in low)
    if missing_boilerplate:
        issues.append("Missing <style amp-boilerplate> (and its <noscript> twin)")
    # custom scripts: only the AMP runtime / components and JSON blocks are allowed
    custom_js = 0
    for m in _SCRIPT.finditer(html):
        attrs = m.group(1).lower()
        if "cdn.ampproject.org" in attrs:
            continue
        if re.search(r"type\s*=\s*[\"']application/(ld\+)?json[\"']", attrs):
            continue
        custom_js += 1
    if custom_js:
        issues.append(f"Custom JavaScript is not allowed in AMP ({custom_js} <script> element{'s' if custom_js > 1 else ''})")
    # styles: one <style amp-custom> up to 75 KB, plus the boilerplate; nothing else
    customs, others = [], 0
    for m in _STYLE.finditer(html):
        attrs = m.group(1).lower()
        if "amp-custom" in attrs:
            customs.append(len(m.group(2).encode("utf-8")))
        elif "amp-boilerplate" in attrs:
            continue
        else:
            others += 1
    if others:
        issues.append(f"<style> without amp-custom / amp-boilerplate ({others})"); other += 1
    if len(customs) > 1:
        issues.append("More than one <style amp-custom>"); other += 1
    if customs and customs[0] > 75000:
        issues.append(f"<style amp-custom> is {customs[0] // 1024} KB (limit 75 KB)"); other += 1
    if re.search(r"style\s*=\s*[\"'][^\"']*!important", low):
        issues.append("!important is not allowed in AMP CSS"); other += 1
    # disallowed HTML elements (need their amp- equivalents)
    disallowed: dict[str, int] = {}
    for tag in _DISALLOWED:
        n = len(re.findall(rf"<{tag}\b", low))
        if n:
            disallowed[tag] = n
    if disallowed:
        issues.append("Disallowed HTML, use the amp- components: " + ", ".join(f"<{t}>×{n}" for t, n in disallowed.items()))
    return {"is_amp": 1, "amp_issues": json.dumps(issues), "amp_issue_count": len(issues), "amp_missing_script": missing_script,
            "amp_missing_boilerplate": missing_boilerplate, "amp_disallowed_html": sum(disallowed.values()) + custom_js,
            "amp_missing_canonical": missing_canonical, "amp_other_errors": other}


def analyse_crawl(con: sqlite3.Connection) -> None:
    """Cross-page AMP checks, run from analysis."""
    con.executescript("""
    UPDATE urls SET amp_non200=0, amp_missing_return=0, amp_canonical_nonindexable=0, amp_indexable=0, amp_missing_html_tag=0;
    -- a page's amphtml link points at a URL that did not answer 200
    UPDATE urls SET amp_non200=1
      WHERE amp_url IS NOT NULL AND amp_url<>'' AND crawled=1
        AND EXISTS (SELECT 1 FROM urls t WHERE t.url=urls.amp_url AND t.crawled=1 AND (t.status_code IS NULL OR t.status_code<>200));
    -- linked as AMP but the page does not declare <html amp>
    UPDATE urls SET amp_missing_html_tag=1
      WHERE coalesce(is_amp,0)=0 AND crawled=1 AND type='HTML' AND status_code=200
        AND EXISTS (SELECT 1 FROM urls c WHERE c.amp_url=urls.url);
    -- AMP page must canonical to a non-AMP page (not itself, not another AMP page)
    UPDATE urls SET amp_missing_return=1
      WHERE is_amp=1 AND crawled=1
        AND (canonical IS NULL OR canonical='' OR canonical=url OR EXISTS (SELECT 1 FROM urls t WHERE t.url=urls.canonical AND t.is_amp=1));
    UPDATE urls SET amp_canonical_nonindexable=1
      WHERE is_amp=1 AND canonical IS NOT NULL AND canonical<>'' AND canonical<>url
        AND EXISTS (SELECT 1 FROM urls t WHERE t.url=urls.canonical AND t.crawled IN (1,3) AND (t.indexable=0 OR t.status_code<>200));
    UPDATE urls SET amp_indexable=1 WHERE is_amp=1 AND crawled=1 AND indexable=1 AND (canonical IS NULL OR canonical='' OR canonical=url);
    """)
