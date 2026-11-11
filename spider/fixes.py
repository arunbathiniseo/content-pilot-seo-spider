"""Fix engine: turn flagged URLs into reviewable proposals (AI-written where text is needed), then push the
approved ones through a connector."""
from __future__ import annotations

import asyncio
import json
import sqlite3
import time
from datetime import datetime, timezone
from typing import Any
from urllib.parse import urlsplit

from . import db as dbm
from .ai import AIError, Provider, parse_json_block
from .config import CrawlConfig
from .connectors import make_connector
from .issues import TABS, filters_by_key
from . import usage as usage_log
from . import fixkinds

# which filter maps to which kind of fix
KIND_BY_TAB = {"page_titles": "title", "meta_description": "meta_description", "h1": "h1"}
KIND_BY_KEY = {
    "images:missing_alt_text": "alt_text", "images:missing_alt_attr": "alt_text", "images:alt_over": "alt_text",
    "response_codes:4xx": "redirect", "response_codes:int_4xx": "redirect", "response_codes:no_response": "redirect",
    "canonicals:missing": "canonical",
}
AI_KINDS = {"title", "meta_description", "h1", "alt_text", "redirect", "brief"}
LIMITS = {"title": "50-60 characters", "meta_description": "120-155 characters", "h1": "20-70 characters", "alt_text": "under 100 characters"}


def _now() -> str:
    return datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M:%S")


def kind_for(key: str) -> str:
    if key in KIND_BY_KEY:
        return KIND_BY_KEY[key]
    if key in fixkinds.KIND_BY_KEY:
        return fixkinds.KIND_BY_KEY[key]
    return KIND_BY_TAB.get(key.split(":")[0], "brief")


def is_rule_kind(kind: str) -> bool:
    """Kinds built from crawl evidence without a model (no API key needed)."""
    return kind == "canonical" or kind in fixkinds.RULE_KINDS


class FixJob:
    """One background generation run; status is polled by the UI."""
    def __init__(self):
        self.state = "idle"       # idle | running | done | error | cancelled
        self.total = 0
        self.done = 0
        self.created = 0
        self.errors: list[str] = []
        self.started = 0.0
        self.label = ""
        self._cancel = False

    def cancel(self):
        self._cancel = True

    def status(self) -> dict[str, Any]:
        return {"state": self.state, "total": self.total, "done": self.done, "created": self.created, "errors": self.errors[-5:],
                "elapsed": round(time.time() - self.started, 1) if self.started else 0, "label": self.label}


def _page_context(con: sqlite3.Connection, uid: int) -> dict[str, Any]:
    r = con.execute("SELECT id, url, title, meta_desc, h1, h2, word_count, status_code, canonical, headings FROM urls WHERE id=?", (uid,)).fetchone()
    body = con.execute("SELECT text FROM bodies WHERE url_id=?", (uid,)).fetchone()
    text = dbm.decompress(body["text"]) if body and body["text"] else ""
    try:
        heads = [h[1] for h in json.loads(r["headings"] or "[]") if h[0] == 2][:6]
    except (json.JSONDecodeError, TypeError):
        heads = []
    return {"id": r["id"], "url": r["url"], "title": r["title"] or "", "meta_description": r["meta_desc"] or "", "h1": r["h1"] or "",
            "h2s": heads, "word_count": r["word_count"] or 0, "status": r["status_code"], "canonical": r["canonical"] or "",
            "excerpt": text[:900]}


