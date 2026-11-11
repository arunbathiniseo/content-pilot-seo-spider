"""Google Search Console + GA4 (Screaming Frog "Search Console" and "Analytics" tabs).

Sign-in is the OAuth 2.0 desktop (loopback) flow with the person's own Google Cloud OAuth client: the app opens the
consent screen in the browser, Google redirects back to http://127.0.0.1:<port>/api/google/callback, the refresh
token is stored in settings.json. Scopes: Search Console read-only, Analytics read-only.

Data: Search Analytics per page (clicks, impressions, CTR, position) and GA4 per page path (sessions, engaged
sessions, engagement rate, bounce rate, views, key events) for the chosen date range, merged into the crawl's URLs;
URLs with traffic that the crawl never saw become "orphan" rows. Optional URL Inspection (index status, Google's
canonical, last crawl) for a capped number of pages (API quota: 2,000 per property per day).

`base_override` points every Google host at one URL — used by tests with a mock server.
"""
from __future__ import annotations

import json
import secrets
import sqlite3
import time
from datetime import date, timedelta
from typing import Any
from urllib.parse import quote, urlencode, urlsplit

import httpx

from . import db as dbm
from .config import CrawlConfig

SCOPES = ["https://www.googleapis.com/auth/webmasters.readonly", "https://www.googleapis.com/auth/analytics.readonly"]
HOSTS = {"auth": "https://accounts.google.com", "token": "https://oauth2.googleapis.com", "gsc": "https://www.googleapis.com",
         "inspect": "https://searchconsole.googleapis.com", "ga_admin": "https://analyticsadmin.googleapis.com", "ga_data": "https://analyticsdata.googleapis.com",
         "userinfo": "https://openidconnect.googleapis.com"}
ORPHAN = 5                     # urls.crawled value for URLs known only from Search Console / GA4
_pending_state: dict[str, float] = {}


class GoogleError(Exception):
    pass


def _host(cfg: CrawlConfig, key: str) -> str:
    return (cfg.google_base_override or HOSTS[key]).rstrip("/")


# ----------------------------------------------------------------------------- OAuth
def auth_url(cfg: CrawlConfig, redirect_uri: str) -> str:
    if not cfg.google_client_id.strip():
        raise GoogleError("Paste the OAuth client ID and secret first (Google Cloud Console → APIs & Services → Credentials → OAuth client, type Desktop app).")
    state = secrets.token_urlsafe(16)
    _pending_state[state] = time.time()
    q = {"client_id": cfg.google_client_id.strip(), "redirect_uri": redirect_uri, "response_type": "code", "scope": " ".join(SCOPES + ["openid", "email"]),
         "access_type": "offline", "prompt": "consent", "state": state}
    return f"{_host(cfg, 'auth')}/o/oauth2/v2/auth?{urlencode(q)}"


def valid_state(state: str) -> bool:
    ts = _pending_state.pop(state, None)
    return ts is not None and time.time() - ts < 900


async def exchange_code(cfg: CrawlConfig, code: str, redirect_uri: str) -> dict[str, Any]:
    async with httpx.AsyncClient(timeout=30) as c:
        r = await c.post(f"{_host(cfg, 'token')}/token", data={"code": code, "client_id": cfg.google_client_id.strip(), "client_secret": cfg.google_client_secret.strip(),
                                                                 "redirect_uri": redirect_uri, "grant_type": "authorization_code"})
    if r.status_code >= 400:
        raise GoogleError(f"Token exchange failed ({r.status_code}): {r.text[:200]}")
    tok = r.json()
    email = ""
    if tok.get("access_token"):
        try:
            async with httpx.AsyncClient(timeout=15) as c:
                u = await c.get(f"{_host(cfg, 'userinfo')}/v1/userinfo", headers={"Authorization": f"Bearer {tok['access_token']}"})
            email = u.json().get("email", "") if u.status_code < 400 else ""
        except httpx.HTTPError:
            pass
    return {"refresh_token": tok.get("refresh_token", ""), "access_token": tok.get("access_token", ""), "expires_in": tok.get("expires_in", 3600), "email": email}


async def access_token(cfg: CrawlConfig) -> str:
    if not cfg.google_refresh_token.strip():
        raise GoogleError("Not connected to Google. Use Config → Search Console & GA4 → Connect.")
    async with httpx.AsyncClient(timeout=30) as c:
        r = await c.post(f"{_host(cfg, 'token')}/token", data={"refresh_token": cfg.google_refresh_token.strip(), "client_id": cfg.google_client_id.strip(),
                                                                 "client_secret": cfg.google_client_secret.strip(), "grant_type": "refresh_token"})
    if r.status_code >= 400:
        raise GoogleError(f"Google refused the refresh token ({r.status_code}); reconnect in Config → Search Console & GA4. {r.text[:120]}")
    return r.json().get("access_token", "")


