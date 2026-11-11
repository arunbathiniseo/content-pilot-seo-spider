"""Structured data: extract JSON-LD, Microdata and RDFa from a page and validate it against the schema.org vocabulary
(bundled, offline) and Google's rich-result requirements (required / recommended properties per feature).

Output per page: items [{syntax, type, data, errors, warnings}] plus counters for the urls row."""
from __future__ import annotations

import functools
import json
import re
from pathlib import Path
from typing import Any

VOCAB_PATH = Path(__file__).with_name("data") / "schemaorg.jsonld"
_SCRIPT = re.compile(r"<script\b[^>]*type\s*=\s*[\"']application/ld\+json[\"'][^>]*>(.*?)</script>", re.I | re.S)
_CDATA = re.compile(r"^\s*<!\[CDATA\[(.*?)\]\]>\s*$", re.S)

# Google rich result features: type -> (required, recommended). FAQPage is deliberately absent (Google retired the
# FAQ rich result for most sites in 2023); it is still extracted and schema.org-validated.
GOOGLE: dict[str, tuple[list[str], list[str]]] = {
    "Article": (["headline", "image", "datePublished"], ["author", "dateModified", "publisher"]),
    "NewsArticle": (["headline", "image", "datePublished"], ["author", "dateModified", "publisher"]),
    "BlogPosting": (["headline", "image", "datePublished"], ["author", "dateModified", "publisher"]),
    "Product": (["name"], ["image", "description", "offers", "aggregateRating", "review", "brand", "sku"]),
    "Offer": (["price", "priceCurrency"], ["availability", "url", "priceValidUntil", "itemCondition"]),
    "AggregateOffer": (["lowPrice", "priceCurrency"], ["highPrice", "offerCount"]),
    "BreadcrumbList": (["itemListElement"], []),
    "ListItem": (["position", "name"], ["item"]),
    "HowTo": (["name", "step"], ["image", "totalTime", "estimatedCost", "supply", "tool"]),
    "HowToStep": (["text"], ["name", "image", "url"]),
    "Organization": (["name"], ["url", "logo", "sameAs"]),
    "LocalBusiness": (["name", "address"], ["url", "telephone", "openingHoursSpecification", "geo", "priceRange", "image"]),
    "Event": (["name", "startDate", "location"], ["endDate", "description", "image", "offers", "performer", "organizer", "eventStatus", "eventAttendanceMode"]),
    "Recipe": (["name", "image"], ["author", "datePublished", "description", "prepTime", "cookTime", "totalTime", "recipeYield", "recipeIngredient", "recipeInstructions", "aggregateRating", "nutrition"]),
    "Review": (["itemReviewed", "reviewRating", "author"], ["datePublished", "reviewBody", "publisher"]),
    "Rating": (["ratingValue"], ["bestRating", "worstRating"]),
    "AggregateRating": (["ratingValue", "ratingCount"], ["bestRating", "worstRating", "reviewCount"]),
    "VideoObject": (["name", "thumbnailUrl", "uploadDate"], ["description", "duration", "contentUrl", "embedUrl", "interactionStatistic", "expires"]),
    "JobPosting": (["title", "description", "datePosted", "hiringOrganization", "jobLocation"], ["validThrough", "employmentType", "baseSalary", "identifier"]),
    "SoftwareApplication": (["name", "offers", "aggregateRating"], ["applicationCategory", "operatingSystem", "image"]),
    "Course": (["name", "description", "provider"], ["offers", "hasCourseInstance"]),
    "Dataset": (["name", "description"], ["url", "sameAs", "identifier", "keywords", "license", "creator", "distribution"]),
    "WebSite": ([], ["url", "potentialAction"]),
    "SearchAction": (["target", "query-input"], []),
    "Question": (["name", "acceptedAnswer"], []),
    "Answer": (["text"], []),
}


# properties Google documents that are not (or no longer) in the schema.org release vocabulary
GOOGLE_EXTRA_PROPS = {"query-input", "query", "target", "speakable", "cssSelector", "xpath"}


