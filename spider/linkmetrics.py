"""Link metrics (Screaming Frog "Link Metrics" tab): per-URL authority and backlink figures from Ahrefs, Moz and
Majestic, each with the person's own API key. Only the providers with a key are queried; URLs are capped per run
because every call costs credits. `base_override` points all three hosts at one URL for tests."""
from __future__ import annotations

import asyncio
import base64
import sqlite3
import time
from datetime import date
from typing import Any
from urllib.parse import urlsplit

import httpx

from . import db as dbm
from .config import CrawlConfig

HOSTS = {"ahrefs": "https://api.ahrefs.com", "moz": "https://lsapi.seomoz.com", "majestic": "https://api.majestic.com"}
COLUMNS = {
    "ahrefs": ["lm_ahrefs_ur", "lm_ahrefs_dr", "lm_ahrefs_backlinks", "lm_ahrefs_refdomains", "lm_ahrefs_keywords", "lm_ahrefs_traffic"],
    "moz": ["lm_moz_pa", "lm_moz_da", "lm_moz_spam", "lm_moz_linking_domains", "lm_moz_backlinks"],
    "majestic": ["lm_majestic_tf", "lm_majestic_cf", "lm_majestic_backlinks", "lm_majestic_refdomains"],
}
ALL_COLUMNS = [c for cols in COLUMNS.values() for c in cols] + ["lm_at", "lm_providers"]


class LinkMetricsError(Exception):
    pass


def _host(cfg: CrawlConfig, key: str) -> str:
    return (cfg.linkmetrics_base_override or HOSTS[key]).rstrip("/")


def providers(cfg: CrawlConfig) -> list[str]:
    out = []
    if cfg.ahrefs_token.strip():
        out.append("ahrefs")
    if cfg.moz_access_id.strip() and cfg.moz_secret.strip():
        out.append("moz")
    if cfg.majestic_key.strip():
        out.append("majestic")
    return out


def _raise(r: httpx.Response, name: str) -> None:
    if r.status_code in (401, 403):
        raise LinkMetricsError(f"{name} rejected the API credentials ({r.status_code}).")
    if r.status_code == 429:
        raise LinkMetricsError(f"{name} rate limit / credits exhausted (429).")
    if r.status_code >= 400:
        raise LinkMetricsError(f"{name} API error {r.status_code}: {r.text[:200]}")


# ----------------------------------------------------------------------------- providers
async def ahrefs(cfg: CrawlConfig, client: httpx.AsyncClient, url: str) -> dict[str, Any]:
    h = {"Authorization": f"Bearer {cfg.ahrefs_token.strip()}", "Accept": "application/json"}
    base = _host(cfg, "ahrefs")
    today = date.today().isoformat()
    out: dict[str, Any] = {}
    r = await client.get(f"{base}/v3/site-explorer/backlinks-stats", params={"target": url, "mode": "exact", "date": today}, headers=h)
    _raise(r, "Ahrefs")
    m = (r.json().get("metrics") or {})
    out["lm_ahrefs_backlinks"], out["lm_ahrefs_refdomains"] = m.get("live"), m.get("live_refdomains")
    r = await client.get(f"{base}/v3/site-explorer/url-rating", params={"target": url, "date": today}, headers=h)
    if r.status_code < 400:
        out["lm_ahrefs_ur"] = (r.json().get("url_rating") or {}).get("url_rating") if isinstance(r.json().get("url_rating"), dict) else r.json().get("url_rating")
    r = await client.get(f"{base}/v3/site-explorer/domain-rating", params={"target": urlsplit(url).netloc, "date": today}, headers=h)
    if r.status_code < 400:
        dr = r.json().get("domain_rating")
        out["lm_ahrefs_dr"] = dr.get("domain_rating") if isinstance(dr, dict) else dr
    r = await client.get(f"{base}/v3/site-explorer/metrics", params={"target": url, "mode": "exact", "date": today, "country": cfg.ahrefs_country or "gb"}, headers=h)
    if r.status_code < 400:
        m = r.json().get("metrics") or {}
        out["lm_ahrefs_keywords"], out["lm_ahrefs_traffic"] = m.get("org_keywords"), m.get("org_traffic")
    return out


async def moz_batch(cfg: CrawlConfig, client: httpx.AsyncClient, urls: list[str]) -> dict[str, dict[str, Any]]:
    """Moz Links API v2: up to 50 targets per call."""
    token = base64.b64encode(f"{cfg.moz_access_id.strip()}:{cfg.moz_secret.strip()}".encode()).decode()
    r = await client.post(f"{_host(cfg, 'moz')}/v2/url_metrics", json={"targets": urls}, headers={"Authorization": f"Basic {token}", "Content-Type": "application/json"})
    _raise(r, "Moz")
    out = {}
    for u, res in zip(urls, r.json().get("results", [])):
        out[u] = {"lm_moz_pa": res.get("page_authority"), "lm_moz_da": res.get("domain_authority"), "lm_moz_spam": res.get("spam_score"),
                  "lm_moz_linking_domains": res.get("root_domains_to_page"), "lm_moz_backlinks": res.get("external_pages_to_page")}
    return out