def _system_prompt(cfg: CrawlConfig, kind: str) -> str:
    brand = f' The brand is "{cfg.ai_brand}"; keep its name where titles already carry it.' if cfg.ai_brand else ""
    house = f"\nHouse style: {cfg.ai_instructions.strip()}" if cfg.ai_instructions.strip() else ""
    task = {
        "title": "Write a new <title> for each page. Lead with the page's primary topic, be specific, no clickbait, no keyword stuffing, " + LIMITS["title"] + ".",
        "meta_description": "Write a meta description for each page: one or two plain sentences that say what the page offers and why to click, " + LIMITS["meta_description"] + ", no quotes.",
        "h1": "Write a single H1 for each page that states the page's subject plainly, " + LIMITS["h1"] + ", distinct from but consistent with the title.",
        "alt_text": "Write concise, descriptive alt text for each image from its filename and the page it appears on, " + LIMITS["alt_text"] + ". Describe what the image shows; do not start with 'image of'.",
        "redirect": "For each broken URL choose the best redirect target from the candidate list (the closest live page by topic/slug). If none fits, propose the site homepage and say so in the rationale.",
        "brief": ("For each page write a short, concrete fix brief (2-5 sentences) a developer or editor can act on. Each input carries an `evidence` object with the "
                  "facts the crawler found for THIS page (counts, URLs, elements, headings, scores): base the brief on that evidence, quote the specific values, "
                  "and never give the same generic advice to every page. If the evidence shows the issue is really site-wide, say so in one sentence and name the shared cause."),
    }[kind]
    persona = (cfg.ai_system_prompts or {}).get((cfg.ai_provider or "anthropic").lower(), "").strip() or "You are a senior technical SEO editor."
    return (persona.rstrip() + " " + task + brand + house +
            "\nRespond with ONLY a JSON array, one object per input id: [{\"id\": <id>, \"proposed\": \"...\", \"rationale\": \"one sentence\"}]. No prose outside the JSON.")


def _user_prompt(kind: str, issue: dict[str, Any], items: list[dict[str, Any]], candidates: list[str] | None = None) -> str:
    lines = [f"Issue being fixed: {issue.get('name')} — {issue.get('desc')} Fix guidance: {issue.get('fix')}", ""]
    for it in items:
        if kind == "alt_text":
            lines.append(json.dumps({"id": it["id"], "image": it["image"], "page_url": it["url"], "page_title": it["title"], "current_alt": it["current"], "page_excerpt": it["excerpt"][:300]}, ensure_ascii=False))
        elif kind == "redirect":
            lines.append(json.dumps({"id": it["id"], "broken_url": it["url"], "linked_from": it.get("linked_from", [])[:3]}, ensure_ascii=False))
        else:
            obj = {"id": it["id"], "url": it["url"], "current_title": it["title"], "current_meta_description": it["meta_description"],
                   "current_h1": it["h1"], "h2s": it["h2s"], "word_count": it["word_count"], "excerpt": it["excerpt"] if kind != "brief" else it["excerpt"][:400]}
            if it.get("evidence"):
                obj["evidence"] = it["evidence"]
            lines.append(json.dumps(obj, ensure_ascii=False, default=str))
    if candidates:
        lines += ["", "Candidate redirect targets (live 200 pages):"] + [f"- {c}" for c in candidates[:60]]
    return "\n".join(lines)