async def _get(cfg: CrawlConfig, token: str, url: str, **kw) -> Any:
    async with httpx.AsyncClient(timeout=60) as c:
        r = await c.get(url, headers={"Authorization": f"Bearer {token}"}, **kw)
    _raise(r, url)
    return r.json()


async def _post(cfg: CrawlConfig, token: str, url: str, body: dict[str, Any]) -> Any:
    async with httpx.AsyncClient(timeout=120) as c:
        r = await c.post(url, headers={"Authorization": f"Bearer {token}", "Content-Type": "application/json"}, json=body)
    _raise(r, url)
    return r.json()


def _raise(r: httpx.Response, url: str) -> None:
    if r.status_code == 403:
        raise GoogleError(f"Google says forbidden (403) for {urlsplit(url).path}: enable the API in your Cloud project and make sure the account owns the property. {r.text[:160]}")
    if r.status_code == 429:
        raise GoogleError("Google API quota exceeded (429). Try again later or lower the URL Inspection limit.")
    if r.status_code >= 400:
        raise GoogleError(f"Google API error {r.status_code} on {urlsplit(url).path}: {r.text[:200]}")


# ----------------------------------------------------------------------------- properties
async def gsc_sites(cfg: CrawlConfig, token: str) -> list[dict[str, str]]:
    d = await _get(cfg, token, f"{_host(cfg, 'gsc')}/webmasters/v3/sites")
    return [{"url": s.get("siteUrl", ""), "permission": s.get("permissionLevel", "")} for s in d.get("siteEntry", []) if s.get("permissionLevel") != "siteUnverifiedUser"]


async def ga4_properties(cfg: CrawlConfig, token: str) -> list[dict[str, str]]:
    out = []
    d = await _get(cfg, token, f"{_host(cfg, 'ga_admin')}/v1beta/accountSummaries", params={"pageSize": 200})
    for acc in d.get("accountSummaries", []):
        for p in acc.get("propertySummaries", []):
            out.append({"id": p.get("property", ""), "name": f"{p.get('displayName', '')} ({acc.get('displayName', '')})"})
    return out


def pick_gsc_site(sites: list[dict[str, str]], start_url: str) -> str:
    host = (urlsplit(start_url).hostname or "").lower().removeprefix("www.")
    for s in sites:
        if s["url"].startswith("sc-domain:") and s["url"][10:].lower() == host:
            return s["url"]
    for s in sites:
        if (urlsplit(s["url"]).hostname or "").lower().removeprefix("www.") == host:
            return s["url"]
    return ""


# ----------------------------------------------------------------------------- data
def date_range(days: int) -> tuple[str, str]:
    end = date.today() - timedelta(days=3)          # GSC data lags ~2-3 days
    start = end - timedelta(days=max(1, days) - 1)
    return start.isoformat(), end.isoformat()


async def gsc_search_analytics(cfg: CrawlConfig, token: str, site: str, start: str, end: str) -> dict[str, dict[str, float]]:
    """page -> {clicks, impressions, ctr, position}, every page with data in the range (paged by 25,000)."""
    out: dict[str, dict[str, float]] = {}
    row_start = 0
    while True:
        body = {"startDate": start, "endDate": end, "dimensions": ["page"], "rowLimit": 25000, "startRow": row_start, "dataState": "final"}
        d = await _post(cfg, token, f"{_host(cfg, 'gsc')}/webmasters/v3/sites/{quote(site, safe='')}/searchAnalytics/query", body)
        rows = d.get("rows", [])
        for r in rows:
            page = r["keys"][0]
            out[page] = {"clicks": r.get("clicks", 0), "impressions": r.get("impressions", 0), "ctr": round(r.get("ctr", 0) * 100, 2), "position": round(r.get("position", 0), 1)}
        if len(rows) < 25000:
            break
        row_start += 25000
    return out


async def gsc_inspect(cfg: CrawlConfig, token: str, site: str, url: str) -> dict[str, Any]:
    d = await _post(cfg, token, f"{_host(cfg, 'inspect')}/v1/urlInspection/index:inspect", {"inspectionUrl": url, "siteUrl": site})
    idx = (d.get("inspectionResult") or {}).get("indexStatusResult") or {}
    mob = (d.get("inspectionResult") or {}).get("mobileUsabilityResult") or {}
    return {"inspect_verdict": idx.get("verdict", ""), "inspect_coverage": idx.get("coverageState", ""), "inspect_indexing_state": idx.get("indexingState", ""),
            "inspect_last_crawl": idx.get("lastCrawlTime", ""), "inspect_robots": idx.get("robotsTxtState", ""), "inspect_fetch": idx.get("pageFetchState", ""),
            "inspect_google_canonical": idx.get("googleCanonical", ""), "inspect_user_canonical": idx.get("userCanonical", ""), "inspect_mobile": mob.get("verdict", ""),
            "inspect_at": time.strftime("%Y-%m-%dT%H:%M:%S")}


