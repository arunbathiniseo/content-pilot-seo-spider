"""Usage statistics: crawls run, URLs fetched, AI requests / tokens / estimated cost per provider and model.
Stored as `usage.json` next to the app (per-run records, so the page can show cumulative or per-run figures)."""
from __future__ import annotations

import json
import sqlite3
import threading
from datetime import datetime
from pathlib import Path
from typing import Any

ROOT = Path(__file__).resolve().parents[1]
USAGE_PATH = ROOT / "usage.json"
_LOCK = threading.Lock()

# USD per 1M tokens [input, output]. Anthropic rates from the official price list (cached 2026-10-06); other providers
# are left for the person to fill in on the Usage page (prices change too often to hard-code reliably).
DEFAULT_PRICES: dict[str, list[float]] = {
    "anthropic:claude-fable-5-1": [10.0, 50.0], "anthropic:claude-fable-5": [10.0, 50.0],
    "anthropic:claude-opus-5-5": [4.0, 20.0], "anthropic:claude-opus-5": [5.0, 25.0],
    "anthropic:claude-opus-4-8": [5.0, 25.0], "anthropic:claude-opus-4-7": [5.0, 25.0], "anthropic:claude-opus-4-6": [5.0, 25.0],
    "anthropic:claude-sonnet-5-5": [2.0, 10.0], "anthropic:claude-sonnet-5": [2.0, 10.0], "anthropic:claude-sonnet-4-6": [3.0, 15.0],
    "anthropic:claude-haiku-5-5": [0.10, 0.50], "anthropic:claude-haiku-4-5": [1.0, 5.0],
    "ollama:*": [0.0, 0.0],
}


def _now() -> str:
    return datetime.now().isoformat(timespec="seconds")


def _load() -> dict[str, Any]:
    try:
        d = json.loads(USAGE_PATH.read_text(encoding="utf-8"))
        if isinstance(d, dict):
            d.setdefault("crawls", []); d.setdefault("ai", []); d.setdefault("prices", {})
            return d
    except (OSError, ValueError):
        pass
    return {"crawls": [], "ai": [], "prices": {}}


def _save(d: dict[str, Any]) -> None:
    tmp = USAGE_PATH.with_suffix(".json.tmp")
    tmp.write_text(json.dumps(d, indent=1), encoding="utf-8")
    tmp.replace(USAGE_PATH)


def crawl_of(con: sqlite3.Connection) -> str:
    """File name of the crawl a connection is open on (so AI usage can be tied to a run)."""
    for row in con.execute("PRAGMA database_list"):
        if row[1] == "main" and row[2]:
            return Path(row[2]).name
    return ""


def record_crawl(crawl: str, start_url: str, mode: str, state: str, urls: int, pages: int, elapsed: float, started_at: str = "", rendering: str = "text") -> None:
    with _LOCK:
        d = _load()
        d["crawls"] = [c for c in d["crawls"] if c.get("crawl") != crawl]       # a stopped-then-finished run counts once
        d["crawls"].append({"crawl": crawl, "start_url": start_url, "mode": mode, "state": state, "urls": int(urls or 0), "pages": int(pages or 0),
                            "elapsed": round(float(elapsed or 0), 1), "started_at": started_at or _now(), "recorded_at": _now(), "rendering": rendering})
        _save(d)


def record_ai(provider: str, model: str, kind: str, requests: int, input_tokens: int, output_tokens: int, crawl: str = "", label: str = "") -> None:
    if not requests:
        return
    with _LOCK:
        d = _load()
        d["ai"].append({"at": _now(), "provider": provider, "model": model, "kind": kind, "requests": int(requests), "input_tokens": int(input_tokens or 0),
                        "output_tokens": int(output_tokens or 0), "crawl": crawl, "label": label})
        _save(d)


def prices() -> dict[str, list[float]]:
    return {**DEFAULT_PRICES, **_load()["prices"]}


def set_prices(new: dict[str, Any]) -> dict[str, list[float]]:
    """Store per-million prices as {"provider:model": [in, out]}; an empty / null entry removes the override."""
    with _LOCK:
        d = _load()
        for k, v in new.items():
            k = str(k).strip().lower()
            if not v or (isinstance(v, (list, tuple)) and not any(x not in (None, "") for x in v)):
                d["prices"].pop(k, None)
                continue
            pin, pout = (list(v) + [0, 0])[:2]
            d["prices"][k] = [max(0.0, float(pin or 0)), max(0.0, float(pout or 0))]
        _save(d)
    return prices()