async def generate(con: sqlite3.Connection, cfg: CrawlConfig, key: str, url_ids: list[int] | None, job: FixJob) -> None:
    """Create fix proposals for every URL in a filter (or the given ids). Runs as a background task."""
    job.state, job.started, job.label = "running", time.time(), key
    try:
        issue = filters_by_key(cfg).get(key) or {"name": key, "desc": "", "fix": ""}
        names = dict(TABS)
        issue_name = f"{names.get(issue.get('tab'), '')}: {issue.get('name')}"
        kind = kind_for(key)
        provider = Provider(cfg) if not is_rule_kind(kind) else None
        if url_ids:
            ids = list(url_ids)
        else:
            ids = [r[0] for r in con.execute("SELECT url_id FROM url_issues WHERE key=? ORDER BY url_id", (key,))]
        # skip URLs that already have a live proposal for this kind
        if kind in fixkinds.RULE_KINDS or kind == "brief":
            have = {r[0] for r in con.execute("SELECT url_id FROM fixes WHERE issue_key=? AND status IN ('proposed','approved','pushed')", (key,))}
        else:
            have = {r[0] for r in con.execute("SELECT url_id FROM fixes WHERE kind=? AND status IN ('proposed','approved','pushed')", (kind,))}
        ids = [i for i in ids if i not in have]
        job.total = len(ids)
        if not ids:
            job.state = "done"
            return

        # one proposal for the whole site (security headers, HTTP -> HTTPS): not a per-page change
        if kind == "site":
            have_site = con.execute("SELECT 1 FROM fixes WHERE issue_key=? AND kind='site' AND status IN ('proposed','approved','pushed') LIMIT 1", (key,)).fetchone()
            job.total = 1
            if not have_site:
                site_url, field, current, proposed, rationale = fixkinds.site_fix(con, cfg, key, ids or url_ids or [])
                con.execute("INSERT INTO fixes(url_id, url, kind, field, issue_key, issue_name, current, proposed, rationale, status, provider, model, created_at, updated_at, extra) VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)",
                            ((ids or url_ids or [0])[0], site_url, "site", field, key, issue_name, current, proposed, rationale, "proposed", "rule", "", _now(), _now(), json.dumps({"urls": len(ids or url_ids or [])})))
                job.created += 1
            job.done = 1
            con.commit()
            job.state = "done"
            return

        # evidence-based page fixes: built from the stored HTML / headers / link graph, no model
        if kind in fixkinds.RULE_KINDS:
            evs = {uid: fixkinds.evidence(con, cfg, key, uid) for uid in ids}
            # markup that recurs on most affected pages is a template problem: one proposal for it, pages keep only their own items
            shared, _counts = fixkinds.split_shared(key, evs) if kind == "code" else ([], {})
            if shared:
                have_t = con.execute("SELECT 1 FROM fixes WHERE issue_key=? AND field='template' AND status IN ('proposed','approved','pushed') LIMIT 1", (key,)).fetchone()
                if not have_t:
                    site_url, field, current, proposed, rationale = fixkinds.template_fix(con, cfg, key, shared, len(ids))
                    con.execute("INSERT INTO fixes(url_id, url, kind, field, issue_key, issue_name, current, proposed, rationale, status, provider, model, created_at, updated_at, extra) VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)",
                                (ids[0], site_url, "site", field, key, issue_name, current, proposed, rationale, "proposed", "rule", "", _now(), _now(), json.dumps({"urls": len(ids), "shared": len(shared)})))
                    job.created += 1
            for uid in ids:
                if job._cancel:
                    break
                ev = evs[uid]
                if shared and not ev.get("items"):
                    job.done += 1          # fully covered by the template fix
                    continue
                fix = fixkinds.rule_fix(con, cfg, key, uid, ev)
                if fix is None:
                    job.errors.append(f"{ev.get('url', uid)}: no evidence found (HTML not stored?)")
                else:
                    field, current, proposed, rationale = fix
                    con.execute("INSERT INTO fixes(url_id, url, kind, field, issue_key, issue_name, current, proposed, rationale, status, provider, model, created_at, updated_at) VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?)",
                                (uid, ev.get("url", ""), kind, field, key, issue_name, current[:500], proposed, rationale[:500], "proposed", "rule", "", _now(), _now()))
                    job.created += 1
                job.done += 1
            con.commit()
            job.state = "cancelled" if job._cancel else "done"
            return

        # canonical fixes need no model: self-referencing canonical
        if kind == "canonical":
            for uid in ids:
                r = con.execute("SELECT url FROM urls WHERE id=?", (uid,)).fetchone()
                con.execute("INSERT INTO fixes(url_id, url, kind, field, issue_key, issue_name, current, proposed, rationale, status, provider, model, created_at, updated_at) VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?)",
                            (uid, r["url"], kind, "canonical", key, issue_name, "", r["url"], "Add a self-referencing canonical so duplicates resolve to this URL.", "proposed", "rule", "", _now(), _now()))
                job.done += 1
                job.created += 1
            con.commit()
            job.state = "done"
            return

        candidates = None
        if kind == "redirect":
            candidates = [r[0] for r in con.execute("SELECT url FROM urls WHERE crawled=1 AND is_internal=1 AND type='HTML' AND status_code=200 AND indexable=1 ORDER BY inlinks DESC LIMIT 400")]

        def build_items(batch: list[int]) -> list[dict[str, Any]]:
            out = []
            for uid in batch:
                ctx = _page_context(con, uid)
                if kind == "alt_text":
                    # the "URL" is an image; context comes from the first page that references it
                    ref = con.execute("SELECT l.from_id, l.alt FROM links l WHERE l.to_id=? AND l.kind='image' ORDER BY l.id LIMIT 1", (uid,)).fetchone()
                    page = _page_context(con, ref["from_id"]) if ref else {"url": "", "title": "", "excerpt": ""}
                    out.append({"id": uid, "image": ctx["url"], "url": page["url"], "title": page["title"], "excerpt": page["excerpt"], "current": ref["alt"] if ref else ""})
                elif kind == "redirect":
                    src = [r[0] for r in con.execute("SELECT f.url FROM links l JOIN urls f ON f.id=l.from_id WHERE l.to_id=? LIMIT 3", (uid,))]
                    out.append({**ctx, "linked_from": src, "current": ctx["url"]})
                else:
                    item = {**ctx, "current": ctx.get({"title": "title", "meta_description": "meta_description", "h1": "h1"}.get(kind, "title"), "")}
                    if kind == "brief":
                        item["evidence"] = {k: v for k, v in fixkinds.evidence(con, cfg, key, uid).items() if k != "url"}
                    out.append(item)
            return out

        sem = asyncio.Semaphore(max(1, cfg.ai_concurrency))
        size = max(1, min(20, cfg.ai_batch_size))
        batches = [ids[i:i + size] for i in range(0, len(ids), size)]
        system = _system_prompt(cfg, kind)

        async def run_batch(batch: list[int]):
            if job._cancel:
                return
            items = build_items(batch)
            async with sem:
                if job._cancel:
                    return
                try:
                    text = await provider.complete(system, _user_prompt(kind, issue, items, candidates))
                    data = parse_json_block(text)
                except AIError as exc:
                    job.errors.append(str(exc))
                    job.done += len(batch)
                    return
            by_id = {it["id"]: it for it in items}
            rows = data if isinstance(data, list) else data.get("fixes", []) if isinstance(data, dict) else []
            for obj in rows:
                try:
                    uid = int(obj.get("id"))
                except (TypeError, ValueError, AttributeError):
                    continue
                it = by_id.get(uid)
                proposed = str(obj.get("proposed", "")).strip()
                if not it or not proposed:
                    continue
                field = {"title": "title", "meta_description": "meta_description", "h1": "h1", "alt_text": "alt", "redirect": "redirect_to", "brief": "brief"}[kind]
                con.execute("INSERT INTO fixes(url_id, url, kind, field, issue_key, issue_name, current, proposed, rationale, status, provider, model, created_at, updated_at, extra) VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)",
                            (uid, it.get("image") or it["url"], kind, field, key, issue_name, it.get("current") or "", proposed, str(obj.get("rationale", ""))[:500],
                             "proposed", provider.provider, provider.model, _now(), _now(), json.dumps({"page_url": it.get("url")}) if kind == "alt_text" else None))
                job.created += 1
            job.done += len(batch)
            con.commit()

        try:
            await asyncio.gather(*(run_batch(b) for b in batches))
        finally:
            usage_log.record_ai(provider.provider, provider.model, kind, provider.requests, provider.input_tokens, provider.output_tokens,
                                crawl=usage_log.crawl_of(con), label=issue_name)
        con.commit()
        job.state = "cancelled" if job._cancel else "done"
    except AIError as exc:
        job.state = "error"
        job.errors.append(str(exc))
    except Exception as exc:  # noqa: BLE001
        job.state = "error"
        job.errors.append(f"{type(exc).__name__}: {exc}")


