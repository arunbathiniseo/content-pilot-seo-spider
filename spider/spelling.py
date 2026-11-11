"""Spelling & grammar (Screaming Frog "Content > Spelling / Grammar Errors").

Spelling: offline dictionary check (pyspellchecker word frequency lists, en/de/fr/es/pt/it/nl/ru) on the extracted
page text, with the site's own vocabulary learnt automatically: an unknown word that appears on many pages is a brand
or product term, not a typo. Grammar: a small set of high-precision rules offline, or LanguageTool (self-hosted or
languagetool.org) when a server URL is configured, which then handles both spelling and grammar.

Results go to the `spelling` table (one row per finding) and urls.spelling_errors / grammar_errors.
"""
from __future__ import annotations

import re
import sqlite3
from collections import Counter
from typing import Any

import httpx

from . import db as dbm
from .config import CrawlConfig

SUPPORTED = {"en", "de", "fr", "es", "pt", "it", "nl", "ru"}
MAX_CHARS = 20000              # LanguageTool's public limit; also keeps rule checks bounded
_WORD = re.compile(r"[A-Za-zÀ-ɏ][A-Za-zÀ-ɏ'’-]{2,}")
_SENT_END = re.compile(r"[.!?]")
_ABBR = {"e.g", "i.e", "etc", "vs", "no", "fig", "approx", "inc", "ltd", "co", "st", "dr", "mr", "mrs", "ms", "jan", "feb", "mar", "apr", "jun", "jul", "aug", "sep", "sept", "oct", "nov", "dec"}
_VOWEL_SOUND_EXC = {"one", "once", "uni", "unit", "use", "user", "usual", "euro", "europe", "ewe", "ufo", "url", "utility", "unique", "uniform", "union", "universe", "university"}
_SILENT_H = {"hour", "hours", "honest", "honour", "honor", "heir", "herb", "homage"}
CONFUSIONS = [
    (re.compile(r"\b(should|could|would|must|might)\s+of\b", re.I), "{0} have", "'{0} of' should be '{0} have'"),
    (re.compile(r"\balot\b", re.I), "a lot", "'alot' is two words: 'a lot'"),
    (re.compile(r"\byour\s+welcome\b", re.I), "you're welcome", "'your welcome' should be 'you're welcome'"),
    (re.compile(r"\bthere\s+(own|is\s+no\s+way\s+to\s+their)\b", re.I), "their {0}", "'there own' should be 'their own'"),
    (re.compile(r"\bits\s+a\s+(?=\w)", re.I), "it's a ", "'its a' should be 'it's a' (it is)"),
    (re.compile(r"\bless\s+(people|customers|users|items|payments|transactions|errors|clicks)\b", re.I), "fewer {0}", "'less' with a countable noun: 'fewer {0}'"),
    (re.compile(r"\bin\s+regards\s+to\b", re.I), "in regard to / regarding", "'in regards to' should be 'in regard to' or 'regarding'"),
    (re.compile(r"\bfor\s+all\s+intensive\s+purposes\b", re.I), "for all intents and purposes", "'intensive purposes' should be 'intents and purposes'"),
]