@functools.lru_cache(maxsize=1)
def vocab() -> dict[str, Any]:
    """schema.org classes, properties (with domainIncludes) and the subclass chain, from the bundled JSON-LD."""
    try:
        g = json.loads(VOCAB_PATH.read_text(encoding="utf-8"))["@graph"]
    except (OSError, ValueError, KeyError):
        return {"classes": set(), "props": {}, "parents": {}}
    sid = lambda x: (x.get("@id") if isinstance(x, dict) else str(x)).split(":", 1)[-1]  # noqa: E731
    classes, props, parents = set(), {}, {}
    for node in g:
        t = node.get("@type")
        types = t if isinstance(t, list) else [t]
        nid = sid(node)
        if "rdfs:Class" in types:
            classes.add(nid)
            sup = node.get("rdfs:subClassOf", [])
            parents[nid] = {sid(x) for x in (sup if isinstance(sup, list) else [sup])}
        if "rdf:Property" in types:
            dom = node.get("schema:domainIncludes", [])
            props[nid] = {sid(x) for x in (dom if isinstance(dom, list) else [dom])}
    return {"classes": classes, "props": props, "parents": parents}


def _ancestors(t: str, parents: dict[str, set[str]]) -> set[str]:
    out, stack = set(), [t]
    while stack:
        x = stack.pop()
        if x in out:
            continue
        out.add(x)
        stack.extend(parents.get(x, ()))
    return out


def _short(v: Any) -> str:
    s = str(v)
    for pre in ("https://schema.org/", "http://schema.org/", "schema:"):
        if s.startswith(pre):
            return s[len(pre):]
    return s


def _types_of(node: dict[str, Any]) -> list[str]:
    t = node.get("@type") or node.get("type") or []
    return [_short(x) for x in (t if isinstance(t, list) else [t]) if x]


def _compact_rdfa(node: dict[str, Any]) -> dict[str, Any]:
    """extruct's RDFa output is expanded JSON-LD with full IRIs; compact schema.org keys and values."""
    out: dict[str, Any] = {}
    for k, v in node.items():
        key = "@type" if k == "@type" else _short(k)
        if isinstance(v, list):
            vals = []
            for x in v:
                if isinstance(x, dict) and set(x) <= {"@value", "@language", "@type"}:
                    vals.append(x.get("@value"))
                elif isinstance(x, dict) and set(x) == {"@id"}:
                    vals.append(x["@id"])
                elif isinstance(x, dict):
                    vals.append(_compact_rdfa(x))
                else:
                    vals.append(_short(x) if key == "@type" else x)
            out[key] = vals[0] if len(vals) == 1 else vals
        else:
            out[key] = v
    return out


def _microdata_to_node(item: dict[str, Any]) -> dict[str, Any]:
    node: dict[str, Any] = {}
    t = item.get("type")
    if t:
        node["@type"] = [_short(x) for x in t] if isinstance(t, list) else _short(t)
    if item.get("id"):
        node["@id"] = item["id"]
    for k, v in (item.get("properties") or {}).items():
        conv = lambda x: _microdata_to_node(x) if isinstance(x, dict) and "properties" in x else x  # noqa: E731
        node[k] = [conv(x) for x in v] if isinstance(v, list) else conv(v)
    return node


def validate(node: dict[str, Any], check_schema: bool = True, check_google: bool = True, _path: str = "") -> tuple[list[str], list[str]]:
    """Errors and warnings for one node (recursing into nested typed nodes)."""
    errors, warnings = [], []
    types = _types_of(node)
    where = f" in {_path}" if _path else ""
    voc = vocab() if check_schema else None
    for t in types:
        if voc and voc["classes"] and t not in voc["classes"]:
            errors.append(f"Unknown schema.org type '{t}'{where}")
    if voc and voc["classes"] and types:
        allowed: set[str] = set()
        for t in types:
            allowed |= _ancestors(t, voc["parents"])
        for k in node:
            if k.startswith("@") or k in ("type", "id"):
                continue
            dom = voc["props"].get(k)
            if dom is None and k in GOOGLE_EXTRA_PROPS:
                continue
            if dom is None:
                errors.append(f"Unknown schema.org property '{k}'{where}")
            elif not (dom & allowed) and "Thing" not in dom and not (dom & {"Thing", "Intangible", "CreativeWork"} & allowed):
                warnings.append(f"Property '{k}' is not expected on {', '.join(types)}{where}")
    if check_google:
        for t in types:
            if t not in GOOGLE:
                continue
            req, rec = GOOGLE[t]
            for p in req:
                if node.get(p) in (None, "", [], {}):
                    errors.append(f"Google: {t} is missing required property '{p}'{where}")
            for p in rec:
                if node.get(p) in (None, "", [], {}):
                    warnings.append(f"Google: {t} is missing recommended property '{p}'{where}")
    for k, v in node.items():
        if k.startswith("@"):
            continue
        for child in (v if isinstance(v, list) else [v]):
            if isinstance(child, dict) and _types_of(child):
                e, w = validate(child, check_schema, check_google, f"{_path + '.' if _path else ''}{k}")
                errors += e
                warnings += w
    return errors, warnings