async def push(con: sqlite3.Connection, cfg: CrawlConfig, target_name: str, fix_ids: list[int] | None) -> dict[str, Any]:
    """Apply approved fixes through the named target. Returns counts and per-fix results."""
    target = next((t for t in cfg.targets if t.get("name") == target_name), None)
    if target is None:
        raise ValueError(f"No connector target named '{target_name}'. Add one in Config > Connectors.")
    conn = make_connector(target)
    where = "status='approved'" + (f" AND id IN ({','.join('?' * len(fix_ids))})" if fix_ids else "")
    rows = [dict(r) for r in con.execute(f"SELECT * FROM fixes WHERE {where} ORDER BY id", fix_ids or [])]
    results = []
    ok = failed = skipped = 0
    for f in rows:
        if f["kind"] not in conn.supports or f["kind"] == "brief":
            skipped += 1
            results.append({"id": f["id"], "ok": False, "message": f"'{f['kind']}' is not pushable through {conn.label}; export it as a brief."})
            continue
        res = await conn.apply(f)
        status = "pushed" if res.ok else "failed"
        con.execute("UPDATE fixes SET status=?, target=?, result=?, pushed_at=?, updated_at=? WHERE id=?",
                    (status, target_name, (res.message + (" · " + res.detail if res.detail else ""))[:1000], _now() if res.ok else None, _now(), f["id"]))
        ok += int(res.ok)
        failed += int(not res.ok)
        results.append({"id": f["id"], "ok": res.ok, "message": res.message})
    con.commit()
    return {"target": target_name, "env": target.get("env", ""), "pushed": ok, "failed": failed, "skipped": skipped, "results": results}