UK_WORDS = set("""programme cheque cheques tyre tyres kerb grey aluminium mum whilst amongst learnt spelt dreamt pyjamas jewellery licence
defence offence practise practised enquiry enquiries favourite favourites behaviour behaviours colour colours neighbour neighbours organisation
organisations realise realised recognise recognised analyse analysed catalogue dialogue centre centres metre metres litre litres theatre fibre
sceptical storey mould plough draught manoeuvre ageing judgement acknowledgement instalment instalments enrol enrolment fulfil fulfilment skilful
wilful cancelled cancelling travelled travelling traveller modelling labelled labelling totalled signalling counsellor jeweller marvellous woollen
aeroplane artefact cosy doughnut moustache omelette paralyse pretence sulphur yoghurt focussed focussing authorise authorised authorising
unauthorised authorisation optimise optimised optimisation customise customised customisation minimise maximise utilise summarise prioritise
prioritised categorise standardise specialise specialised personalise personalised monetise monetisation digitise digitisation tokenise tokenisation
capitalise capitalisation finalise finalised visualise characterise emphasise apologise criticise mobilise normalise localisation globalisation
centralised decentralised unrealised honour honoured humour labour labours rumour savour vapour flavour flavours harbour endeavour endeavours
fulfilling totalling levelled modeller""".split())
# everyday modern / web / payments vocabulary the frequency-list dictionary predates
MODERN_WORDS = set("""etc ios iphone ipad android wifi api apis saas paas uk eu gdpr psd pos crm erp seo sem ux ui app apps login logins logout signup signups
checkout checkouts email emails online offline website websites webpage webpages webinar webinars blog blogs blogger vlog podcast upsell upsells cross-sell
onboarding offboarding startup startups scaleup fintech fintechs insurtech proptech regtech edtech healthtech cashflow contactless chargeback chargebacks
cardholder cardholders payout payouts paywall paylink paylinks ecommerce omnichannel multichannel walkthrough walkthroughs cardless cashless paperless
gateway gateways username usernames passcode passcodes hashtag hashtags selfie smartphone smartphones smartwatch tablet tablets laptop laptops desktop
workflow workflows dashboard dashboards tooltip tooltips dropdown dropdowns popup popups plugin plugins widget widgets emoji emojis livestream backend
frontend fullstack devops microservice microservices webhook webhooks sdk sdks json xml html css javascript typescript python wordpress woocommerce shopify
magento bigcommerce squarespace wix stripe paypal klarna visa mastercard amex applepay googlepay fx kyc aml kyb iban bic sepa ach bacs chaps swift
instore in-store in-app add-on add-ons opt-in opt-out e-commerce multi-site multi-location tech-savvy real-time end-to-end cost-effective
scalable scalability actionable autofill autocomplete geolocation geotag geotagging retargeting remarketing conversion conversions upselling
tokenisation tokenization tokenised tokenized unsubscribe unsubscribed resend resent reorder reorders preorder preorders prepay prepaid repay repayment
repayments refundable nonrefundable subscriber subscribers subscription subscriptions freemium downtime uptime roadmap roadmaps rebrand rebranding
influencer influencers marketplace marketplaces onsite offsite checkout-free tap-to-pay pay-by-link pay-by-bank
https http url urls auth oauth sso mfa otp crypto cryptocurrency cryptocurrencies blockchain stablecoin stablecoins cashback touchpoint touchpoints
dropshipping dropship cafe cafes mpos epos agentic chatbot chatbots copilot genai llm llms openai anthropic gemini automations automation
repricing geofencing geofence upskill reskill onboard onboarded offboard reconfigure reconfigured pre-authorisation pre-authorization preauth
reconciliation reconciliations settlement settlements remittance remittances surcharge surcharges surcharging interchange acquirer acquirers issuer issuers
pci dss qr nfc emv tipping tippable tokenise nps csat kpi kpis roi arpu ltv cac mrr arr b2b b2c d2c p2p""".split())
_SUFFIX = [("ies", "y"), ("es", ""), ("s", ""), ("ed", ""), ("ed", "e"), ("ing", ""), ("ing", "e"), ("er", ""), ("ers", ""), ("est", ""), ("ly", ""), ("ness", ""), ("ment", ""), ("ments", "")]
_UK_US = [("isation", "ization"), ("isations", "izations"), ("ised", "ized"), ("ising", "izing"), ("ises", "izes"), ("isable", "izable"), ("isably", "izably"),
          ("ise", "ize"), ("yse", "yze"), ("ysed", "yzed"), ("ysing", "yzing"),
          ("our", "or"), ("ours", "ors"), ("oured", "ored"), ("ouring", "oring"), ("ogue", "og"), ("ence", "ense"), ("re", "er"), ("ll", "l")]


def known(sc, w: str, _depth: int = 0) -> bool:
    """Dictionary lookup that also accepts British spellings, plurals/inflections, possessives, contractions and
    hyphenated compounds whose parts are known."""
    if not w or w.isdigit():
        return True
    if w in UK_WORDS or w in MODERN_WORDS or sc.known([w]):
        return True
    if "'" in w:
        base = w.split("'")[0]
        return known(sc, base, _depth) if base else True
    if "-" in w:
        return all(known(sc, part, _depth) for part in w.split("-") if part)
    if _depth >= 1:
        return False
    for suf, repl in _SUFFIX:
        if w.endswith(suf) and len(w) - len(suf) + len(repl) >= 3 and known(sc, w[: -len(suf)] + repl, 1):
            return True
    for uk, us in _UK_US:
        if uk in w:
            i = w.rfind(uk)
            if known(sc, w[:i] + us + w[i + len(uk):], 1):
                return True
    return False


