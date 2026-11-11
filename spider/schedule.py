"""Scheduled crawls (Screaming Frog "Scheduling"): a schedule names a site, how often to crawl it and what to do when
the crawl finishes (export, compare, Lighthouse, Google data; email comes from Config → Email). Schedules live in
`schedules.json`. The scheduler loop runs inside the app server; `cli.py schedule run-due` runs due schedules from a
Windows Task Scheduler job when the app is not open. Everything goes through the HTTP API so a scheduled crawl
behaves exactly like one started from the UI."""
from __future__ import annotations

import asyncio
import json
import threading
import time
import uuid
from datetime import datetime, timedelta
from pathlib import Path
from typing import Any, Callable

import httpx

ROOT = Path(__file__).resolve().parents[1]
SCHEDULES_PATH = ROOT / "schedules.json"
EXPORT_DIR = ROOT / "exports"
_LOCK = threading.Lock()
_RUNNING: set[str] = set()          # schedule ids currently executing (in this process)
DAYS = ["mon", "tue", "wed", "thu", "fri", "sat", "sun"]


def _load() -> dict[str, Any]:
    try:
        d = json.loads(SCHEDULES_PATH.read_text(encoding="utf-8"))
        if isinstance(d, dict):
            d.setdefault("schedules", [])
            return d
    except (OSError, ValueError):
        pass
    return {"schedules": []}


def _save(d: dict[str, Any]) -> None:
    tmp = SCHEDULES_PATH.with_suffix(".json.tmp")
    tmp.write_text(json.dumps(d, indent=1), encoding="utf-8")
    tmp.replace(SCHEDULES_PATH)


def list_schedules() -> list[dict[str, Any]]:
    out = _load()["schedules"]
    now = datetime.now()
    for s in out:
        s["next_run"] = next_run(s, now).isoformat(timespec="minutes") if s.get("enabled", True) else None
    return out


def get(sid: str) -> dict[str, Any] | None:
    return next((s for s in _load()["schedules"] if s["id"] == sid), None)


def upsert(s: dict[str, Any]) -> dict[str, Any]:
    with _LOCK:
        d = _load()
        s = normalise(s)
        for i, cur in enumerate(d["schedules"]):
            if cur["id"] == s["id"]:
                s["history"] = cur.get("history", [])
                s["last_run"] = cur.get("last_run")
                d["schedules"][i] = s
                break
        else:
            d["schedules"].append(s)
        _save(d)
    return s


def remove(sid: str) -> bool:
    with _LOCK:
        d = _load()
        n = len(d["schedules"])
        d["schedules"] = [s for s in d["schedules"] if s["id"] != sid]
        _save(d)
        return len(d["schedules"]) < n


def normalise(s: dict[str, Any]) -> dict[str, Any]:
    url = (s.get("url") or "").strip()
    if not url:
        raise ValueError("A schedule needs a URL to crawl")
    if not url.startswith(("http://", "https://")):
        url = "https://" + url
    freq = s.get("frequency", "daily")
    if freq not in ("hourly", "daily", "weekly", "once"):
        raise ValueError("frequency must be hourly, daily, weekly or once")
    t = (s.get("time") or "03:00").strip()
    try:
        hh, mm = (int(x) for x in t.split(":"))
        assert 0 <= hh < 24 and 0 <= mm < 60
    except (ValueError, AssertionError):
        raise ValueError("time must be HH:MM")
    day = (s.get("day") or "mon").lower()[:3]
    if freq == "weekly" and day not in DAYS:
        raise ValueError("day must be mon..sun")
    every = int(s.get("every_hours") or 6)
    if freq == "hourly" and not (1 <= every <= 168):
        raise ValueError("every_hours must be 1-168")
    out = {
        "id": s.get("id") or uuid.uuid4().hex[:10], "name": (s.get("name") or url).strip()[:80], "url": url, "mode": s.get("mode") if s.get("mode") in ("spider", "sitemap", "list") else "spider",
        "list_urls": [u.strip() for u in (s.get("list_urls") or []) if u.strip()],
        "frequency": freq, "time": f"{hh:02d}:{mm:02d}", "day": day, "every_hours": every, "once_at": s.get("once_at") or "",
        "enabled": bool(s.get("enabled", True)),
        "config": {k: v for k, v in (s.get("config") or {}).items() if isinstance(k, str)},          # CrawlConfig overrides for this run (max_urls, rendering…)
        "export": s.get("export") if s.get("export") in ("none", "issues", "everything") else "issues",
        "export_format": s.get("export_format") if s.get("export_format") in ("xlsx", "zip") else "xlsx",
        "out_dir": (s.get("out_dir") or "").strip(),
        "compare": bool(s.get("compare", True)), "lighthouse": int(s.get("lighthouse") or 0), "google": bool(s.get("google", False)),
        "created": s.get("created") or datetime.now().isoformat(timespec="seconds"),
    }
    return out