async def majestic_batch(cfg: CrawlConfig, client: httpx.AsyncClient, urls: list[str]) -> dict[str, dict[str, Any]]:
    """Majestic GetIndexItemInfo: up to 100 items per call."""
    params: dict[str, Any] = {"app_api_key": cfg.majestic_key.strip(), "cmd": "GetIndexItemInfo", "items": len(urls), "datasource": cfg.majestic_datasource or "fresh"}
    for i, u in enumerate(urls):
        params[f"item{i}"] = u
    r = await client.get(f"{_host(cfg, 'majestic')}/api/json", params=params)
    _raise(r, "Majestic")
    data = r.json()
    if data.get("Code") not in (None, "OK"):
        raise LinkMetricsError(f"Majestic: {data.get('ErrorMessage') or data.get('Code')}")
    rows = ((data.get("DataTables") or {}).get("Results") or {}).get("Data") or []
    out = {}
    for u, res in zip(urls, rows):
        out[u] = {"lm_majestic_tf": res.get("TrustFlow"), "lm_majestic_cf": res.get("CitationFlow"), "lm_majestic_backlinks": res.get("ExtBackLinks"), "lm_majestic_refdomains": res.get("RefDomains")}
    return out


# ----------------------------------------------------------------------------- job
def candidates(con: sqlite3.Connection, n: int) -> list[tuple[int, str]]:
    return [(r[0], r[1]) for r in con.execute("SELECT id, url FROM urls WHERE crawled=1 AND is_internal=1 AND type='HTML' AND status_code=200 ORDER BY inlinks DESC, depth, id LIMIT ?", (max(1, n),))]


class LinkMetricsJob:
    def __init__(self) -> None:
        self.state, self.total, self.done = "idle", 0, 0
        self.errors: list[str] = []
        self.providers: list[str] = []
        self.started = 0.0
        self._cancel = False

    def cancel(self) -> None:
        self._cancel = True

    def status(self) -> dict[str, Any]:
        return {"state": self.state, "total": self.total, "done": self.done, "errors": self.errors[-5:], "error_count": len(self.errors), "providers": self.providers,
                "elapsed": round(time.time() - self.started, 1) if self.started else 0}


async def run_job(con: sqlite3.Connection, cfg: CrawlConfig, ids: list[int], job: LinkMetricsJob) -> None:
    job.state, job.started, job.total = "running", time.time(), len(ids)
    job.providers = providers(cfg)
    if not job.providers:
        job.state, job.errors = "error", ["No link-metrics API keys configured (Config → Link metrics)."]
        return
    rows = {r["id"]: r["url"] for r in con.execute(f"SELECT id, url FROM urls WHERE id IN ({','.join('?' * len(ids))})", ids)} if ids else {}
    results: dict[int, dict[str, Any]] = {uid: {} for uid in rows}
    stamp = time.strftime("%Y-%m-%dT%H:%M:%S")
    try:
        async with httpx.AsyncClient(timeout=60) as client:
            if "moz" in job.providers:
                items = list(rows.items())
                for i in range(0, len(items), 50):
                    if job._cancel:
                        break
                    chunk = items[i:i + 50]
                    try:
                        got = await moz_batch(cfg, client, [u for _, u in chunk])
                        for uid, u in chunk:
                            results[uid].update(got.get(u, {}))
                    except LinkMetricsError as exc:
                        job.errors.append(str(exc))
                        break
            if "majestic" in job.providers:
                items = list(rows.items())
                for i in range(0, len(items), 100):
                    if job._cancel:
                        break
                    chunk = items[i:i + 100]
                    try:
                        got = await majestic_batch(cfg, client, [u for _, u in chunk])
                        for uid, u in chunk:
                            results[uid].update(got.get(u, {}))
                    except LinkMetricsError as exc:
                        job.errors.append(str(exc))
                        break
            if "ahrefs" in job.providers:
                sem = asyncio.Semaphore(4)

                async def one(uid: int, url: str) -> None:
                    if job._cancel:
                        return
                    async with sem:
                        try:
                            results[uid].update(await ahrefs(cfg, client, url))
                        except LinkMetricsError as exc:
                            job.errors.append(str(exc))
                        except httpx.HTTPError as exc:
                            job.errors.append(f"Ahrefs unreachable: {exc}")
                await asyncio.gather(*(one(uid, u) for uid, u in rows.items()))
        for uid, vals in results.items():
            if vals:
                dbm.update_url(con, uid, {**vals, "lm_at": stamp, "lm_providers": ",".join(p for p in job.providers if any(k.startswith(f"lm_{p}_") for k in vals))})
            job.done += 1
        con.commit()
        from .issues import rebuild_issue_table
        rebuild_issue_table(con, cfg)
        con.commit()
        job.state = "cancelled" if job._cancel else "done"
    except Exception as exc:  # noqa: BLE001
        job.state = "error"
        job.errors.append(f"{type(exc).__name__}: {exc}")