def _ctx(text: str, start: int, end: int, width: int = 40) -> str:
    a, b = max(0, start - width), min(len(text), end + width)
    return ("…" if a else "") + text[a:start] + "[" + text[start:end] + "]" + text[end:b] + ("…" if b < len(text) else "")


def grammar_rules(text: str) -> list[dict[str, Any]]:
    """Offline, high-precision grammar findings: [{text, suggestion, message, context, rule}]."""
    out: list[dict[str, Any]] = []
    # repeated word ("the the"); allow legitimate doubles
    for m in re.finditer(r"\b([A-Za-z]+) \1\b", text, flags=re.I):
        w = m.group(1).lower()
        if w in ("had", "that", "is", "very", "bye", "no", "so", "ha", "hip", "cha", "chop", "beri"):
            continue
        out.append({"text": m.group(0), "suggestion": m.group(1), "message": f"Repeated word '{m.group(1)}'", "context": _ctx(text, m.start(), m.end()), "rule": "repeated_word"})
    # a / an
    for m in re.finditer(r"\b(a|an) ([A-Za-z][A-Za-z-]*)", text):
        art, word = m.group(1), m.group(2)
        wl = word.lower()
        if word.isupper() and len(word) <= 5:
            continue   # acronyms are read by letter name (an SEO, a URL): skip
        vowel = wl[0] in "aeiou" and not any(wl.startswith(x) for x in _VOWEL_SOUND_EXC)
        if wl in _SILENT_H or wl.startswith(("hour", "honest", "honour", "honor", "heir")):
            vowel = True
        if art.lower() == "a" and vowel:
            out.append({"text": m.group(0), "suggestion": f"an {word}", "message": f"'a {word}' should be 'an {word}'", "context": _ctx(text, m.start(), m.end()), "rule": "a_an"})
        elif art.lower() == "an" and not vowel:
            out.append({"text": m.group(0), "suggestion": f"a {word}", "message": f"'an {word}' should be 'a {word}'", "context": _ctx(text, m.start(), m.end()), "rule": "a_an"})
    # lowercase sentence start (after . ! ? and a space), ignoring abbreviations and things like e.g./URLs
    for m in re.finditer(r"(\b[\w.]+)([.!?]) +([a-z][a-z]+)\b", text):
        prev = m.group(1).lower().rstrip(".")
        if prev in _ABBR or "." in prev or len(prev) <= 1 or prev.isdigit():
            continue
        out.append({"text": m.group(3), "suggestion": m.group(3).capitalize(), "message": "Sentence starts with a lowercase letter", "context": _ctx(text, m.start(3), m.end(3)), "rule": "sentence_case"})
    # doubled punctuation (a space before punctuation is usually a text-extraction artefact, so it is not flagged)
    for m in re.finditer(r"([!?]){2,}|(,){2,}", text):
        out.append({"text": m.group(0), "suggestion": m.group(0)[0], "message": "Doubled punctuation", "context": _ctx(text, m.start(), m.end()), "rule": "double_punct"})
    # missing space after a comma between two words (not decimals / URLs / domains)
    for m in re.finditer(r"[A-Za-z]{2,},(?=[A-Za-z]{2,}\b)", text):
        out.append({"text": text[m.start():m.end() + 1], "suggestion": text[m.start():m.end()] + " ", "message": "Missing space after comma", "context": _ctx(text, m.start(), m.end() + 1), "rule": "space_after_comma"})
    # common confusions
    for rx, repl, msg in CONFUSIONS:
        for m in rx.finditer(text):
            g = m.group(1) if m.groups() else ""
            out.append({"text": m.group(0), "suggestion": repl.format(g), "message": msg.format(g), "context": _ctx(text, m.start(), m.end()), "rule": "confusion"})
    return out


class LanguageToolError(Exception):
    pass