def next_run(s: dict[str, Any], now: datetime | None = None) -> datetime:
    now = now or datetime.now()
    hh, mm = (int(x) for x in s.get("time", "03:00").split(":"))
    freq = s.get("frequency", "daily")
    if freq == "once":
        try:
            return datetime.fromisoformat(s.get("once_at") or "")
        except ValueError:
            return now.replace(hour=hh, minute=mm, second=0, microsecond=0)
    if freq == "hourly":
        last = s.get("last_run")
        base = datetime.fromisoformat(last) if last else now - timedelta(hours=int(s.get("every_hours") or 6))
        nxt = base + timedelta(hours=int(s.get("every_hours") or 6))
        return nxt if nxt > now else now
    cand = now.replace(hour=hh, minute=mm, second=0, microsecond=0)
    if freq == "daily":
        return cand if cand > now else cand + timedelta(days=1)
    want = DAYS.index((s.get("day") or "mon")[:3])
    delta = (want - cand.weekday()) % 7
    cand = cand + timedelta(days=delta)
    return cand if cand > now else cand + timedelta(days=7)


def due(now: datetime | None = None) -> list[dict[str, Any]]:
    now = now or datetime.now()
    out = []
    for s in _load()["schedules"]:
        if not s.get("enabled", True) or s["id"] in _RUNNING:
            continue
        last = s.get("last_run")
        if s["frequency"] == "once" and last:
            continue
        nr = next_run({**s, "last_run": None} if s["frequency"] != "hourly" else s, now)
        # a daily/weekly schedule is due when its latest slot has passed, it has not run since that slot, and it
        # existed before the slot (a schedule created at 10:00 for 03:00 waits for tomorrow, it does not fire at once)
        if s["frequency"] in ("daily", "weekly"):
            slot = nr - timedelta(days=1 if s["frequency"] == "daily" else 7)
            try:
                created = datetime.fromisoformat(s.get("created") or "")
            except ValueError:
                created = datetime.min
            if slot <= now and created <= slot and (not last or datetime.fromisoformat(last) < slot):
                out.append(s)
        elif nr <= now:
            out.append(s)
    return out


def record(sid: str, entry: dict[str, Any]) -> None:
    with _LOCK:
        d = _load()
        for s in d["schedules"]:
            if s["id"] == sid:
                s["last_run"] = entry.get("started") or datetime.now().isoformat(timespec="seconds")
                s["history"] = ([entry] + s.get("history", []))[:20]
                if s["frequency"] == "once":
                    s["enabled"] = False
        _save(d)


# ----------------------------------------------------------------------------- running one schedule through the API
async def run_schedule(s: dict[str, Any], client: httpx.AsyncClient, log: Callable[[str], None] = lambda m: None, poll: float = 2.0, timeout: float = 6 * 3600) -> dict[str, Any]:
    """Start the crawl, wait, then export / compare / Lighthouse / Google as the schedule asks. Returns the history entry."""
    if s["id"] in _RUNNING:
        return {"ok": False, "note": "already running", "schedule": s["name"]}
    _RUNNING.add(s["id"])
    try:
        return await _run_schedule(s, client, log, poll, timeout)
    finally:
        _RUNNING.discard(s["id"])