def cost_usd(provider: str, model: str, input_tokens: int, output_tokens: int, table: dict[str, list[float]] | None = None) -> float | None:
    table = table or prices()
    p = table.get(f"{provider}:{model}".lower()) or table.get(f"{provider}:*".lower())
    if p is None:
        # dated / suffixed Anthropic ids such as claude-opus-5-5-20260401 share the base price
        for k, v in table.items():
            prov, _, m = k.partition(":")
            if prov == provider.lower() and m != "*" and model.lower().startswith(m):
                p = v
                break
    if p is None:
        return None
    return round(input_tokens / 1e6 * p[0] + output_tokens / 1e6 * p[1], 4)


def summary() -> dict[str, Any]:
    """Everything the Usage page needs: totals, per provider/model, per run (crawl + its AI usage), raw AI rows."""
    d = _load()
    table = prices()
    crawls = sorted(d["crawls"], key=lambda c: c.get("started_at", ""), reverse=True)
    ai = d["ai"]
    by_model: dict[str, dict[str, Any]] = {}
    for r in ai:
        k = f"{r['provider']}:{r['model']}"
        m = by_model.setdefault(k, {"provider": r["provider"], "model": r["model"], "requests": 0, "input_tokens": 0, "output_tokens": 0, "cost_usd": 0.0, "priced": True})
        m["requests"] += r["requests"]; m["input_tokens"] += r["input_tokens"]; m["output_tokens"] += r["output_tokens"]
        c = cost_usd(r["provider"], r["model"], r["input_tokens"], r["output_tokens"], table)
        if c is None:
            m["priced"] = False
        else:
            m["cost_usd"] = round(m["cost_usd"] + c, 4)
    for m in by_model.values():
        p = table.get(f"{m['provider']}:{m['model']}".lower()) or table.get(f"{m['provider']}:*")
        m["price_in"], m["price_out"] = (p or [None, None])[:2]
    ai_by_crawl: dict[str, dict[str, Any]] = {}
    for r in ai:
        a = ai_by_crawl.setdefault(r.get("crawl", ""), {"requests": 0, "input_tokens": 0, "output_tokens": 0, "cost_usd": 0.0, "unpriced": False})
        a["requests"] += r["requests"]; a["input_tokens"] += r["input_tokens"]; a["output_tokens"] += r["output_tokens"]
        c = cost_usd(r["provider"], r["model"], r["input_tokens"], r["output_tokens"], table)
        if c is None:
            a["unpriced"] = True
        else:
            a["cost_usd"] = round(a["cost_usd"] + c, 4)
    runs = [{**c, "ai": ai_by_crawl.get(c["crawl"], {"requests": 0, "input_tokens": 0, "output_tokens": 0, "cost_usd": 0.0, "unpriced": False})} for c in crawls]
    orphan = {k: v for k, v in ai_by_crawl.items() if k not in {c["crawl"] for c in crawls}}
    totals = {
        "crawls": len(crawls), "urls": sum(c["urls"] for c in crawls), "pages": sum(c["pages"] for c in crawls),
        "crawl_seconds": round(sum(c["elapsed"] for c in crawls), 1), "js_crawls": sum(1 for c in crawls if c.get("rendering") == "javascript"),
        "ai_requests": sum(r["requests"] for r in ai), "input_tokens": sum(r["input_tokens"] for r in ai), "output_tokens": sum(r["output_tokens"] for r in ai),
        "cost_usd": round(sum(m["cost_usd"] for m in by_model.values()), 4), "unpriced_models": sorted(k for k, m in by_model.items() if not m["priced"]),
        "first_at": min([c["started_at"] for c in crawls] + [r["at"] for r in ai], default=""),
    }
    return {"totals": totals, "by_model": sorted(by_model.values(), key=lambda m: -m["cost_usd"]), "runs": runs, "other_ai": orphan,
            "prices": table, "default_prices": DEFAULT_PRICES, "path": str(USAGE_PATH)}