def languagetool_check(url: str, text: str, language: str, timeout: float = 30.0) -> tuple[list[dict[str, Any]], list[dict[str, Any]]]:
    """(spelling, grammar) findings from a LanguageTool server (/v2/check). Raises LanguageToolError."""
    base = url.rstrip("/")
    if not base.endswith("/v2"):
        base += "/v2"
    try:
        r = httpx.post(f"{base}/check", data={"text": text[:MAX_CHARS], "language": language or "auto"}, timeout=timeout)
    except httpx.HTTPError as exc:
        raise LanguageToolError(f"LanguageTool unreachable at {url}: {exc}")
    if r.status_code >= 400:
        raise LanguageToolError(f"LanguageTool error {r.status_code}: {r.text[:200]}")
    spelling, grammar = [], []
    for m in r.json().get("matches", []):
        off, ln = m.get("offset", 0), m.get("length", 0)
        cat = (m.get("rule", {}).get("category", {}).get("id") or "").upper()
        item = {"text": text[off:off + ln], "suggestion": ", ".join(x.get("value", "") for x in m.get("replacements", [])[:3]),
                "message": m.get("message", ""), "context": _ctx(text, off, off + ln), "rule": m.get("rule", {}).get("id", "")}
        (spelling if cat in ("TYPOS", "MISSPELLING") or "SPELL" in cat else grammar).append(item)
    return spelling, grammar


def _scope(cfg: CrawlConfig) -> str:
    scope = "u.is_internal=1 AND u.type='HTML' AND u.crawled=1 AND u.status_code BETWEEN 200 AND 299"
    if cfg.ignore_non_indexable_for_issues:
        scope += " AND u.indexable=1"
    return scope


def _lang_for(page_lang: str | None, cfg: CrawlConfig) -> str:
    code = (page_lang or "").split("-")[0].lower()
    return code if code in SUPPORTED else (cfg.spell_language or "en").split("-")[0].lower()