async def _run_schedule(s: dict[str, Any], client: httpx.AsyncClient, log: Callable[[str], None], poll: float, timeout: float) -> dict[str, Any]:
    started = datetime.now().isoformat(timespec="seconds")
    entry: dict[str, Any] = {"started": started, "schedule": s["name"], "url": s["url"]}
    t0 = time.time()

    async def api(method: str, path: str, **kw) -> Any:
        r = await client.request(method, path, **kw)
        if r.status_code >= 400:
            try:
                detail = r.json().get("detail")
            except ValueError:
                detail = r.text[:200]
            raise RuntimeError(f"{path}: {detail}")
        return r

    try:
        st = (await api("GET", "/api/crawl/status")).json()
        if st.get("state") in ("running", "paused", "stopping"):
            raise RuntimeError("a crawl is already running")
        body = {"start_url": s["url"], "mode": s["mode"], "list_urls": s.get("list_urls", []), "config": s.get("config", {})}
        r = (await api("POST", "/api/crawl/start", json=body)).json()
        entry["crawl_file"] = Path(r.get("db") or "").name
        log(f"[{s['name']}] crawl started: {s['url']}")
        while True:
            st = (await api("GET", "/api/crawl/status")).json()
            if st.get("state") not in ("running", "stopping"):
                break
            if time.time() - t0 > timeout:
                await api("POST", "/api/crawl/stop")
                entry["note"] = "stopped: timeout"
            await asyncio.sleep(poll)
        entry.update({"state": st.get("state"), "urls": st.get("crawled", 0), "elapsed": round(st.get("elapsed", 0))})
        log(f"[{s['name']}] {st.get('state')}: {st.get('crawled', 0)} URLs in {round(st.get('elapsed', 0))}s")
        issues = (await api("GET", "/api/issues")).json().get("issues", [])
        entry["issues"] = len(issues)
        entry["high_issues"] = sum(1 for i in issues if i.get("priority") == "High" and i.get("kind") == "Issue")
        if s.get("export", "issues") != "none":
            out_dir = Path(s.get("out_dir") or EXPORT_DIR / "scheduled")
            out_dir.mkdir(parents=True, exist_ok=True)
            parts = "overview,issues,issue_urls,issue_inlinks,matrix" if s["export"] == "issues" else "overview,issues,issue_urls,issue_inlinks,matrix,tabs,filters,links,reports,fixes"
            r = await api("GET", "/api/export/bundle", params={"parts": parts, "format": s.get("export_format", "xlsx")})
            stem = f"{s['name'].replace(' ', '_')}_{datetime.now().strftime('%Y%m%d_%H%M')}.{s.get('export_format', 'xlsx')}"
            path = out_dir / "".join(ch for ch in stem if ch not in '<>:"/\\|?*')
            path.write_bytes(r.content)
            entry["export"] = str(path)
            log(f"[{s['name']}] exported {path}")
        if s.get("compare", True):
            try:
                c = (await api("GET", "/api/compare", params={"limit": 1})).json()
                sm = c["summary"]
                entry["compare"] = {"against": c["a"].get("started_at", ""), "issues_up": sm["issues_up"], "issues_down": sm["issues_down"], "new": sm["issues_new"], "fixed": sm["issues_fixed"],
                                    "added": sm["added"], "removed": sm["removed"], "changed": sm["changed"]}
                log(f"[{s['name']}] vs previous: {sm['issues_up']} up, {sm['issues_down']} down, {sm['added']} added, {sm['removed']} removed, {sm['changed']} changed")
            except RuntimeError as exc:
                entry["compare"] = {"note": str(exc)[:120]}
        if int(s.get("lighthouse") or 0) > 0:
            try:
                await api("POST", "/api/lighthouse/run", json={"sample": int(s["lighthouse"])})
                while (await api("GET", "/api/lighthouse/status")).json().get("state") == "running":
                    await asyncio.sleep(3)
                lh = (await api("GET", "/api/lighthouse/status")).json()
                entry["lighthouse"] = {"ok": lh.get("ok"), "errors": lh.get("error_count")}
                log(f"[{s['name']}] Lighthouse: {lh.get('ok')} pages")
            except RuntimeError as exc:
                entry["lighthouse"] = {"note": str(exc)[:120]}
        if s.get("google"):
            try:
                await api("POST", "/api/google/run", json={"gsc": True, "ga4": True, "inspect": -1})
                while (await api("GET", "/api/google/status")).json().get("job", {}).get("state") == "running":
                    await asyncio.sleep(2)
                g = (await api("GET", "/api/google/status")).json().get("job", {})
                entry["google"] = g.get("result") or {"note": "; ".join(g.get("errors", []))[:120]}
            except RuntimeError as exc:
                entry["google"] = {"note": str(exc)[:120]}
        entry["ok"] = st.get("state") in ("finished", "stopped")
    except Exception as exc:  # noqa: BLE001
        entry["ok"] = False
        entry["error"] = f"{type(exc).__name__}: {exc}"[:300]
        log(f"[{s['name']}] FAILED: {entry['error']}")
    entry["finished"] = datetime.now().isoformat(timespec="seconds")
    record(s["id"], entry)
    return entry


async def scheduler_loop(client_factory: Callable[[], httpx.AsyncClient], stop: asyncio.Event, log: Callable[[str], None] = print, interval: float = 30.0) -> None:
    """Runs inside the app: every `interval` seconds start any due schedule (one at a time)."""
    await asyncio.sleep(5)
    while not stop.is_set():
        try:
            for s in due():
                if stop.is_set():
                    break
                async with client_factory() as client:
                    await run_schedule(s, client, log)
        except Exception as exc:  # noqa: BLE001
            log(f"[scheduler] {type(exc).__name__}: {exc}")
        try:
            await asyncio.wait_for(stop.wait(), timeout=interval)
        except asyncio.TimeoutError:
            pass