def analyse(html: str, url: str, check_schema: bool = True, check_google: bool = True, syntaxes: tuple[str, ...] = ("json-ld", "microdata", "rdfa")) -> dict[str, Any]:
    """Extract + validate. Returns {"items": [...], "counts": {...}}."""
    items: list[dict[str, Any]] = []
    parse_errors = 0
    if "json-ld" in syntaxes:
        for m in _SCRIPT.finditer(html):
            raw = m.group(1).strip()
            cd = _CDATA.match(raw)
            if cd:
                raw = cd.group(1).strip()
            if not raw:
                continue
            try:
                data = json.loads(raw)
            except ValueError as exc:
                parse_errors += 1
                items.append({"syntax": "JSON-LD", "type": "(invalid JSON)", "data": raw[:2000], "errors": [f"JSON-LD parse error: {exc}"], "warnings": []})
                continue
            nodes = data if isinstance(data, list) else [data]
            flat: list[dict[str, Any]] = []
            for n in nodes:
                if isinstance(n, dict) and "@graph" in n and isinstance(n["@graph"], list):
                    flat.extend(x for x in n["@graph"] if isinstance(x, dict))
                elif isinstance(n, dict):
                    flat.append(n)
            for n in flat:
                e, w = validate(n, check_schema, check_google)
                items.append({"syntax": "JSON-LD", "type": ", ".join(_types_of(n)) or "(untyped)", "data": n, "errors": e, "warnings": w})
    want = [s for s in syntaxes if s != "json-ld"]
    if want and ("itemscope" in html or "itemtype" in html or "typeof=" in html or "vocab=" in html or "property=" in html):
        try:
            import extruct
            ex = extruct.extract(html, base_url=url, syntaxes=want, errors="ignore", uniform=False)
        except Exception as exc:  # noqa: BLE001
            ex = {}
            parse_errors += 1
            items.append({"syntax": "Microdata/RDFa", "type": "(extraction failed)", "data": "", "errors": [f"Extraction error: {exc}"], "warnings": []})
        for it in ex.get("microdata", []):
            n = _microdata_to_node(it)
            if not _types_of(n):
                continue
            e, w = validate(n, check_schema, check_google)
            items.append({"syntax": "Microdata", "type": ", ".join(_types_of(n)), "data": n, "errors": e, "warnings": w})
        for it in ex.get("rdfa", []):
            n = _compact_rdfa(it)
            if not _types_of(n):
                continue   # the document node (usesVocabulary) and other untyped resources
            e, w = validate(n, check_schema, check_google)
            items.append({"syntax": "RDFa", "type": ", ".join(_types_of(n)), "data": n, "errors": e, "warnings": w})
    types = sorted({t for it in items for t in it["type"].split(", ") if t and not t.startswith("(")})
    counts = {
        "sd_count": len([i for i in items if not i["type"].startswith("(")]),
        "sd_jsonld": sum(1 for i in items if i["syntax"] == "JSON-LD" and not i["type"].startswith("(")),
        "sd_microdata": sum(1 for i in items if i["syntax"] == "Microdata"),
        "sd_rdfa": sum(1 for i in items if i["syntax"] == "RDFa"),
        "sd_errors": sum(len(i["errors"]) for i in items if not i["type"].startswith("(")),
        "sd_warnings": sum(len(i["warnings"]) for i in items),
        "sd_parse_errors": parse_errors,
        "sd_types": ", ".join(types),
    }
    return {"items": items, "counts": counts}