def compute(con: sqlite3.Connection, cfg: CrawlConfig, log=print) -> dict[str, int]:
    """Run spelling (if cfg.spell_check) and grammar (if cfg.grammar_check) over the crawl. Returns counts."""
    con.execute("""CREATE TABLE IF NOT EXISTS spelling (id INTEGER PRIMARY KEY, url_id INTEGER NOT NULL, kind TEXT NOT NULL, text TEXT, suggestion TEXT,
                   message TEXT, context TEXT, rule TEXT)""")
    con.execute("CREATE INDEX IF NOT EXISTS idx_spelling_url ON spelling(url_id)")
    con.execute("DELETE FROM spelling")
    con.execute("UPDATE urls SET spelling_errors=0, grammar_errors=0")
    if not (cfg.spell_check or cfg.grammar_check):
        return {"pages": 0, "spelling": 0, "grammar": 0}
    rows = con.execute(f"SELECT u.id, u.url, u.lang, b.text FROM urls u JOIN bodies b ON b.url_id=u.id WHERE {_scope(cfg)} AND b.text IS NOT NULL").fetchall()
    pages = [(r["id"], r["url"], r["lang"], dbm.decompress(r["text"])[:MAX_CHARS]) for r in rows]
    ignore = {w.strip().lower() for w in (cfg.spell_ignore or []) if w.strip()}
    use_lt = bool(cfg.grammar_check and cfg.grammar_lt_url.strip())
    spell_total = gram_total = 0
    findings: list[tuple] = []
    per_page_sp: dict[int, int] = {}
    per_page_gr: dict[int, int] = {}

    if cfg.spell_check and not use_lt:
        from spellchecker import SpellChecker
        checkers: dict[str, SpellChecker] = {}
        unknown_by_page: dict[int, Counter] = {}
        page_count: Counter = Counter()
        for uid, _url, lang, text in pages:
            code = _lang_for(lang, cfg)
            if code not in checkers:
                try:
                    checkers[code] = SpellChecker(language=code, distance=1)   # distance 1: suggestions in ms, not seconds
                except Exception:  # noqa: BLE001
                    checkers[code] = SpellChecker(language="en", distance=1)
            sc = checkers[code]
            text = text.replace("\u2019", "'").replace("\u2018", "'")
            words = [w for w in _WORD.findall(text) if not w.isupper() and not any(ch.isdigit() for ch in w)]
            cand = {w.lower().strip("'-") for w in words}
            cand = {w for w in cand if len(w) >= 3 and w not in ignore}
            unk = {w for w in cand if not known(sc, w)}
            # words that start with a capital in the text and are unknown are usually names; keep only lowercase-looking typos
            lowered_present = {w.lower() for w in words if w[0].islower()}
            unk = {w for w in unk if w in lowered_present}
            unknown_by_page[uid] = Counter(w.lower() for w in words if w.lower() in unk)
            for w in unk:
                page_count[w] += 1
        # site vocabulary: unknown words on many pages are brand / product terms, not typos
        n = max(1, len(pages))
        learned = {w for w, c in page_count.items() if c >= max(cfg.spell_learn_min_pages, 2) and c / n >= cfg.spell_learn_share / 100.0}
        suggestions: dict[str, str] = {}          # one dictionary lookup per distinct word, not per occurrence
        for uid, _url, lang, text in pages:
            text = text.replace("\u2019", "'").replace("\u2018", "'")
            sc = checkers[_lang_for(lang, cfg)]
            cnt = 0
            for w, k in unknown_by_page.get(uid, Counter()).most_common():
                if w in learned:
                    continue
                m = re.search(r"(?i)\b" + re.escape(w) + r"\b", text)
                ctx = _ctx(text, m.start(), m.end()) if m else ""
                if w not in suggestions:
                    sugg = sc.correction(w) if len(w) <= 20 else None
                    suggestions[w] = sugg if sugg and sugg != w else ""
                findings.append((uid, "spelling", w, suggestions[w], f"Possible misspelling ({k}×)" if k > 1 else "Possible misspelling", ctx, "dictionary"))
                cnt += 1
            per_page_sp[uid] = cnt
            spell_total += cnt
        log(f"[spelling] {spell_total} misspellings on {sum(1 for v in per_page_sp.values() if v)} pages; learnt {len(learned)} site words")

    if cfg.grammar_check:
        for uid, _url, lang, text in pages:
            if use_lt:
                try:
                    sp, gr = languagetool_check(cfg.grammar_lt_url, text, _lang_for(lang, cfg) if _lang_for(lang, cfg) != "en" else "en-GB")
                except LanguageToolError as exc:
                    log(f"[grammar] {exc}")
                    break
                sp = [x for x in sp if x["text"].lower() not in ignore]
                for x in sp:
                    findings.append((uid, "spelling", x["text"], x["suggestion"], x["message"], x["context"], x["rule"]))
                per_page_sp[uid] = len(sp)
                spell_total += len(sp)
            else:
                gr = grammar_rules(text)
            for x in gr:
                findings.append((uid, "grammar", x["text"], x["suggestion"], x["message"], x["context"], x["rule"]))
            per_page_gr[uid] = len(gr)
            gram_total += len(gr)

    con.executemany("INSERT INTO spelling(url_id, kind, text, suggestion, message, context, rule) VALUES (?,?,?,?,?,?,?)", findings)
    con.executemany("UPDATE urls SET spelling_errors=? WHERE id=?", [(v, k) for k, v in per_page_sp.items()])
    con.executemany("UPDATE urls SET grammar_errors=? WHERE id=?", [(v, k) for k, v in per_page_gr.items()])
    return {"pages": len(pages), "spelling": spell_total, "grammar": gram_total}


def findings_for(con: sqlite3.Connection, url_id: int) -> list[dict[str, Any]]:
    try:
        return [dict(r) for r in con.execute("SELECT kind, text, suggestion, message, context, rule FROM spelling WHERE url_id=? ORDER BY kind, text", (url_id,))]
    except sqlite3.OperationalError:
        return []


def summary_rows(con: sqlite3.Connection) -> list[list[Any]]:
    """Report: each misspelling / grammar issue with how many pages it appears on and an example URL."""
    try:
        return [list(r) for r in con.execute(
            """SELECT s.kind, s.text, max(s.suggestion), count(DISTINCT s.url_id) AS pages, min(u.url)
               FROM spelling s JOIN urls u ON u.id=s.url_id GROUP BY s.kind, lower(s.text) ORDER BY pages DESC, s.text""")]
    except sqlite3.OperationalError:
        return []