async def ga4_report(cfg: CrawlConfig, token: str, prop: str, start: str, end: str) -> dict[str, dict[str, float]]:
    """pagePath -> {sessions, engaged_sessions, engagement_rate, bounce_rate, views, key_events} (paged by 100,000)."""
    out: dict[str, dict[str, float]] = {}
    offset = 0
    while True:
        body = {"dateRanges": [{"startDate": start, "endDate": end}], "dimensions": [{"name": "pagePath"}],
                "metrics": [{"name": "sessions"}, {"name": "engagedSessions"}, {"name": "engagementRate"}, {"name": "bounceRate"}, {"name": "screenPageViews"}, {"name": "keyEvents"}],
                "limit": 100000, "offset": offset}
        d = await _post(cfg, token, f"{_host(cfg, 'ga_data')}/v1beta/{prop}:runReport", body)
        rows = d.get("rows", [])
        for r in rows:
            path = r["dimensionValues"][0]["value"]
            v = [float(x.get("value") or 0) for x in r["metricValues"]]
            out[path] = {"sessions": v[0], "engaged_sessions": v[1], "engagement_rate": round(v[2] * 100, 1), "bounce_rate": round(v[3] * 100, 1), "views": v[4], "key_events": v[5]}
        if len(rows) < 100000:
            break
        offset += 100000
    return out


# ----------------------------------------------------------------------------- merge into the crawl
def _path_key(url: str) -> str:
    p = urlsplit(url)
    return (p.path or "/") + (("?" + p.query) if p.query else "")


def apply_gsc(con: sqlite3.Connection, cfg: CrawlConfig, data: dict[str, dict[str, float]], site: str, start: str, end: str) -> dict[str, int]:
    con.execute("UPDATE urls SET gsc_clicks=NULL, gsc_impressions=NULL, gsc_ctr=NULL, gsc_position=NULL")
    con.execute(f"DELETE FROM urls WHERE crawled={ORPHAN} AND coalesce(ga_sessions,0)=0 AND url NOT IN (SELECT url FROM urls WHERE crawled<>{ORPHAN})")
    by_url = {r["url"]: r["id"] for r in con.execute("SELECT id, url FROM urls WHERE is_internal=1")}
    matched = orphans = 0
    stamp = time.strftime("%Y-%m-%dT%H:%M:%S")
    for page, m in data.items():
        uid = by_url.get(page) or by_url.get(page.rstrip("/")) or by_url.get(page + "/")
        if uid is None:
            cur = con.execute("INSERT OR IGNORE INTO urls(url, is_internal, type, crawled, depth, gsc_orphan) VALUES (?,?,?,?,?,1)", (page, 1, "HTML", ORPHAN, 0))
            uid = cur.lastrowid if cur.rowcount else con.execute("SELECT id FROM urls WHERE url=?", (page,)).fetchone()[0]
            orphans += 1
        else:
            matched += 1
        dbm.update_url(con, uid, {"gsc_clicks": m["clicks"], "gsc_impressions": m["impressions"], "gsc_ctr": m["ctr"], "gsc_position": m["position"]})
    dbm.set_meta(con, "gsc", {"site": site, "start": start, "end": end, "fetched_at": stamp, "pages": len(data), "matched": matched, "orphans": orphans})
    return {"pages": len(data), "matched": matched, "orphans": orphans}


def apply_ga4(con: sqlite3.Connection, cfg: CrawlConfig, data: dict[str, dict[str, float]], prop: str, start: str, end: str) -> dict[str, int]:
    con.execute("UPDATE urls SET ga_sessions=NULL, ga_engaged=NULL, ga_engagement_rate=NULL, ga_bounce_rate=NULL, ga_views=NULL, ga_key_events=NULL")
    rows = con.execute("SELECT id, url FROM urls WHERE is_internal=1").fetchall()
    by_path: dict[str, int] = {}
    for r in rows:
        by_path.setdefault(_path_key(r["url"]), r["id"])
        by_path.setdefault(_path_key(r["url"]).rstrip("/") or "/", r["id"])
    root = cfg.start_url or (rows[0]["url"] if rows else "")
    origin = f"{urlsplit(root).scheme}://{urlsplit(root).netloc}" if root else ""
    matched = orphans = 0
    stamp = time.strftime("%Y-%m-%dT%H:%M:%S")
    for path, m in data.items():
        uid = by_path.get(path) or by_path.get(path.rstrip("/") or "/")
        if uid is None:
            if not origin or not path.startswith("/"):
                continue
            url = origin + path
            cur = con.execute("INSERT OR IGNORE INTO urls(url, is_internal, type, crawled, depth, ga_orphan) VALUES (?,?,?,?,?,1)", (url, 1, "HTML", ORPHAN, 0))
            uid = cur.lastrowid if cur.rowcount else con.execute("SELECT id FROM urls WHERE url=?", (url,)).fetchone()[0]
            con.execute("UPDATE urls SET ga_orphan=1 WHERE id=?", (uid,))
            orphans += 1
        else:
            matched += 1
        dbm.update_url(con, uid, {"ga_sessions": m["sessions"], "ga_engaged": m["engaged_sessions"], "ga_engagement_rate": m["engagement_rate"], "ga_bounce_rate": m["bounce_rate"],
                                  "ga_views": m["views"], "ga_key_events": m["key_events"]})
    dbm.set_meta(con, "ga4", {"property": prop, "start": start, "end": end, "fetched_at": stamp, "paths": len(data), "matched": matched, "orphans": orphans})
    return {"paths": len(data), "matched": matched, "orphans": orphans}


def inspect_candidates(con: sqlite3.Connection, n: int) -> list[tuple[int, str]]:
    """Pages worth inspecting first: indexable 200 HTML by impressions, then inlinks."""
    return [(r[0], r[1]) for r in con.execute("SELECT id, url FROM urls WHERE crawled=1 AND is_internal=1 AND type='HTML' AND status_code=200 ORDER BY coalesce(gsc_impressions,0) DESC, inlinks DESC, depth LIMIT ?", (max(1, n),))]


class GoogleJob:
    def __init__(self) -> None:
        self.state = "idle"
        self.step = ""
        self.total = self.done = 0
        self.errors: list[str] = []
        self.result: dict[str, Any] = {}
        self.started = 0.0
        self._cancel = False

    def cancel(self) -> None:
        self._cancel = True

    def status(self) -> dict[str, Any]:
        return {"state": self.state, "step": self.step, "total": self.total, "done": self.done, "errors": self.errors[-5:], "result": self.result,
                "elapsed": round(time.time() - self.started, 1) if self.started else 0}


async def run_job(con: sqlite3.Connection, cfg: CrawlConfig, job: GoogleJob, do_gsc: bool = True, do_ga: bool = True, inspect_n: int = 0) -> None:
    job.state, job.started = "running", time.time()
    try:
        token = await access_token(cfg)
        start, end = date_range(cfg.gsc_days)
        if do_gsc:
            site = cfg.gsc_property.strip()
            if not site:
                sites = await gsc_sites(cfg, token)
                site = pick_gsc_site(sites, cfg.start_url)
                if not site:
                    raise GoogleError("No Search Console property matches this site; pick one in Config → Search Console & GA4.")
            job.step = f"Search Console {start} → {end}"
            data = await gsc_search_analytics(cfg, token, site, start, end)
            job.result["gsc"] = apply_gsc(con, cfg, data, site, start, end)
            con.commit()
        if do_ga and cfg.ga4_property.strip():
            job.step = f"GA4 {start} → {end}"
            data = await ga4_report(cfg, token, cfg.ga4_property.strip(), start, end)
            job.result["ga4"] = apply_ga4(con, cfg, data, cfg.ga4_property.strip(), start, end)
            con.commit()
        elif do_ga:
            job.errors.append("GA4: no property selected (Config → Search Console & GA4)")
        if inspect_n > 0:
            site = cfg.gsc_property.strip() or (dbm.get_meta(con, "gsc", {}) or {}).get("site", "")
            if not site:
                raise GoogleError("URL Inspection needs a Search Console property.")
            cands = inspect_candidates(con, inspect_n)
            job.total, job.done, job.step = len(cands), 0, "URL Inspection"
            ok = 0
            for uid, url in cands:
                if job._cancel:
                    break
                try:
                    dbm.update_url(con, uid, await gsc_inspect(cfg, token, site, url))
                    ok += 1
                except GoogleError as exc:
                    job.errors.append(str(exc))
                    if "quota" in str(exc).lower():
                        break
                job.done += 1
                if job.done % 10 == 0:
                    con.commit()
            con.commit()
            job.result["inspect"] = {"requested": len(cands), "inspected": ok}
        from .issues import rebuild_issue_table
        rebuild_issue_table(con, cfg)
        con.commit()
        job.state = "cancelled" if job._cancel else "done"
    except GoogleError as exc:
        job.state = "error"
        job.errors.append(str(exc))
    except Exception as exc:  # noqa: BLE001
        job.state = "error"
        job.errors.append(f"{type(exc).__name__}: {exc}")
