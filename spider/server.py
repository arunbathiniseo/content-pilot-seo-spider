"""Local HTTP API + static UI. Served on 127.0.0.1 only; the desktop window (run.py) points at it."""
from __future__ import annotations

import asyncio
import base64
import hashlib
import hmac
import html
import json
import sys
import os
import re
import secrets
import sqlite3
import time
from contextlib import asynccontextmanager
from datetime import datetime
from pathlib import Path
from typing import Any
from urllib.parse import urlsplit

import httpx
from fastapi import FastAPI, HTTPException, Query, Request
from fastapi.responses import FileResponse, HTMLResponse, JSONResponse, RedirectResponse, Response
from fastapi.staticfiles import StaticFiles
from starlette.routing import Route
from pydantic import BaseModel

from . import db as dbm
from .ai import AIError, DEFAULT_MODELS, PROVIDER_NAMES, Provider, list_models
from .config import CrawlConfig, USER_AGENTS
from .connectors import connector_catalog, make_connector
from .crawler import Crawler
from .exports import COL_LABELS, REPORT_NAMES, export_filter, report, rows_for, to_csv, to_xlsx
from .fixes import FixJob, generate as generate_fixes, kind_for, push as push_fixes
from .issues import COLUMNS, TABS, all_filters, counts, filters_by_key

ROOT = Path(__file__).resolve().parents[1]
UI_DIR = ROOT / "ui"
CRAWLS_DIR = Path(os.environ.get("SPIDER_CRAWLS_DIR") or ROOT / "crawls")   # tests point this at a scratch folder
CRAWLS_DIR.mkdir(exist_ok=True)
SETTINGS_PATH = ROOT / "settings.json"          # persisted config incl. API keys and connector secrets (gitignored)
SECRET_KEYS = {"ai_api_key", "targets", "auth_pass", "custom_headers", "smtp_pass", "google_client_secret", "google_refresh_token", "ahrefs_token", "moz_secret", "majestic_key"}
SECRET_MASK = "\u2022" * 8   # what the settings API shows for a saved key or password
MASKED_KEYS = ("ai_api_key", "auth_pass", "smtp_pass", "google_client_secret", "google_refresh_token", "ahrefs_token", "moz_secret", "majestic_key", "psi_api_key")


def masked_config(d: dict) -> dict:
    """The settings as the interface gets them: a saved secret is shown as the mask, never as its value."""
    out = {**d, **{k: SECRET_MASK for k in MASKED_KEYS if d.get(k)}}
    out["targets"] = [{**x, "secret": SECRET_MASK} if x.get("secret") else x for x in d.get("targets") or []]
    out["custom_headers"] = {k: (SECRET_MASK if v else v) for k, v in (d.get("custom_headers") or {}).items()}
    return out


def saved_target_secret(target: dict) -> str:
    """The saved secret of the connector target this (masked) one stands for: same name, else same URL and username."""
    saved = State.cfg.targets or []
    hit = next((x for x in saved if x.get("name") == target.get("name")), None) or next(
        (x for x in saved if x.get("base_url") == target.get("base_url") and x.get("username") == target.get("username")), None)
    return (hit or {}).get("secret", "")


def unmasked_body(body: dict) -> dict:
    """Settings coming back from the interface: a secret that is still the mask was not edited, so it is left as saved."""
    out = {k: v for k, v in body.items() if not (k in MASKED_KEYS and v == SECRET_MASK)}
    if isinstance(out.get("targets"), list):
        out["targets"] = [{**x, "secret": saved_target_secret(x)} if isinstance(x, dict) and x.get("secret") == SECRET_MASK else x for x in out["targets"]]
    if isinstance(out.get("custom_headers"), dict):
        saved = State.cfg.custom_headers or {}
        out["custom_headers"] = {k: (saved.get(k, "") if v == SECRET_MASK else v) for k, v in out["custom_headers"].items()}
    return out


MACHINE_KEYS = ("ai_provider", "ai_api_key", "ai_model", "ai_base_url", "ai_concurrency", "ai_batch_size", "ai_brand", "ai_instructions", "ai_system_prompts",
                "targets", "email_enabled", "email_to", "email_from", "email_include_compare", "smtp_host", "smtp_port", "smtp_security", "smtp_user", "smtp_pass",
                "mcp_enabled", "app_port", "spell_ignore", "google_client_id", "google_client_secret", "google_refresh_token", "google_account", "gsc_property", "ga4_property",
                "gsc_days", "gsc_auto", "gsc_inspect_max", "google_base_override", "lighthouse_enabled", "lighthouse_max_pages", "lighthouse_device", "lighthouse_concurrency", "psi_api_key",
                "ahrefs_token", "ahrefs_country", "moz_access_id", "moz_secret", "majestic_key", "majestic_datasource", "linkmetrics_max_urls", "linkmetrics_auto", "linkmetrics_base_override")   # this machine's settings: kept when an old crawl's config is loaded

MCP = None                 # MCPServer once _mcp_setup has run (None while starting, or when the mcp package is missing)
MCP_INSTALLED = True       # flipped to False if the import fails
_mcp_stop: asyncio.Event | None = None
_mcp_task: asyncio.Task | None = None


async def _mcp_setup(stop: asyncio.Event) -> None:
    """Import the mcp package (~2 s, off the event loop), build the tool server and mount /mcp. Runs as a background task
    so the window and the API are usable immediately after launch; /mcp answers 404 until this finishes."""
    global MCP, MCP_INSTALLED
    import importlib
    try:
        mod = await asyncio.to_thread(importlib.import_module, "spider.mcp_server")
    except ImportError:
        MCP_INSTALLED = False
        return
    srv = mod.build_mcp(lambda: httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url="http://127.0.0.1", timeout=httpx.Timeout(900.0)))
    sub = srv.streamable_http_app(streamable_http_path="/mcp", stateless_http=True)
    asgi = next(r.app for r in sub.routes if getattr(r, "path", "") == "/mcp")
    async with srv.session_manager.run():
        app.router.routes.append(Route("/mcp", endpoint=_McpGate(asgi)))
        MCP = srv
        await stop.wait()


def _warm_imports() -> None:
    """Heavy optional libraries (textstat -> nltk -> scipy) load in the background so the first crawl does not pay for them."""
    try:
        from . import textmetrics
        textmetrics.warm()
    except Exception:  # noqa: BLE001
        pass


_sched_task: asyncio.Task | None = None


@asynccontextmanager
async def lifespan(_app: FastAPI):
    global _mcp_stop, _mcp_task, _sched_task
    _mcp_stop = asyncio.Event()
    _mcp_task = asyncio.create_task(_mcp_setup(_mcp_stop))
    from .schedule import scheduler_loop
    _sched_task = asyncio.create_task(scheduler_loop(lambda: httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url="http://127.0.0.1", timeout=httpx.Timeout(900.0)), _mcp_stop))
    import threading
    threading.Thread(target=_warm_imports, daemon=True).start()
    jobs.load(CRAWLS_DIR / "jobs.json")   # the crawl queue (hosted mode): waiting jobs survive a restart
    jobs.next()
    try:
        yield
    finally:
        _mcp_stop.set()
        try:
            await asyncio.wait_for(_mcp_task, 5)
        except Exception:  # noqa: BLE001
            pass


app = FastAPI(title="Content Pilot Spider", lifespan=lifespan)
app.mount("/ui", StaticFiles(directory=str(UI_DIR)), name="ui")


@app.middleware("http")
async def _ui_no_stale_cache(request, call_next):
    """The desktop window (WebView2) heuristically caches files sent without Cache-Control, so it kept running an old
    app.js after updates. Make it revalidate the page and our own UI files every time (vendor libraries can cache)."""
    resp = await call_next(request)
    path = request.url.path
    if path == "/" or (path.startswith("/ui/") and not path.startswith("/ui/vendor/")):
        resp.headers["Cache-Control"] = "no-cache"
    return resp


def load_settings() -> CrawlConfig:
    try:
        if SETTINGS_PATH.exists():
            return CrawlConfig.from_dict(json.loads(SETTINGS_PATH.read_text(encoding="utf-8")))
    except (OSError, ValueError, TypeError):
        pass
    return CrawlConfig()


def save_settings(cfg: CrawlConfig) -> None:
    d = cfg.to_dict()
    for k in ("start_url", "list_urls", "sitemap_urls", "mode"):
        d.pop(k, None)
    SETTINGS_PATH.write_text(json.dumps(d, indent=1), encoding="utf-8")


class State:
    crawler: Crawler | None = None
    task: asyncio.Task | None = None
    db_path: Path | None = None
    cfg: CrawlConfig = load_settings()
    fix_job: FixJob = FixJob()
    fix_task: asyncio.Task | None = None
    lh_job: Any = None
    lh_task: asyncio.Task | None = None
    g_job: Any = None
    g_task: asyncio.Task | None = None
    lm_job: Any = None
    lm_task: asyncio.Task | None = None
    _read: sqlite3.Connection | None = None
    _read_path: Path | None = None
    _counts_cache: tuple[float, dict[str, int]] | None = None

    @classmethod
    def read(cls) -> sqlite3.Connection:
        if cls.db_path is None or not cls.db_path.exists():
            raise HTTPException(404, "No crawl open")
        if cls._read is None or cls._read_path != cls.db_path:
            if cls._read is not None:
                cls._read.close()
            cls._read = dbm.connect(cls.db_path, readonly=False)   # same schema; writer only writes in the crawler
            cls._read_path = cls.db_path
        return cls._read

    @classmethod
    def counts(cls) -> dict[str, int]:
        now = time.time()
        if cls._counts_cache and now - cls._counts_cache[0] < 2.0:
            return cls._counts_cache[1]
        c = counts(cls.read(), cls.cfg)
        cls._counts_cache = (now, c)
        return c


# ----------------------------------------------------------------------------- pages
@app.get("/", response_class=HTMLResponse)
def index():
    html = (UI_DIR / "index.html").read_text(encoding="utf-8")
    for name in ("styles.css", "icons.js", "app.js"):   # version stamp = file mtime, so an updated file is always refetched
        f = UI_DIR / name
        if f.exists():
            html = html.replace(f'"/ui/{name}"', f'"/ui/{name}?v={int(f.stat().st_mtime)}"')
    return html


# ----------------------------------------------------------------------------- crawl control
# ----------------------------------------------------------------------------- crawl queue (hosted mode, 3 Nov 2026)
# The box crawls one site at a time. In hosted mode a start while a crawl runs is queued instead of refused: the answer
# is 202 with a job id and a position, the queue runs in order as each crawl ends, and a job is read by id. A start for
# a host that already has a waiting job returns that job (one click per site is enough, however many users click).
# The queue lives in <crawls>/jobs.json, so waiting jobs survive a restart; a job that was running when the service
# stopped is marked "interrupted" and its partial crawl file kept. The desktop app (no key) keeps answering 409.
QUEUE_MAX = 100
ENDED_KEEP = 200


class _Jobs:
    def __init__(self) -> None:
        self.items: list[dict[str, Any]] = []
        self.running: str | None = None
        self.path: Path | None = None

    # --- persistence
    def load(self, path: Path) -> None:
        self.path = path
        try:
            data = json.loads(path.read_text(encoding="utf-8"))
            self.items = [j for j in data.get("jobs", []) if isinstance(j, dict) and j.get("id")]
        except (OSError, ValueError):
            self.items = []
        for j in self.items:
            if j.get("state") == "running":
                j.update(state="interrupted", finished_at=_now(), error="The Spider restarted while this crawl ran; its partial crawl file is kept.")
        self.running = None
        self.save()

    def save(self) -> None:
        if self.path is None:
            return
        ended = [j for j in self.items if j["state"] not in ("queued", "running")]
        keep = {j["id"] for j in ended[-ENDED_KEEP:]}
        self.items = [j for j in self.items if j["state"] in ("queued", "running") or j["id"] in keep]
        tmp = self.path.with_suffix(".tmp")
        tmp.write_text(json.dumps({"jobs": self.items}, indent=1), encoding="utf-8")
        tmp.replace(self.path)

    # --- reading
    def _waiting(self) -> list[dict[str, Any]]:
        return [j for j in self.items if j["state"] == "queued"]

    def _out(self, j: dict[str, Any]) -> dict[str, Any]:
        o = {k: v for k, v in j.items() if k != "config"}
        o["crawl"] = Path(j["db"]).name if j.get("db") else None     # the crawl file name, for crawl=<file>
        o["position"] = self._waiting().index(j) + 1 if j["state"] == "queued" else 0
        o["max_urls"] = int(j.get("config", {}).get("max_urls") or 0)
        o["max_urls_per_second"] = j.get("config", {}).get("max_urls_per_second")
        o["max_threads"] = j.get("config", {}).get("max_threads")
        if j["state"] == "running" and State.crawler is not None:
            st = State.crawler.status()
            o["progress"] = {k: st.get(k) for k in ("state", "crawled", "queued", "total", "elapsed", "rate", "current")}
        return o

    def get(self, job_id: str) -> dict[str, Any] | None:
        j = next((x for x in self.items if x["id"] == job_id), None)
        return self._out(j) if j else None

    def listing(self) -> list[dict[str, Any]]:
        return [self._out(j) for j in reversed(self.items)]

    def summary(self) -> dict[str, Any]:
        return {"running": self.running, "waiting": [{"id": j["id"], "start_url": j["start_url"], "host": j["host"], "position": i + 1,
                                                       "created_at": j["created_at"]} for i, j in enumerate(self._waiting())]}

    # --- writing
    def submit(self, cfg: CrawlConfig) -> JSONResponse:
        host = (urlsplit(cfg.start_url).hostname or "").lower().removeprefix("www.")
        same = next((j for j in self._waiting() if j["host"] == host and j["mode"] == cfg.mode), None)
        if same is not None:
            return JSONResponse({"ok": True, "queued": True, "duplicate": True, **self._out(same)}, status_code=202)
        busy = bool(State.task and not State.task.done())
        if busy and len(self._waiting()) >= QUEUE_MAX:
            raise HTTPException(429, f"The crawl queue is full ({QUEUE_MAX} waiting); try again later")
        j = {"id": secrets.token_hex(6), "state": "queued", "start_url": cfg.start_url, "host": host, "mode": cfg.mode,
             "created_at": _now(), "started_at": None, "finished_at": None, "db": None, "error": None, "crawled": 0,
             "config": cfg.to_dict()}
        self.items.append(j)
        if busy:
            self.save()
            return JSONResponse({"ok": True, "queued": True, **self._out(j)}, status_code=202)
        self._start(j)
        return JSONResponse({"ok": True, "queued": False, **self._out(j)}, status_code=200)

    def _start(self, j: dict[str, Any]) -> None:
        try:
            db = _launch(CrawlConfig.from_dict(j["config"]))
        except Exception as exc:  # noqa: BLE001 - a job that cannot start must not block the queue
            j.update(state="error", finished_at=_now(), error=f"Could not start: {exc}")
            self.save()
            self.next()
            return
        j.update(state="running", started_at=_now(), db=str(db))
        self.running = j["id"]
        self.save()

    def ended(self, task: asyncio.Task) -> None:
        j = next((x for x in self.items if x["id"] == self.running), None)
        if j is not None:
            st = State.crawler.status() if State.crawler is not None else {}
            if task.cancelled():
                state, err = "stopped", None
            elif task.exception() is not None:
                state, err = "error", repr(task.exception())
            else:
                state, err = (st.get("state") or "finished"), (st.get("error") or None)
            j.update(state=state if state in ("finished", "stopped", "error") else "finished", finished_at=_now(), error=err,
                     crawled=int(st.get("crawled") or 0))
        self.running = None
        self.save()
        self.next()

    def next(self) -> None:
        if State.task and not State.task.done():
            return
        w = self._waiting()
        if w:
            self._start(w[0])

    def cancel(self, job_id: str) -> dict[str, Any] | None:
        j = next((x for x in self.items if x["id"] == job_id), None)
        if j is None:
            return None
        if j["state"] == "queued":
            j.update(state="cancelled", finished_at=_now())
            self.save()
        elif j["state"] == "running" and State.crawler is not None:
            State.crawler.stop()
        return self._out(j)


def _now() -> str:
    return datetime.now().isoformat(timespec="seconds")


jobs = _Jobs()


class StartBody(BaseModel):
    start_url: str = ""
    mode: str = "spider"
    list_urls: list[str] = []
    config: dict[str, Any] = {}


# Hosted pacing (3 Nov 2026): with SPIDER_API_KEY set, every crawl is paced so a crowd of users cannot load the box or
# the sites crawled. max_urls_per_second defaults to HOSTED_RATE (about 500 URLs in 28 minutes) and is clamped to
# [HOSTED_RATE_MIN, HOSTED_RATE_MAX]; max_threads (parallel connections) defaults to 1 and is clamped to
# HOSTED_THREADS_MAX; render_concurrency to HOSTED_THREADS_MAX. Only the values in this request count: the shared
# settings never carry a faster pace from one crawl to the next.
HOSTED_RATE, HOSTED_RATE_MIN, HOSTED_RATE_MAX, HOSTED_THREADS_MAX = 0.3, 0.05, 2.0, 2


def _hosted_pace(cfg: CrawlConfig, asked: dict[str, Any]) -> None:
    from .netguard import hosted
    if not hosted():
        return
    try:
        rate = float(asked.get("max_urls_per_second") or 0) or HOSTED_RATE
    except (TypeError, ValueError):
        rate = HOSTED_RATE
    try:
        threads = int(asked.get("max_threads") or 1)
    except (TypeError, ValueError):
        threads = 1
    cfg.max_urls_per_second = round(min(max(rate, HOSTED_RATE_MIN), HOSTED_RATE_MAX), 3)
    cfg.max_threads = max(1, min(threads, HOSTED_THREADS_MAX))
    cfg.render_concurrency = max(1, min(cfg.render_concurrency, HOSTED_THREADS_MAX))


@app.post("/api/crawl/start")
async def crawl_start(body: StartBody):
    from .netguard import hosted
    if State.task and not State.task.done() and not hosted():
        raise HTTPException(409, "A crawl is already running")
    cfg = CrawlConfig.from_dict({**State.cfg.to_dict(), **unmasked_body(body.config)})
    _hosted_pace(cfg, body.config)
    cfg.mode = body.mode
    cfg.list_urls = [u.strip() for u in body.list_urls if u.strip()]
    url = body.start_url.strip()
    if cfg.mode in ("spider", "sitemap"):
        if not url:
            raise HTTPException(400, "Enter a URL to crawl" if cfg.mode == "spider" else "Enter the XML sitemap URL")
        if not re.match(r"^https?://", url, re.I):
            url = "https://" + url
        host = urlsplit(url).hostname or ""
        if "." not in host and host not in ("localhost",):
            raise HTTPException(400, f"'{host or url}' does not look like a website address (expected something like www.example.com)")
        if " " in url:
            raise HTTPException(400, "The URL contains a space")
        cfg.start_url = url
        if cfg.mode == "sitemap":
            cfg.sitemap_urls = [url]
    else:
        cfg.list_urls = [u if re.match(r"^https?://", u, re.I) else "https://" + u for u in cfg.list_urls]
        bad = [u for u in cfg.list_urls if "." not in (urlsplit(u).hostname or "")]
        if bad:
            raise HTTPException(400, f"{len(bad)} line(s) are not URLs, e.g. '{bad[0][:60]}'")
        if not cfg.list_urls:
            raise HTTPException(400, "Paste at least one URL")
        cfg.start_url = cfg.list_urls[0]
    from .netguard import refuse_reason
    for u in {urlsplit(x).hostname: x for x in [cfg.start_url, *cfg.list_urls]}.values():   # hosted mode: public sites only, one check per host (fetch and render enforce it too)
        why = await refuse_reason(u)
        if why:
            raise HTTPException(400, why)
    if cfg.rendering == "javascript" and not system_check()["chromium"]:
        raise HTTPException(400, "JavaScript rendering is on but Chromium is not installed. Run:  .venv\\Scripts\\python.exe -m playwright install chromium  — or switch Config → Rendering to Text Only.")
    if hosted():
        return jobs.submit(cfg)
    return {"ok": True, "db": str(_launch(cfg))}


def _launch(cfg: CrawlConfig) -> Path:
    """Open a new crawl file for `cfg` and start crawling it now; the crawl becomes the open crawl."""
    host = (urlsplit(cfg.start_url).hostname or "list").replace(":", "_")
    db_path = CRAWLS_DIR / f"{host}_{datetime.now().strftime('%Y%m%d_%H%M%S')}.db"
    n = 1
    while db_path.exists():                     # two queued crawls of a host started within the same second
        n += 1
        db_path = CRAWLS_DIR / f"{host}_{datetime.now().strftime('%Y%m%d_%H%M%S')}_{n}.db"
    State.cfg = cfg
    State.db_path = db_path
    State._counts_cache = None
    State.crawler = Crawler(db_path, cfg)
    State.task = asyncio.create_task(State.crawler.run())
    State.task.add_done_callback(lambda t: asyncio.ensure_future(_crawl_ended(t)))
    return db_path


async def _crawl_ended(task: asyncio.Task) -> None:
    try:
        await _after_crawl(task)
    finally:
        jobs.ended(task)


async def _after_crawl(task: asyncio.Task) -> None:
    """Runs when the crawl task ends: usage record, then the crawl-complete email (Config > Email) if enabled."""
    if task.cancelled() or task.exception() or State.crawler is None:
        return
    try:
        from . import usage as usage_log
        st = State.crawler.status()
        pages = 0
        if State.db_path and State.db_path.exists():
            con = dbm.connect(State.db_path, readonly=True)
            try:
                pages = con.execute("SELECT count(*) FROM urls WHERE crawled=1 AND is_internal=1 AND type='HTML'").fetchone()[0]
                started = dbm.get_meta(con, "started_at", "")
            finally:
                con.close()
        else:
            started = ""
        usage_log.record_crawl(State.db_path.name if State.db_path else "", State.cfg.start_url, State.cfg.mode, st.get("state", ""), st.get("crawled", 0), pages,
                               st.get("elapsed", 0), started, State.cfg.rendering)
    except Exception as exc:  # noqa: BLE001
        State.last_email = {"ok": False, "message": f"usage record failed: {exc}", "at": datetime.now().isoformat(timespec="seconds")}
    if State.cfg.lighthouse_enabled and State.db_path and State.db_path.exists():
        try:
            from .lighthouse import LighthouseJob, run_job, sample_ids
            con = State.read()
            ids = sample_ids(con, State.cfg, State.cfg.lighthouse_max_pages)
            if ids and not (State.lh_task and not State.lh_task.done()):
                State.lh_job = LighthouseJob()
                State.lh_task = asyncio.create_task(run_job(con, State.cfg, ids, State.lh_job))
        except Exception as exc:  # noqa: BLE001
            print(f"[lighthouse] auto-run failed to start: {exc}")
    if State.cfg.gsc_auto and State.cfg.google_refresh_token and State.db_path and State.db_path.exists():
        try:
            from .google import GoogleJob, run_job as google_run
            if not (State.g_task and not State.g_task.done()):
                State.g_job = GoogleJob()
                State.g_task = asyncio.create_task(google_run(State.read(), State.cfg, State.g_job, True, True, State.cfg.gsc_inspect_max))
        except Exception as exc:  # noqa: BLE001
            print(f"[google] auto-run failed to start: {exc}")
    if State.cfg.linkmetrics_auto and State.db_path and State.db_path.exists():
        try:
            from .linkmetrics import LinkMetricsJob, candidates, providers, run_job as lm_run
            if providers(State.cfg) and not (State.lm_task and not State.lm_task.done()):
                con = State.read()
                ids = [i for i, _ in candidates(con, State.cfg.linkmetrics_max_urls)]
                State.lm_job = LinkMetricsJob()
                State.lm_task = asyncio.create_task(lm_run(con, State.cfg, ids, State.lm_job))
        except Exception as exc:  # noqa: BLE001
            print(f"[linkmetrics] auto-run failed to start: {exc}")
    if not State.cfg.email_enabled:
        return
    try:
        await asyncio.to_thread(_send_crawl_email, State.crawler.status(), State.db_path)
    except Exception as exc:  # noqa: BLE001 - never let a mail problem surface as a crawl error
        State.last_email = {"ok": False, "message": str(exc), "at": datetime.now().isoformat(timespec="seconds")}


def _send_crawl_email(status: dict[str, Any], db_path: Path | None) -> None:
    from .compare import compare, previous_crawl
    from .notify import build_email, send_email
    cfg = State.cfg
    con = dbm.connect(db_path, readonly=True)
    try:
        cmp = None
        if cfg.email_include_compare and db_path is not None:
            prev = previous_crawl(db_path, CRAWLS_DIR)
            if prev is not None:
                try:
                    cmp = compare(prev, db_path, cfg)
                except Exception:  # noqa: BLE001
                    cmp = None
        status = {**status, "start_url": cfg.start_url}
        subject, text, body_html = build_email(con, cfg, status, cmp)
    finally:
        con.close()
    to = send_email(cfg, subject, text, body_html)
    State.last_email = {"ok": True, "message": f"Sent to {to}: {subject}", "at": datetime.now().isoformat(timespec="seconds")}


@app.post("/api/email/test")
async def email_test():
    """Send the crawl-complete email for the open crawl (or a short hello when none is open) with the saved settings."""
    from .notify import EmailError, build_email, send_email
    cfg = State.cfg
    try:
        if State.db_path and State.db_path.exists():
            con = dbm.connect(State.db_path, readonly=True)
            try:
                subject, text, body_html = build_email(con, cfg, {"state": "test", "start_url": cfg.start_url, "elapsed": 0})
            finally:
                con.close()
            subject = "[Test] " + subject
        else:
            subject, text, body_html = "[Content Pilot Spider] Test email", "Email settings work. Crawl-complete reports will arrive like this.", ""
        to = await asyncio.to_thread(send_email, cfg, subject, text, body_html)
    except EmailError as exc:
        raise HTTPException(400, str(exc))
    return {"ok": True, "message": f"Sent to {to}"}


@app.get("/api/email/last")
def email_last():
    return getattr(State, "last_email", None) or {}


@app.post("/api/crawl/pause")
def crawl_pause():
    if State.crawler:
        State.crawler.pause()
    return status()


@app.post("/api/crawl/resume")
def crawl_resume():
    if State.crawler:
        State.crawler.resume()
    return status()


@app.post("/api/crawl/stop")
def crawl_stop():
    if State.crawler:
        State.crawler.stop()
    return status()


@app.get("/api/crawl/status")
def status():
    if State.crawler is None:
        s = {"state": "idle", "crawled": 0, "queued": 0, "total": 0, "elapsed": 0, "rate": 0, "current": [], "db": str(State.db_path or "")}
    else:
        s = State.crawler.status()
        if State.task and State.task.done() and State.task.exception():
            s["state"] = "error"
            s["error"] = repr(State.task.exception())
    s["jobs"] = jobs.summary()
    return s


@app.get("/api/crawl/jobs")
def crawl_jobs():
    """Every job the queue remembers (running, waiting, and the last 200 ended), newest first."""
    return {"jobs": jobs.listing(), **jobs.summary()}


@app.get("/api/crawl/jobs/{job_id}")
def crawl_job(job_id: str):
    j = jobs.get(job_id)
    if j is None:
        raise HTTPException(404, f"No crawl job {job_id}")
    return j


@app.post("/api/crawl/jobs/{job_id}/cancel")
def crawl_job_cancel(job_id: str):
    """Drop a waiting job, or stop the running one (it keeps what it crawled, like Stop)."""
    j = jobs.cancel(job_id)
    if j is None:
        raise HTTPException(404, f"No crawl job {job_id}")
    return j


# ----------------------------------------------------------------------------- config
@app.get("/api/config")
def get_config():
    return {"config": masked_config(State.cfg.to_dict()), "defaults": CrawlConfig().to_dict(), "user_agents": USER_AGENTS}


def validate_config(cfg: CrawlConfig) -> dict[str, str]:
    """Field -> problem. Everything the UI cannot catch on its own (regex compile, ranges, connector sanity)."""
    errors: dict[str, str] = {}
    for field_name in ("include_regex", "exclude_regex"):
        for pat in getattr(cfg, field_name):
            try:
                re.compile(pat)
            except re.error as exc:
                errors[field_name] = f"'{pat}' is not a valid regular expression ({exc})"
                break
    ranges = {"max_threads": (1, 50), "max_urls_per_second": (0, 1000), "timeout": (1, 300), "retries_5xx": (0, 5), "max_redirects": (1, 30),
              "max_url_length": (100, 10000), "max_page_size_kb": (64, 102400), "render_ajax_timeout": (0, 60), "render_wait_extra": (0, 60),
              "render_width": (320, 4000), "render_height": (320, 4000), "render_concurrency": (1, 10), "ai_concurrency": (1, 10), "ai_batch_size": (1, 20),
              "title_max_chars": (10, 200), "title_min_chars": (0, 200), "desc_max_chars": (10, 400), "desc_min_chars": (0, 400), "low_content_words": (0, 5000),
              "high_crawl_depth": (1, 50), "max_depth": (0, 100), "max_urls": (0, 5_000_000), "app_port": (1024, 65535), "near_duplicate_threshold": (30, 99)}
    for k, (lo, hi) in ranges.items():
        v = getattr(cfg, k, None)
        if v is None or v < lo or v > hi:
            errors[k] = f"must be between {lo} and {hi}"
    if cfg.title_min_chars >= cfg.title_max_chars:
        errors["title_min_chars"] = "must be lower than the title max"
    if cfg.desc_min_chars >= cfg.desc_max_chars:
        errors["desc_min_chars"] = "must be lower than the description max"
    if cfg.ai_base_url and not re.match(r"^https?://", cfg.ai_base_url):
        errors["ai_base_url"] = "must start with http:// or https://"
    if cfg.email_enabled:
        if not cfg.email_to.strip():
            errors["email_to"] = "enter at least one recipient"
        if not cfg.smtp_host.strip():
            errors["smtp_host"] = "enter the SMTP server"
    if cfg.email_to.strip() and not all(re.match(r"^[^@\s]+@[^@\s]+\.[^@\s]+$", a.strip()) for a in cfg.email_to.replace(";", ",").split(",") if a.strip()):
        errors["email_to"] = "one or more addresses do not look valid"
    if not (1 <= int(cfg.smtp_port or 0) <= 65535):
        errors["smtp_port"] = "must be between 1 and 65535"
    if cfg.smtp_security not in ("starttls", "ssl", "none"):
        errors["smtp_security"] = "must be starttls, ssl or none"
    if cfg.grammar_lt_url and not re.match(r"^https?://", cfg.grammar_lt_url):
        errors["grammar_lt_url"] = "must start with http:// or https://"
    if (cfg.spell_language or "en").split("-")[0].lower() not in ("en", "de", "fr", "es", "pt", "it", "nl", "ru"):
        errors["spell_language"] = "supported: en, de, fr, es, pt, it, nl, ru"
    if not (1 <= cfg.spell_learn_min_pages <= 1000):
        errors["spell_learn_min_pages"] = "must be between 1 and 1000"
    if not (0 <= cfg.spell_learn_share <= 100):
        errors["spell_learn_share"] = "must be between 0 and 100"
    seen = set()
    for t in cfg.targets:
        name = (t.get("name") or "").strip()
        if not name:
            errors["targets"] = "every target needs a name"
        elif name in seen:
            errors["targets"] = f"two targets are named '{name}'"
        seen.add(name)
        if not re.match(r"^https?://", t.get("base_url") or ""):
            errors["targets"] = f"target '{name}': URL must start with http:// or https://"
        if t.get("type") in ("wordpress", "wordpress_xmlrpc") and not (t.get("username") and t.get("secret")):
            errors["targets"] = f"target '{name}': WordPress needs a username and an application password"
    for i, cs in enumerate(cfg.custom_search):
        if cs.get("regex"):
            try:
                re.compile(cs.get("text", ""))
            except re.error as exc:
                errors["custom_search"] = f"search {i + 1}: invalid regex ({exc})"
    for i, ce in enumerate(cfg.custom_extraction):
        if ce.get("type") not in ("xpath", "css", "regex"):
            errors["custom_extraction"] = f"extractor {i + 1}: type must be xpath, css or regex"
        elif ce["type"] == "regex":
            try:
                re.compile(ce.get("expr", ""))
            except re.error as exc:
                errors["custom_extraction"] = f"extractor {i + 1}: invalid regex ({exc})"
        elif ce["type"] == "xpath":
            try:
                from lxml import etree
                etree.XPath(ce.get("expr", ""))
            except Exception as exc:  # noqa: BLE001
                errors["custom_extraction"] = f"extractor {i + 1}: invalid XPath ({str(exc)[:80]})"
    return errors


@app.post("/api/config")
def set_config(body: dict[str, Any]):
    cfg = CrawlConfig.from_dict({**State.cfg.to_dict(), **unmasked_body(body)})
    errors = validate_config(cfg)
    if errors:
        return JSONResponse({"ok": False, "errors": errors}, status_code=422)
    State.cfg = cfg
    State._counts_cache = None
    save_settings(State.cfg)
    return {"ok": True, "config": masked_config(State.cfg.to_dict())}


@app.get("/api/system/check")
def system_check():
    """What this machine can do: JS rendering needs Playwright + a downloaded Chromium."""
    out: dict[str, Any] = {"playwright": False, "chromium": False, "chromium_path": "", "crawls_dir": str(CRAWLS_DIR), "app_dir": str(ROOT)}
    try:
        import playwright  # noqa: F401
        out["playwright"] = True
        from playwright._impl._driver import compute_driver_executable  # noqa: F401
        base = Path(os.environ.get("PLAYWRIGHT_BROWSERS_PATH") or (Path(os.environ.get("LOCALAPPDATA", "")) / "ms-playwright"))
        for p in sorted(base.glob("chromium*")) if base.exists() else []:
            exe = next(p.rglob("chrome.exe"), None) or next(p.rglob("headless_shell.exe"), None) or next(p.rglob("chrome"), None)
            if exe:
                out["chromium"], out["chromium_path"] = True, str(exe)
                break
    except Exception:  # noqa: BLE001
        pass
    try:
        import shutil
        out["disk_free_gb"] = round(shutil.disk_usage(str(CRAWLS_DIR)).free / 1e9, 1)
    except OSError:
        pass
    from .lighthouse import available
    out.update({f"lighthouse_{k}": v for k, v in available().items()})
    return out


# ----------------------------------------------------------------------------- AI + fixes
@app.get("/api/ai/catalog")
def ai_catalog():
    return {"providers": [{"id": k, "name": v, "default_model": DEFAULT_MODELS.get(k, "")} for k, v in PROVIDER_NAMES.items()],
            "connectors": connector_catalog()}


@app.get("/api/ai/models")
async def ai_models():
    """Live model list from the configured provider, plus whether the chosen model is in it (null = could not check)."""
    cfg = State.cfg
    current = cfg.ai_model or DEFAULT_MODELS.get((cfg.ai_provider or "anthropic").lower(), "")
    try:
        models = await list_models(cfg)
    except AIError as exc:
        return {"models": [], "current": current, "valid": None, "error": str(exc)}
    return {"models": sorted(models), "current": current, "valid": (current in models) if models else None, "error": ""}


@app.post("/api/ai/test")
async def ai_test():
    try:
        p = Provider(State.cfg)
        text = await p.complete("You are a connectivity check. Reply with the single word OK.", "Say OK.", max_tokens=20)
    except AIError as exc:
        raise HTTPException(400, str(exc))
    from . import usage as usage_log
    usage_log.record_ai(p.provider, p.model, "test", p.requests, p.input_tokens, p.output_tokens, label="Test connection")
    msg = f"{p.label} answered: {text.strip()[:60]}"
    try:
        models = await list_models(State.cfg)
        if models and p.model not in models:
            msg += f" · warning: '{p.model}' is not in this provider's model list ({len(models)} models); pick one from the dropdown."
    except AIError:
        pass
    return {"ok": True, "message": msg}


class GenerateBody(BaseModel):
    key: str
    ids: list[int] = []


@app.post("/api/fixes/generate")
async def fixes_generate(body: GenerateBody):
    if State.fix_task and not State.fix_task.done():
        raise HTTPException(409, "A fix generation is already running")
    con = State.read()
    from .fixes import is_rule_kind
    kind = kind_for(body.key)
    State.fix_job = FixJob()
    State.fix_task = asyncio.create_task(generate_fixes(con, State.cfg, body.key, body.ids or None, State.fix_job))
    return {"ok": True, "kind": kind, "rule": is_rule_kind(kind)}


@app.post("/api/fixes/cancel")
def fixes_cancel():
    State.fix_job.cancel()
    return {"ok": True}


@app.get("/api/fixes/status")
def fixes_status():
    s = State.fix_job.status()
    if State.db_path and State.db_path.exists():
        con = State.read()
        s["counts"] = {r[0]: r[1] for r in con.execute("SELECT status, count(*) FROM fixes GROUP BY status")}
        s["total_fixes"] = sum(s["counts"].values())
    else:
        s["counts"], s["total_fixes"] = {}, 0
    return s


@app.get("/api/fixes")
def fixes_list(status: str = "", kind: str = "", q: str = "", page: int = 1, size: int = 200):
    con = State.read()
    where, params = ["1=1"], []
    if status:
        where.append("status=?"); params.append(status)
    if kind:
        where.append("kind=?"); params.append(kind)
    if q:
        where.append("(url LIKE ? OR proposed LIKE ? OR current LIKE ?)"); params += [f"%{q}%"] * 3
    w = " AND ".join(where)
    total = con.execute(f"SELECT count(*) FROM fixes WHERE {w}", params).fetchone()[0]
    rows = [dict(r) for r in con.execute(f"SELECT * FROM fixes WHERE {w} ORDER BY id DESC LIMIT ? OFFSET ?", params + [size, (page - 1) * size])]
    kinds = [r[0] for r in con.execute("SELECT DISTINCT kind FROM fixes ORDER BY kind")]
    return {"data": rows, "total": total, "last_page": max(1, -(-total // size)), "kinds": kinds}


class FixUpdate(BaseModel):
    id: int
    proposed: str | None = None
    status: str | None = None


@app.post("/api/fixes/update")
def fixes_update(body: FixUpdate):
    con = State.read()
    sets, params = ["updated_at=?"], [datetime.utcnow().strftime("%Y-%m-%d %H:%M:%S")]
    if body.proposed is not None:
        sets.append("proposed=?"); params.append(body.proposed.strip())
    if body.status in ("proposed", "approved", "rejected"):
        sets.append("status=?"); params.append(body.status)
    params.append(body.id)
    con.execute(f"UPDATE fixes SET {', '.join(sets)} WHERE id=?", params)
    con.commit()
    return {"ok": True}


class FixBulk(BaseModel):
    ids: list[int] = []
    status: str = "approved"
    filter_status: str = ""
    filter_kind: str = ""
    delete: bool = False


@app.post("/api/fixes/bulk")
def fixes_bulk(body: FixBulk):
    con = State.read()
    where, params = ["1=1"], []
    if body.ids:
        where.append(f"id IN ({','.join('?' * len(body.ids))})"); params += body.ids
    else:
        if body.filter_status:
            where.append("status=?"); params.append(body.filter_status)
        if body.filter_kind:
            where.append("kind=?"); params.append(body.filter_kind)
    w = " AND ".join(where)
    if body.delete:
        n = con.execute(f"DELETE FROM fixes WHERE {w}", params).rowcount
    else:
        if body.status not in ("proposed", "approved", "rejected"):
            raise HTTPException(400, "status must be proposed, approved or rejected")
        n = con.execute(f"UPDATE fixes SET status=?, updated_at=? WHERE {w} AND status<>'pushed'", [body.status, datetime.utcnow().strftime("%Y-%m-%d %H:%M:%S")] + params).rowcount
    con.commit()
    return {"ok": True, "affected": n}


class TargetTest(BaseModel):
    target: dict[str, Any] | None = None
    name: str = ""


@app.post("/api/targets/test")
async def targets_test(body: TargetTest):
    t = body.target or next((x for x in State.cfg.targets if x.get("name") == body.name), None)
    if t and t.get("secret") == SECRET_MASK:   # the form still shows the mask: test with the saved secret
        t = {**t, "secret": saved_target_secret(t)}
    if not t:
        raise HTTPException(404, "Target not found")
    try:
        res = await make_connector(t).test()
    except ValueError as exc:
        raise HTTPException(400, str(exc))
    return {"ok": res.ok, "message": res.message, "detail": res.detail}


class PushBody(BaseModel):
    target: str
    ids: list[int] = []
    confirm: str = ""


@app.post("/api/fixes/push")
async def fixes_push(body: PushBody):
    t = next((x for x in State.cfg.targets if x.get("name") == body.target), None)
    if not t:
        raise HTTPException(404, "Target not found. Add it in Config > Connectors.")
    if (t.get("env") or "").lower() == "live" and body.confirm.strip() != body.target:
        raise HTTPException(400, f"This target is LIVE. Type its name ({body.target}) to confirm.")
    con = State.read()
    try:
        return await push_fixes(con, State.cfg, body.target, body.ids or None)
    except ValueError as exc:
        raise HTTPException(400, str(exc))


@app.get("/api/fixes/export")
def fixes_export(format: str = "csv", status: str = ""):
    con = State.read()
    w = "WHERE status=?" if status else ""
    rows = [list(r) for r in con.execute(f"SELECT url, kind, field, issue_name, current, proposed, rationale, status, target, result, created_at, pushed_at FROM fixes {w} ORDER BY id", [status] if status else [])]
    headers = ["URL", "Kind", "Field", "Issue", "Current", "Proposed", "Rationale", "Status", "Target", "Result", "Created", "Pushed"]
    if format == "xlsx":
        return Response(to_xlsx([("Fixes", headers, rows)]), media_type="application/octet-stream", headers={"Content-Disposition": 'attachment; filename="fixes.xlsx"'})
    if format == "json":
        return {"fixes": [dict(zip([h.lower() for h in headers], r)) for r in rows]}
    return Response(to_csv(headers, rows), media_type="text/csv", headers={"Content-Disposition": 'attachment; filename="fixes.csv"'})


# ----------------------------------------------------------------------------- tabs / rows
# Report API (3 Nov 2026): /api/tabs, /api/issues, /api/urls, /api/urls/values, /api/url/{id} and /api/url/{id}/source
# answer for the open crawl, or, with `crawl=<file name>`, for any saved crawl read-only and with that crawl's own
# settings, without opening it (the app's Site audit report reads finished crawls this way). Same shapes either way.
class _Named:
    """A saved crawl opened read-only for one request: its connection and the settings it was crawled with."""

    def __init__(self, crawl: str):
        self.own = bool(crawl)
        if self.own:
            self.con = dbm.connect(_crawl_path(crawl), readonly=True)
            self.cfg = CrawlConfig.from_dict({**State.cfg.to_dict(), **(dbm.get_meta(self.con, "config", {}) or {})})
        else:
            self.con, self.cfg = State.read(), State.cfg

    def counts(self) -> dict[str, int]:
        return counts(self.con, self.cfg) if self.own else (State.counts() if State.db_path else {})

    def __enter__(self) -> "_Named":
        return self

    def __exit__(self, *exc) -> None:
        if self.own:
            self.con.close()


@app.get("/api/tabs")
def tabs(crawl: str = ""):
    if crawl:
        with _Named(crawl) as n:
            c, cfg = n.counts(), n.cfg
    else:
        c, cfg = (State.counts() if State.db_path else {}), State.cfg
    out = []
    for key, name in TABS:
        fl = [{"key": f["key"], "name": f["name"], "kind": f["kind"], "priority": f["priority"], "count": c.get(f["key"], 0),
               "desc": f["desc"], "fix": f["fix"], "issue_only": f["issue_only"], "fixKind": kind_for(f["key"])} for f in all_filters(cfg) if f["tab"] == key]
        out.append({"key": key, "name": name, "filters": fl})
    return {"tabs": out, "columns": {k: [{"field": col, "title": COL_LABELS.get(col, col)} for col in v] for k, v in COLUMNS.items()}}


@app.get("/api/issues")
def issues(crawl: str = ""):
    if crawl:
        with _Named(crawl) as n:
            c, cfg = n.counts(), n.cfg
    else:
        c, cfg = (State.counts() if State.db_path else {}), State.cfg
    names = dict(TABS)
    out = []
    for f in all_filters(cfg):
        if f["kind"] and c.get(f["key"], 0):
            out.append({"key": f["key"], "tab": names.get(f["tab"], f["tab"]), "name": f["name"], "kind": f["kind"], "priority": f["priority"],
                        "count": c[f["key"]], "desc": f["desc"], "fix": f["fix"]})
    order = {"High": 0, "Medium": 1, "Low": 2}
    out.sort(key=lambda x: (order.get(x["priority"], 3), -x["count"]))
    return {"issues": out}


@app.get("/favicon.ico")
def favicon():
    ico = UI_DIR / "icon.ico"
    if ico.exists():
        return FileResponse(str(ico), media_type="image/x-icon")
    return Response(status_code=204)


def _col_filters(raw: str) -> list[dict]:
    """?filters= is a JSON list of column filters from the grid header (see exports.column_filter_sql)."""
    if not raw:
        return []
    try:
        data = json.loads(raw)
    except json.JSONDecodeError:
        raise HTTPException(400, "Bad column filters") from None
    if not isinstance(data, list) or not all(isinstance(f, dict) for f in data):
        raise HTTPException(400, "Bad column filters")
    return data


@app.get("/api/urls")
def urls(key: str = "internal:all", page: int = 1, size: int = 200, sort: str = "", dir: str = "asc", q: str = "", filters: str = "", crawl: str = ""):
    if not crawl and (State.db_path is None or not State.db_path.exists()):
        return {"data": [], "last_page": 1, "total": 0, "columns": []}
    page, size = max(1, page), max(1, min(size, 1000))
    with _Named(crawl) as n:
        try:
            cols, rows, total = rows_for(n.con, n.cfg, key, q=q, sort=sort, direction=dir, offset=(page - 1) * size, limit=size,
                                         filters=_col_filters(filters))
        except ValueError as e:
            raise HTTPException(400, str(e)) from None
    data = [dict(zip(["id"] + cols, r)) for r in rows]
    return {"data": data, "last_page": max(1, -(-total // size)), "total": total, "columns": cols}


@app.get("/api/urls/values")
def url_values(key: str, field: str, q: str = "", filters: str = "", crawl: str = ""):
    if not crawl and (State.db_path is None or not State.db_path.exists()):
        return {"values": [], "truncated": False}
    from .exports import column_values
    with _Named(crawl) as n:
        try:
            return column_values(n.con, n.cfg, key, field, q=q, filters=_col_filters(filters))
        except ValueError as e:
            raise HTTPException(400, str(e)) from None


@app.get("/api/url/{url_id}")
def url_detail(url_id: int, crawl: str = ""):
    with _Named(crawl) as n:
        return _url_detail(n.con, n.cfg, url_id)


def _url_detail(con: sqlite3.Connection, cfg: CrawlConfig, url_id: int) -> dict[str, Any]:
    row = con.execute("SELECT * FROM urls WHERE id=?", (url_id,)).fetchone()
    if not row:
        raise HTTPException(404, "URL not found")
    d = {k: row[k] for k in row.keys()}
    for k in ("headers", "cookies", "hreflang", "headings", "custom", "js_errors_text", "js_blocked_text"):
        if d.get(k):
            try:
                d[k] = json.loads(d[k])
            except json.JSONDecodeError:
                pass
    links_sql = """SELECT l.id, l.kind, f.url AS source, t.url AS destination, l.anchor, l.alt, l.follow, l.target, l.rel, l.position, l.path,
                          t.status_code, t.status, t.indexability_status, t.size, t.type, l.hreflang, l.is_bg, l.has_size_attrs
                   FROM links l JOIN urls f ON f.id=l.from_id JOIN urls t ON t.id=l.to_id WHERE {w} ORDER BY l.id LIMIT 5000"""
    d["inlinks"] = [dict(r) for r in con.execute(links_sql.format(w="l.to_id=?"), (url_id,))]
    d["outlinks"] = [dict(r) for r in con.execute(links_sql.format(w="l.from_id=?"), (url_id,))]
    d["images"] = [o for o in d["outlinks"] if o["kind"] == "image"]
    d["resources"] = [o for o in d["outlinks"] if o["kind"] in ("css", "js", "iframe", "media")]
    d["issues"] = [r[0] for r in con.execute("SELECT key FROM url_issues WHERE url_id=?", (url_id,))]
    fk = filters_by_key(cfg)
    names = dict(TABS)
    d["issue_names"] = [f"{names.get(fk[k]['tab'], '')}: {fk[k]['name']}" for k in d["issues"] if k in fk and fk[k]["kind"]]
    d["uncrawlable"] = [dict(r) for r in con.execute("SELECT href, anchor, reason FROM uncrawlable WHERE from_id=? LIMIT 500", (url_id,))]
    from .neardup import duplicates_of
    dups = duplicates_of(con, url_id)
    d["exact_duplicates"], d["near_duplicates"] = dups["exact"], dups["near"]
    from .spelling import findings_for
    d["spelling"] = findings_for(con, url_id)
    from .lighthouse import result_for
    d["lighthouse"] = result_for(con, url_id)
    try:
        d["structured_data"] = [{"syntax": r["syntax"], "type": r["type"], "data": json.loads(r["data"]) if r["data"] and r["data"][:1] in "{[" else r["data"],
                                 "errors": json.loads(r["errors"] or "[]"), "warnings": json.loads(r["warnings"] or "[]")}
                                for r in con.execute("SELECT syntax, type, data, errors, warnings FROM structured_data WHERE url_id=? ORDER BY id", (url_id,))]
    except sqlite3.OperationalError:
        d["structured_data"] = []
    b = con.execute("SELECT screenshot IS NOT NULL AS shot, rendered IS NOT NULL AS rend FROM bodies WHERE url_id=?", (url_id,)).fetchone()
    d["has_screenshot"] = bool(b and b["shot"])
    d["has_rendered_html"] = bool(b and b["rend"])
    if row["in_sitemap"]:
        d["sitemaps"] = [r[0] for r in con.execute("SELECT sitemap FROM sitemap_urls WHERE url=?", (row["url"],))]
    return d


@app.get("/api/url/{url_id}/source")
def url_source(url_id: int, kind: str = "html", crawl: str = ""):
    with _Named(crawl) as n:
        return _url_source(n.con, url_id, kind)


def _url_source(con: sqlite3.Connection, url_id: int, kind: str):
    row = con.execute("SELECT html, text, rendered FROM bodies WHERE url_id=?", (url_id,)).fetchone()
    if not row:
        return Response("", media_type="text/plain")
    blob = {"text": row["text"], "rendered": row["rendered"]}.get(kind, row["html"])
    return Response(dbm.decompress(blob), media_type="text/plain; charset=utf-8")


@app.get("/api/url/{url_id}/screenshot")
def url_screenshot(url_id: int):
    con = State.read()
    row = con.execute("SELECT screenshot FROM bodies WHERE url_id=?", (url_id,)).fetchone()
    if not row or not row["screenshot"]:
        raise HTTPException(404, "No screenshot")
    return Response(row["screenshot"], media_type="image/jpeg")


# ----------------------------------------------------------------------------- exports / reports
@app.get("/api/export")
def export(key: str, format: str = "csv", q: str = "", filters: str = ""):
    con = State.read()
    try:
        name, data = export_filter(con, State.cfg, key, format, q=q, filters=_col_filters(filters))
    except ValueError as e:
        raise HTTPException(400, str(e)) from None
    return Response(data, media_type="application/octet-stream", headers={"Content-Disposition": f'attachment; filename="{name}"'})


@app.get("/api/reports")
def reports_list():
    return {"reports": [{"key": k, "name": n} for k, n in REPORT_NAMES]}


@app.get("/api/report/{name}")
def report_download(name: str, format: str = "csv"):
    con = State.read()
    try:
        sheets = report(con, State.cfg, name)
    except KeyError:
        raise HTTPException(404, "Unknown report")
    if format == "json":
        return {"sheets": [{"name": n, "headers": h, "rows": r[:2000]} for n, h, r in sheets]}
    if format == "xlsx":
        return Response(to_xlsx(sheets), media_type="application/octet-stream", headers={"Content-Disposition": f'attachment; filename="{name}.xlsx"'})
    n, h, r = sheets[0]
    return Response(to_csv(h, r), media_type="text/csv", headers={"Content-Disposition": f'attachment; filename="{name}.csv"'})


@app.get("/api/export/parts")
def export_parts():
    from .exports import BUNDLE_PARTS
    return {"parts": [{"key": k, "name": n, "desc": d} for k, n, d in BUNDLE_PARTS]}


@app.get("/api/export/bundle")
def export_bundle(parts: str = "overview,issues,issue_urls,issue_inlinks,matrix", format: str = "xlsx", all_filters_too: int = 0):
    """Every combination in one download: choose parts (comma list) and zip (CSV per sheet) or xlsx (one workbook)."""
    from .exports import build_bundle, bundle_to_xlsx, bundle_to_zip
    con = State.read()
    chosen = [p.strip() for p in parts.split(",") if p.strip()]
    sheets = build_bundle(con, State.cfg, chosen, issues_only_filters=not all_filters_too)
    if not sheets:
        raise HTTPException(400, "Nothing to export for the chosen parts")
    stem = f"spider-export_{(urlsplit(State.cfg.start_url).hostname or 'crawl')}_{datetime.now().strftime('%Y%m%d_%H%M')}"
    if format == "zip":
        readme = ("Content Pilot Spider export\n\nFolders: issues/ = affected URLs per issue, issue-inlinks/ = links pointing at them, "
                  "tabs/ = every tab, filters/ = every non-empty filter, links/, reports/. Top level: overview, issues overview, URL x issues matrix, AI fixes.\n")
        return Response(bundle_to_zip(sheets, readme), media_type="application/zip", headers={"Content-Disposition": f'attachment; filename="{stem}.zip"'})
    return Response(bundle_to_xlsx(sheets), media_type="application/octet-stream", headers={"Content-Disposition": f'attachment; filename="{stem}.xlsx"'})


@app.get("/api/export/issue")
def export_issue(key: str, what: str = "urls", format: str = "csv"):
    """One issue: its URLs, or the inlinks pointing at them, or both (xlsx)."""
    from .exports import LINK_HEADERS, _issue_inlink_rows
    con = State.read()
    f = filters_by_key(State.cfg).get(key)
    if not f:
        raise HTTPException(404, "Unknown filter")
    label = f"{dict(TABS).get(f['tab'], f['tab'])} - {f['name']}"
    sheets = []
    if what in ("urls", "both"):
        cols, rows, _ = rows_for(con, State.cfg, key)
        sheets.append((label, [COL_LABELS.get(x, x) for x in cols], [r[1:] for r in rows]))
    if what in ("inlinks", "both"):
        sheets.append((label + " (inlinks)", LINK_HEADERS, _issue_inlink_rows(con, key)))
    stem = key.replace(":", "_") + ("_inlinks" if what == "inlinks" else "_with_inlinks" if what == "both" else "")
    if format == "xlsx" or what == "both":
        return Response(to_xlsx(sheets), media_type="application/octet-stream", headers={"Content-Disposition": f'attachment; filename="{stem}.xlsx"'})
    n, h, r = sheets[0]
    return Response(to_csv(h, r), media_type="text/csv", headers={"Content-Disposition": f'attachment; filename="{stem}.csv"'})


@app.get("/api/export/issues.md")
def export_issues_md():
    from .exports import issues_markdown
    con = State.read()
    return Response(issues_markdown(con, State.cfg, State.cfg.start_url), media_type="text/markdown; charset=utf-8")


@app.get("/api/export/all")
def export_all():
    """One workbook: every tab filter with rows + overview + issues."""
    con = State.read()
    sheets = []
    for nm in ("crawl_overview", "issues_overview"):
        sheets.extend(report(con, State.cfg, nm))
    c = State.counts()
    for f in all_filters(State.cfg):
        if f["key"] in ("internal:all", "external:all") or (c.get(f["key"], 0) and f["kind"]):
            cols, rows, _ = rows_for(con, State.cfg, f["key"])
            sheets.append((f"{f['tab']}-{f['short']}"[:31], [COL_LABELS.get(x, x) for x in cols], [r[1:] for r in rows]))
    return Response(to_xlsx(sheets), media_type="application/octet-stream", headers={"Content-Disposition": 'attachment; filename="crawl_export.xlsx"'})


# ----------------------------------------------------------------------------- crawl files
@app.get("/api/crawls")
def crawls():
    out = []
    for p in sorted(CRAWLS_DIR.glob("*.db"), key=lambda x: x.stat().st_mtime, reverse=True):
        meta: dict[str, Any] = {}
        try:
            con = dbm.connect(p, readonly=True)
            meta = {r["key"]: json.loads(r["value"]) for r in con.execute("SELECT key, value FROM meta")}
            meta["urls"] = con.execute("SELECT count(*) FROM urls WHERE crawled IN (1,3)").fetchone()[0]
            con.close()
        except sqlite3.Error:
            pass
        out.append({"path": str(p), "name": p.name, "size": p.stat().st_size, "modified": datetime.fromtimestamp(p.stat().st_mtime).isoformat(timespec="seconds"),
                    "start_url": meta.get("start_url", ""), "state": meta.get("state", ""), "urls": meta.get("urls", 0), "started_at": meta.get("started_at", ""),
                    "open": State.db_path is not None and p == State.db_path})
    return {"crawls": out}


class PathBody(BaseModel):
    path: str


@app.post("/api/crawls/open")
def crawl_open(body: PathBody):
    if State.task and not State.task.done():
        raise HTTPException(409, "Stop the running crawl first")
    p = Path(body.path)
    if not p.exists() or p.parent.resolve() != CRAWLS_DIR.resolve():
        raise HTTPException(404, "Crawl file not found")
    con = dbm.connect(p, readonly=True)
    cfg = dbm.get_meta(con, "config", {})
    con.close()
    if cfg:
        # the crawl's own settings, but keep this machine's AI keys and connector targets
        keep = {k: getattr(State.cfg, k) for k in MACHINE_KEYS}
        State.cfg = CrawlConfig.from_dict({**cfg, **keep})
    State.db_path = p
    State.crawler = None
    State._counts_cache = None
    # filters may have been added since this crawl was analysed: refresh the materialised filter table
    from .issues import rebuild_issue_table
    wcon = State.read()
    rebuild_issue_table(wcon, State.cfg)
    wcon.commit()
    return {"ok": True}


@app.post("/api/crawls/delete")
def crawl_delete(body: PathBody):
    p = Path(body.path)
    if p.parent.resolve() != CRAWLS_DIR.resolve() or p.suffix.lower() != ".db":
        raise HTTPException(400, "Not a crawl file")
    if State.db_path == p:
        if State.task and not State.task.done():
            raise HTTPException(409, "Stop the running crawl first")
        if State._read is not None:
            State._read.close()
            State._read = None
        State.db_path = None
        State.crawler = None
    for suffix in ("", "-wal", "-shm"):
        q = Path(str(p) + suffix)
        if q.exists():
            q.unlink()
    return {"ok": True}


@app.post("/api/analysis/run")
def analysis_run():
    if State.task and not State.task.done():
        raise HTTPException(409, "Analysis runs automatically while crawling")
    from .analysis import run_analysis
    con = State.read()
    run_analysis(con, State.cfg, final=True)
    State._counts_cache = None
    return {"ok": True}


@app.get("/api/structure")
def structure(max_children: int = 40, max_depth: int = 6):
    """Folder hierarchy of crawled internal HTML pages: {name, path, pages, indexable, children[]}."""
    con = State.read()
    root: dict[str, Any] = {"name": "/", "path": "/", "pages": 0, "indexable": 0, "children": {}}
    for url, indexable in con.execute("SELECT url, indexable FROM urls WHERE crawled=1 AND is_internal=1 AND type='HTML'"):
        path = urlsplit(url).path or "/"
        segs = [s for s in path.split("/") if s]
        node = root
        node["pages"] += 1
        node["indexable"] += int(bool(indexable))
        for i, seg in enumerate(segs[:max_depth]):
            if i == len(segs) - 1 and not path.endswith("/"):
                break   # last segment is the page itself, not a folder
            child = node["children"].get(seg)
            if child is None:
                child = node["children"][seg] = {"name": seg, "path": node["path"].rstrip("/") + "/" + seg + "/", "pages": 0, "indexable": 0, "children": {}}
            child["pages"] += 1
            child["indexable"] += int(bool(indexable))
            node = child

    def pack(n: dict[str, Any]) -> dict[str, Any]:
        kids = sorted(n["children"].values(), key=lambda c: (-c["pages"], c["name"]))
        more = max(0, len(kids) - max_children)
        return {"name": n["name"], "path": n["path"], "pages": n["pages"], "indexable": n["indexable"],
                "children": [pack(c) for c in kids[:max_children]], "more": more}
    return pack(root)


@app.get("/api/overview")
def overview():
    con = State.read()
    c = State.counts()
    q = lambda sql: con.execute(sql).fetchone()[0]  # noqa: E731
    return {
        "total": q("SELECT count(*) FROM urls WHERE crawled IN (1,3)"),
        "internal": c.get("internal:all", 0), "external": c.get("external:all", 0),
        "indexable": q("SELECT count(*) FROM urls WHERE crawled=1 AND is_internal=1 AND indexable=1"),
        "non_indexable": q("SELECT count(*) FROM urls WHERE crawled IN (1,3) AND is_internal=1 AND indexable=0"),
        "response_times": [list(r) for r in con.execute(
            "SELECT CASE WHEN response_time<0.5 THEN '0-0.5s' WHEN response_time<1 THEN '0.5-1s' WHEN response_time<2 THEN '1-2s' WHEN response_time<5 THEN '2-5s' ELSE '5s+' END AS b, count(*) FROM urls WHERE crawled=1 AND response_time IS NOT NULL GROUP BY b")],
        "depth": [list(r) for r in con.execute("SELECT depth, count(*) FROM urls WHERE crawled=1 AND is_internal=1 AND type='HTML' GROUP BY depth ORDER BY depth")],
        "status": [list(r) for r in con.execute("SELECT status_code, count(*) FROM urls WHERE crawled=1 GROUP BY status_code ORDER BY status_code")],
    }


# ----------------------------------------------------------------------------- spelling & grammar
class WordBody(BaseModel):
    word: str


@app.post("/api/spelling/ignore")
def spelling_ignore(body: WordBody):
    """Add a word to the dictionary (Config > Spelling & grammar > ignore list) and re-run the check on the open crawl."""
    w = body.word.strip().lower()
    if not w:
        raise HTTPException(400, "Empty word")
    if w not in [x.lower() for x in State.cfg.spell_ignore]:
        State.cfg.spell_ignore = [*State.cfg.spell_ignore, w]
        save_settings(State.cfg)
    return spelling_run()


@app.post("/api/spelling/run")
def spelling_run():
    """Re-run spelling & grammar on the open crawl with the current settings (fast: text is already stored)."""
    if State.task and not State.task.done():
        raise HTTPException(409, "Wait for the crawl to finish")
    from .spelling import compute
    from .issues import rebuild_issue_table
    con = State.read()
    out = compute(con, State.cfg, log=lambda *_: None)
    rebuild_issue_table(con, State.cfg)
    con.commit()
    State._counts_cache = None
    return {"ok": True, **out, "ignored": len(State.cfg.spell_ignore)}


# ----------------------------------------------------------------------------- site audit dashboard
@app.get("/api/audit")
def audit_overview(crawl: str = ""):
    """The open crawl's audit, or a named crawl file's (`crawl=`), read-only, for the app's hosted Site audit."""
    from .audit import site_audit
    if not crawl:
        return site_audit(State.read(), State.cfg)
    con = dbm.connect(_crawl_path(crawl), readonly=True)
    try:
        return site_audit(con, CrawlConfig.from_dict({**State.cfg.to_dict(), **(dbm.get_meta(con, "config", {}) or {})}))
    finally:
        con.close()


@app.get("/api/audit/history")
def audit_history(crawl: str = ""):
    from .audit import history
    return {"points": history(CRAWLS_DIR, _crawl_path(crawl) if crawl else State.db_path, State.cfg)}


@app.get("/api/audit/report.md")
def audit_report_md():
    from .audit import report_markdown, site_audit
    md = report_markdown(site_audit(State.read(), State.cfg))
    return Response(md, media_type="text/markdown; charset=utf-8", headers={"Content-Disposition": 'attachment; filename="site-audit.md"'})


# Content Pilot (contentpilot.cloud) checks whether this local app is running and deep-links into it. Browsers require
# CORS and, from a public https page to 127.0.0.1, the Private Network Access preflight header.
LAUNCHER_ORIGINS = {"https://contentpilot.cloud", "https://www.contentpilot.cloud", "https://app.contentpilot.cloud", "http://localhost:3000", "http://127.0.0.1:3000"}


@app.middleware("http")
async def _launcher_cors(request: Request, call_next):
    origin = request.headers.get("origin", "")
    allowed = origin in LAUNCHER_ORIGINS or (origin.startswith("https://") and origin.endswith(".contentpilot.cloud")) or bool(re.match(r"^https?://(localhost|127\.0\.0\.1)(:\d+)?$", origin))   # any local dev port
    launcher_path = request.url.path in ("/api/system/check", "/api/audit", "/api/audit/history", "/api/crawls")   # read-only, for the launcher card
    if request.method == "OPTIONS" and allowed and launcher_path:
        return Response(status_code=204, headers={"Access-Control-Allow-Origin": origin, "Access-Control-Allow-Methods": "GET, OPTIONS", "Access-Control-Allow-Headers": "*",
                                                  "Access-Control-Allow-Credentials": "true", "Access-Control-Allow-Private-Network": "true", "Access-Control-Max-Age": "600", "Vary": "Origin"})
    response = await call_next(request)
    if allowed and launcher_path and request.method == "GET":
        response.headers["Access-Control-Allow-Origin"] = origin
        response.headers["Access-Control-Allow-Credentials"] = "true"   # the hosted address sits behind Cloudflare Access, whose session cookie travels with the request
        response.headers["Access-Control-Allow-Private-Network"] = "true"
        response.headers["Vary"] = "Origin"
    return response


# ----------------------------------------------------------------------------- schedules
@app.get("/api/schedules")
def schedules_list():
    from . import schedule as sch
    return {"schedules": sch.list_schedules(), "due": [s["id"] for s in sch.due()], "running": State.task is not None and not State.task.done()}


@app.post("/api/schedules")
def schedules_upsert(body: dict[str, Any]):
    from . import schedule as sch
    try:
        s = sch.upsert(body)
    except ValueError as exc:
        raise HTTPException(400, str(exc))
    s["next_run"] = sch.next_run(s).isoformat(timespec="minutes")
    return {"ok": True, "schedule": s}


@app.delete("/api/schedules/{sid}")
def schedules_delete(sid: str):
    from . import schedule as sch
    if not sch.remove(sid):
        raise HTTPException(404, "No such schedule")
    return {"ok": True}


@app.post("/api/schedules/{sid}/run")
async def schedules_run(sid: str):
    """Run one schedule now (in the background; progress via /api/crawl/status and the schedule's history)."""
    from . import schedule as sch
    s = sch.get(sid)
    if not s:
        raise HTTPException(404, "No such schedule")
    if State.task and not State.task.done():
        raise HTTPException(409, "A crawl is already running")

    async def go():
        async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url="http://127.0.0.1", timeout=httpx.Timeout(900.0)) as client:
            await sch.run_schedule(s, client, print)
    asyncio.create_task(go())
    return {"ok": True, "started": s["name"]}


# ----------------------------------------------------------------------------- Google Search Console + GA4
def _redirect_uri(request: Request) -> str:
    return str(request.base_url).rstrip("/") + "/api/google/callback"


@app.get("/api/google/auth-url")
def google_auth_url(request: Request):
    from .google import GoogleError, auth_url
    try:
        return {"url": auth_url(State.cfg, _redirect_uri(request)), "redirect_uri": _redirect_uri(request)}
    except GoogleError as exc:
        raise HTTPException(400, str(exc))


@app.get("/api/google/callback", response_class=HTMLResponse)
async def google_callback(request: Request, code: str = "", state: str = "", error: str = ""):
    from .google import GoogleError, exchange_code, valid_state
    page = lambda title, body, safe=False: f"<!doctype html><html><body style='font-family:IBM Plex Sans,Segoe UI,sans-serif;padding:40px;color:#15171a'><h2>{html.escape(str(title))}</h2><p>{body if safe else html.escape(str(body))}</p><p>You can close this tab and return to Content Pilot Spider.</p></body></html>"  # noqa: E731
    if error or not code:
        return HTMLResponse(page("Google sign-in cancelled", error or "No authorisation code received."), status_code=400)
    if not valid_state(state):
        return HTMLResponse(page("Sign-in expired", "Start again from Config → Search Console & GA4."), status_code=400)
    try:
        tok = await exchange_code(State.cfg, code, _redirect_uri(request))
    except GoogleError as exc:
        return HTMLResponse(page("Google sign-in failed", str(exc)), status_code=400)
    if tok.get("refresh_token"):
        State.cfg.google_refresh_token = tok["refresh_token"]
    State.cfg.google_account = tok.get("email", "") or State.cfg.google_account or "connected"
    save_settings(State.cfg)
    return HTMLResponse(page("Connected to Google", f"Signed in as <b>{html.escape(State.cfg.google_account)}</b>. Search Console and GA4 properties will now appear in the app.", True))


@app.get("/api/google/status")
def google_status():
    return {"configured": bool(State.cfg.google_client_id.strip() and State.cfg.google_client_secret.strip()), "connected": bool(State.cfg.google_refresh_token.strip()),
            "account": State.cfg.google_account, "gsc_property": State.cfg.gsc_property, "ga4_property": State.cfg.ga4_property,
            "job": State.g_job.status() if State.g_job else {"state": "idle"},
            "gsc": dbm.get_meta(State.read(), "gsc", None) if State.db_path and State.db_path.exists() else None,
            "ga4": dbm.get_meta(State.read(), "ga4", None) if State.db_path and State.db_path.exists() else None}


@app.get("/api/google/properties")
async def google_properties():
    """Search Console sites and GA4 properties the connected account can read, plus the auto-matched site for the open crawl."""
    from .google import GoogleError, access_token, ga4_properties, gsc_sites, pick_gsc_site
    try:
        token = await access_token(State.cfg)
        sites = await gsc_sites(State.cfg, token)
        try:
            props = await ga4_properties(State.cfg, token)
        except GoogleError as exc:
            props = []
            ga_err = str(exc)
        else:
            ga_err = ""
    except GoogleError as exc:
        raise HTTPException(400, str(exc))
    return {"gsc_sites": sites, "ga4_properties": props, "suggested_gsc": pick_gsc_site(sites, State.cfg.start_url), "ga4_error": ga_err}


class GoogleRunBody(BaseModel):
    gsc: bool = True
    ga4: bool = True
    inspect: int = -1       # -1 = use the configured limit


@app.post("/api/google/run")
async def google_run_now(body: GoogleRunBody):
    from .google import GoogleJob, run_job as google_run
    if not State.cfg.google_refresh_token.strip():
        raise HTTPException(400, "Connect a Google account first (Config → Search Console & GA4).")
    if State.g_task and not State.g_task.done():
        raise HTTPException(409, "A Search Console / GA4 fetch is already running")
    con = State.read()
    State.g_job = GoogleJob()
    n = State.cfg.gsc_inspect_max if body.inspect < 0 else body.inspect
    State.g_task = asyncio.create_task(google_run(con, State.cfg, State.g_job, body.gsc, body.ga4, n))
    State._counts_cache = None
    return {"ok": True, "inspect": n}


@app.post("/api/google/disconnect")
def google_disconnect():
    State.cfg.google_refresh_token = ""
    State.cfg.google_account = ""
    save_settings(State.cfg)
    return {"ok": True}


# ----------------------------------------------------------------------------- link metrics (Ahrefs / Moz / Majestic)
class LinkMetricsBody(BaseModel):
    ids: list[int] = []
    key: str = ""
    sample: int = 0


@app.post("/api/linkmetrics/run")
async def linkmetrics_run(body: LinkMetricsBody):
    from .linkmetrics import LinkMetricsJob, candidates, providers, run_job as lm_run
    if not providers(State.cfg):
        raise HTTPException(400, "Add an Ahrefs, Moz or Majestic API key in Config → Link metrics first.")
    if State.lm_task and not State.lm_task.done():
        raise HTTPException(409, "A link-metrics fetch is already running")
    con = State.read()
    cap = max(1, State.cfg.linkmetrics_max_urls)
    if body.ids:
        ids = list(dict.fromkeys(body.ids))[:cap]
    elif body.key:
        ids = [r[0] for r in con.execute("SELECT u.id FROM urls u WHERE u.id IN (SELECT url_id FROM url_issues WHERE key=?) AND u.type='HTML' ORDER BY u.inlinks DESC LIMIT ?", (body.key, cap))]
    else:
        ids = [i for i, _ in candidates(con, min(body.sample or cap, cap))]
    if not ids:
        raise HTTPException(400, "No HTML pages to look up")
    State.lm_job = LinkMetricsJob()
    State.lm_task = asyncio.create_task(lm_run(con, State.cfg, ids, State.lm_job))
    State._counts_cache = None
    return {"ok": True, "urls": len(ids), "providers": providers(State.cfg)}


@app.get("/api/linkmetrics/status")
def linkmetrics_status():
    from .linkmetrics import providers
    s = State.lm_job.status() if State.lm_job else {"state": "idle", "total": 0, "done": 0, "errors": [], "error_count": 0, "providers": [], "elapsed": 0}
    s["configured"] = providers(State.cfg)
    if State.db_path and State.db_path.exists():
        try:
            s["fetched"] = State.read().execute("SELECT count(*) FROM urls WHERE lm_at IS NOT NULL").fetchone()[0]
        except sqlite3.OperationalError:
            s["fetched"] = 0
    if s["state"] in ("done", "cancelled", "error"):
        State._counts_cache = None
    return s


# ----------------------------------------------------------------------------- Lighthouse / PageSpeed
class LighthouseBody(BaseModel):
    ids: list[int] = []
    key: str = ""
    sample: int = 0
    max_pages: int = 50


@app.post("/api/lighthouse/run")
async def lighthouse_run(body: LighthouseBody):
    """Audit URLs with Lighthouse: explicit ids, every URL in a filter (capped by max_pages), or the top `sample` pages."""
    from .lighthouse import LighthouseJob, available, run_job, sample_ids
    if State.lh_task and not State.lh_task.done():
        raise HTTPException(409, "A Lighthouse run is already in progress")
    con = State.read()
    if body.ids:
        ids = list(dict.fromkeys(body.ids))
    elif body.key:
        ids = [r[0] for r in con.execute("SELECT u.id FROM urls u WHERE u.id IN (SELECT url_id FROM url_issues WHERE key=?) AND u.type='HTML' ORDER BY u.inlinks DESC LIMIT ?", (body.key, max(1, min(500, body.max_pages))))]
    else:
        ids = sample_ids(con, State.cfg, body.sample or State.cfg.lighthouse_max_pages)
    if not ids:
        raise HTTPException(400, "No HTML pages to audit")
    if not (State.cfg.psi_api_key or "").strip():
        av = available()
        if not av["node"] or not av["lighthouse"]:
            raise HTTPException(400, "Local Lighthouse needs Node.js and the vendored lighthouse package (cd ui\\vendor && npm install), or add a PageSpeed Insights API key in Config → PageSpeed.")
    State.lh_job = LighthouseJob()
    State.lh_task = asyncio.create_task(run_job(con, State.cfg, ids, State.lh_job))
    State._counts_cache = None
    return {"ok": True, "pages": len(ids), "source": "psi" if (State.cfg.psi_api_key or "").strip() else "local", "device": State.cfg.lighthouse_device}


@app.get("/api/lighthouse/status")
def lighthouse_status():
    s = State.lh_job.status() if State.lh_job else {"state": "idle", "total": 0, "done": 0, "ok": 0, "errors": [], "error_count": 0, "current": [], "elapsed": 0}
    if State.db_path and State.db_path.exists():
        try:
            s["audited"] = State.read().execute("SELECT count(*) FROM urls WHERE lh_at IS NOT NULL").fetchone()[0]
        except sqlite3.OperationalError:
            s["audited"] = 0
    if s["state"] in ("done", "cancelled", "error"):
        State._counts_cache = None
    return s


@app.post("/api/lighthouse/cancel")
def lighthouse_cancel():
    if State.lh_job:
        State.lh_job.cancel()
    return {"ok": True}


# ----------------------------------------------------------------------------- visualisations
@app.get("/api/viz/tree")
def viz_tree(mode: str = "crawl", max_nodes: int = 1000):
    """Hierarchy for the tree / force-directed diagrams: crawl (shortest click path from the start page) or directory (URL folders)."""
    from .viz import crawl_tree, directory_tree
    con = State.read()
    n = max(50, min(5000, max_nodes))
    return crawl_tree(con, State.cfg, n) if mode == "crawl" else directory_tree(con, n)


@app.get("/api/viz/words")
def viz_words(kind: str = "anchor", limit: int = 150):
    from .viz import word_frequencies
    return word_frequencies(State.read(), "anchor" if kind == "anchor" else "body", limit)


# ----------------------------------------------------------------------------- XML sitemap generation
def _sitemap_opts(request: Request):
    from .sitemapgen import SitemapOptions
    return SitemapOptions.from_dict(dict(request.query_params))


@app.get("/api/sitemap/preview")
def sitemap_preview(request: Request):
    """How many URLs the generated sitemap would contain with these options, and what gets excluded."""
    from .sitemapgen import select
    con = State.read()
    rows, excluded = select(con, _sitemap_opts(request))
    return {"urls": len(rows), "excluded": excluded, "files": max(1, -(-len(rows) // 50000)), "sample": [r["url"] for r in rows[:8]]}


@app.get("/api/sitemap/generate")
def sitemap_generate(request: Request):
    """Download sitemap.xml, or a zip with sitemap1.xml… + sitemap_index.xml when it has to be split."""
    from .sitemapgen import build, to_zip
    con = State.read()
    root = f"{urlsplit(State.cfg.start_url).scheme}://{urlsplit(State.cfg.start_url).netloc}" if State.cfg.start_url else ""
    files, summary = build(con, _sitemap_opts(request), root)
    host = (urlsplit(State.cfg.start_url).hostname or "site").replace(":", "_")
    if len(files) == 1:
        return Response(files[0][1], media_type="application/xml", headers={"Content-Disposition": f'attachment; filename="sitemap_{host}.xml"', "X-Sitemap-URLs": str(summary["urls"])})
    return Response(to_zip(files), media_type="application/zip", headers={"Content-Disposition": f'attachment; filename="sitemaps_{host}.zip"', "X-Sitemap-URLs": str(summary["urls"])})


# ----------------------------------------------------------------------------- usage
@app.get("/api/usage")
def usage_stats():
    from . import usage as usage_log
    return usage_log.summary()


class PricesBody(BaseModel):
    prices: dict[str, Any]


@app.post("/api/usage/prices")
def usage_prices(body: PricesBody):
    """Set USD-per-million prices: {"provider:model": [input, output]}; null/empty removes an override."""
    from . import usage as usage_log
    try:
        return {"ok": True, "prices": usage_log.set_prices(body.prices)}
    except (TypeError, ValueError) as exc:
        raise HTTPException(400, f"Prices must be numbers: {exc}")


# ----------------------------------------------------------------------------- compare (change detection)
def _crawl_path(name: str) -> Path:
    p = CRAWLS_DIR / Path(name).name
    if not name or not p.exists():
        raise HTTPException(404, f"Crawl file not found: {name or '(none)'}")
    return p


def _compare_pair(a: str, b: str) -> tuple[Path, Path]:
    """b defaults to the open crawl, a to the previous completed crawl of the same site."""
    from .compare import previous_crawl
    pb = _crawl_path(b) if b else State.db_path
    if pb is None or not pb.exists():
        raise HTTPException(404, "No crawl open: pass b=<crawl file>")
    if a:
        pa = _crawl_path(a)
    else:
        pa = previous_crawl(pb, CRAWLS_DIR)
        if pa is None:
            raise HTTPException(404, f"No earlier completed crawl of {urlsplit(dbm.get_meta(dbm.connect(pb, readonly=True), 'start_url', '')).hostname or pb.stem} to compare with")
    if pa.resolve() == pb.resolve():
        raise HTTPException(400, "Pick two different crawls")
    return pa, pb


@app.get("/api/compare")
def compare_crawls(a: str = "", b: str = "", limit: int = 500):
    """Change detection A (before) -> B (after). Lists are trimmed to `limit` rows each; totals are in summary."""
    from .compare import FIELD_LABELS, compare
    pa, pb = _compare_pair(a, b)
    r = compare(pa, pb, State.cfg)
    lim = max(1, min(5000, limit))
    return {"a": r["a"], "b": r["b"], "summary": r["summary"], "field_labels": FIELD_LABELS, "issues": r["issues"],
            "added": r["added"][:lim], "removed": r["removed"][:lim], "changed": r["changed"][:lim]}


@app.get("/api/compare/export")
def compare_export(a: str = "", b: str = ""):
    from .compare import compare, to_sheets
    pa, pb = _compare_pair(a, b)
    r = compare(pa, pb, State.cfg)
    stem = f"compare_{pa.stem}_vs_{pb.stem}"
    return Response(to_xlsx(to_sheets(r)), media_type="application/octet-stream", headers={"Content-Disposition": f'attachment; filename="{stem}.xlsx"'})



# ----------------------------------------------------------------------------- hosted mode: API key on every route
# When SPIDER_API_KEY is set (the hosted Spider, docs/SPIDER-HOSTING.md in the app repo) every path, the UI and /mcp
# included, needs the same value in the X-Spider-Key header. Two kinds of caller are trusted without it: the app's own
# in-process clients (the scheduler and MCP call it through httpx.ASGITransport) and a browser on the box itself,
# reached through an SSH port forward. Both arrive from a loopback address with no forwarding header; Caddy always adds
# X-Forwarded-For, and uvicorn then reports the real client address, so nothing from outside looks local. Declared after
# the other middlewares so it runs first.
#
# The browser sign-in (1 Nov 2026): the Content Pilot app embeds this UI on its Site audit page for its admins. Its
# server mints `<uid, base64url>.<expiry, unix s>.<HMAC-SHA256, base64url>` with a key derived from SPIDER_API_KEY
# (ui_token_key), valid for an hour at most; the frame opens `/?token=...`, which sets it as the cp_spider_ui cookie
# (HttpOnly, SameSite=Lax; app.contentpilot.cloud and this host are the same site) and redirects to `/` so the token
# leaves the address bar. The cookie then stands in for the key on every path except /mcp. A write (anything but
# GET/HEAD/OPTIONS) signed in by cookie must also carry an Origin of this host, so a page on another subdomain cannot
# post with it. The app refreshes the cookie through `/auth/ui?token=...` (204) before it runs out.
_FORWARD_HEADERS = ("x-forwarded-for", "forwarded", "x-real-ip", "cf-connecting-ip")
UI_COOKIE = "cp_spider_ui"
UI_TOKEN_MAX_S = 3600 + 120          # one hour, plus two minutes of clock difference between the Worker and this box
_SAFE_METHODS = ("GET", "HEAD", "OPTIONS")


def _b64u(b: bytes) -> str:
    return base64.urlsafe_b64encode(b).rstrip(b"=").decode()


def ui_token_key(api_key: str) -> bytes:
    """The signing key for UI sign-ins: never the API key itself, so a token reveals nothing about it."""
    return hmac.new(api_key.encode(), b"content-pilot spider ui token v1", hashlib.sha256).digest()


def make_ui_token(api_key: str, uid: str, exp: int) -> str:
    """Same format as the app's src/app/api/v1/spider/ui-token/route.ts; used by the tests."""
    body = f"{_b64u(uid.encode())}.{int(exp)}"
    return f"{body}.{_b64u(hmac.new(ui_token_key(api_key), body.encode(), hashlib.sha256).digest())}"


def ui_token_exp(token: str, now: float | None = None) -> int:
    """The token's expiry when it is well formed, signed with this Spider's key and not run out; else 0."""
    key = os.environ.get("SPIDER_API_KEY", "")
    if not key or not token or len(token) > 400:
        return 0
    parts = token.split(".")
    if len(parts) != 3 or not parts[1].isdigit():
        return 0
    exp = int(parts[1])
    now = time.time() if now is None else now
    if exp <= now or exp > now + UI_TOKEN_MAX_S:
        return 0
    want = _b64u(hmac.new(ui_token_key(key), f"{parts[0]}.{parts[1]}".encode(), hashlib.sha256).digest())
    return exp if hmac.compare_digest(parts[2].encode(), want.encode()) else 0


def _is_mcp(path: str) -> bool:
    return path == "/mcp" or path.startswith("/mcp/")


def _key_ok(request: Request) -> bool:
    key = os.environ.get("SPIDER_API_KEY", "")
    if not key:
        return True
    if hmac.compare_digest(request.headers.get("x-spider-key", "").encode(), key.encode()):
        return True
    host = request.client.host if request.client else ""
    return host in ("127.0.0.1", "::1") and not any(h in request.headers for h in _FORWARD_HEADERS)


def _cookie_ok(request: Request) -> bool:
    if _is_mcp(request.url.path) or not ui_token_exp(request.cookies.get(UI_COOKIE, "")):
        return False
    if request.method in _SAFE_METHODS:
        return True
    origin = request.headers.get("origin", "")
    return bool(origin) and urlsplit(origin).netloc.lower() == request.headers.get("host", "").lower()


def _set_ui_cookie(resp: Response, request: Request, token: str, exp: int) -> Response:
    resp.set_cookie(UI_COOKIE, token, max_age=max(1, int(exp - time.time())), path="/", httponly=True, samesite="lax",
                    secure=request.url.scheme == "https")
    resp.headers["Cache-Control"] = "no-store"
    return resp


_SIGN_IN_PAGE = ("<!doctype html><meta charset=utf-8><title>Content Pilot Spider</title>"
                 "<body style=\"font:14px system-ui,sans-serif;margin:40px;color:#222\">"
                 "<p><b>Sign-in needed.</b> Open the Spider from Content Pilot: Site audit, then the Spider view.</p>"
                 "<p style=\"color:#666\">{why}</p></body>")


@app.middleware("http")
async def _api_key_gate(request: Request, call_next):
    if os.environ.get("SPIDER_API_KEY") and request.method == "GET" and request.url.path in ("/", "/auth/ui") and "token" in request.query_params:
        token = request.query_params.get("token", "")
        exp = ui_token_exp(token)
        if not exp:
            why = "The sign-in link has run out or is not valid; open the Spider view again."
            if request.url.path == "/":
                return HTMLResponse(_SIGN_IN_PAGE.format(why=why), status_code=401, headers={"Cache-Control": "no-store"})
            return JSONResponse({"detail": why}, status_code=401)
        resp = RedirectResponse("/", status_code=303) if request.url.path == "/" else Response(status_code=204)
        return _set_ui_cookie(resp, request, token, exp)
    if not _key_ok(request) and not _cookie_ok(request):
        if request.method == "GET" and request.url.path == "/":
            return HTMLResponse(_SIGN_IN_PAGE.format(why="This address needs a sign-in from the app or the X-Spider-Key header."),
                                status_code=401, headers={"Cache-Control": "no-store"})
        return JSONResponse({"detail": "X-Spider-Key header required"}, status_code=401)
    return await call_next(request)


_LOCAL_HOSTS = {"127.0.0.1", "localhost", "[::1]"}


def _extra_hosts() -> set[str]:
    """SPIDER_ALLOWED_HOSTS: extra host names this desktop app answers to, comma separated (a tunnel's hostname)."""
    return {h.strip().lower() for h in os.environ.get("SPIDER_ALLOWED_HOSTS", "").split(",") if h.strip()}


def _host_ok(host_header: str) -> bool:
    h = (host_header or "").strip().lower()
    name = h if h.endswith("]") or ":" not in h else h.rsplit(":", 1)[0]
    return name in _LOCAL_HOSTS or name in _extra_hosts() or h in _extra_hosts()


@app.middleware("http")
async def _local_origin_guard(request: Request, call_next):
    """Desktop mode (no SPIDER_API_KEY): the app has no sign-in, so a web page open in the user's browser must not be
    able to reach it. A request whose Host is not this machine is refused (DNS rebinding), and so is a write that comes
    from another origin (cross-site form posts and fetches). The hosted Spider has its key gate instead."""
    desktop = not os.environ.get("SPIDER_API_KEY")
    if desktop:
        host = request.headers.get("host", "")
        if not _host_ok(host):
            return JSONResponse({"detail": "This address is not the local Spider (set SPIDER_ALLOWED_HOSTS to allow another host name)"}, status_code=403)
        if request.method not in _SAFE_METHODS:
            origin = request.headers.get("origin", "")
            cross = urlsplit(origin).netloc.lower() != host.strip().lower() if origin else request.headers.get("sec-fetch-site", "") in ("cross-site", "same-site")
            if cross:
                return JSONResponse({"detail": "Requests from another site are not accepted"}, status_code=403)
    response = await call_next(request)
    response.headers.setdefault("X-Content-Type-Options", "nosniff")
    if desktop and not _extra_hosts():   # the hosted and tunnelled Spider is framed by the Content Pilot app
        response.headers.setdefault("X-Frame-Options", "DENY")
        response.headers.setdefault("Content-Security-Policy", "frame-ancestors 'none'")
    return response


# ----------------------------------------------------------------------------- internal links (read by the Content Pilot app)
# The app's Internal links module reads the crawl through these three endpoints: the link graph, where phrases are
# mentioned in page text, and a one-page re-fetch that verifies a pushed link went live. They answer for the open crawl,
# or for a named crawl file (`crawl=<file name>`), and when SPIDER_API_KEY is set in the environment they require the
# same value in the X-Spider-Key header (the hosted Spider sits behind that; the desktop app has no key and answers
# 127.0.0.1 only).
from . import links as linksmod


def _links_auth(request: Request) -> None:   # the gate above already covers these; kept so the endpoints stay safe on their own
    if not _key_ok(request) and not _cookie_ok(request):
        raise HTTPException(401, "X-Spider-Key header required")


def _links_con(crawl: str) -> tuple[sqlite3.Connection, bool]:
    if crawl:
        return dbm.connect(_crawl_path(crawl), readonly=True), True
    return State.read(), False


@app.get("/api/graph")
def links_graph(request: Request, crawl: str = "", page: int = 1, size: int = 1000):
    _links_auth(request)
    con, own = _links_con(crawl)
    try:
        return linksmod.graph(con, page, size)
    finally:
        if own:
            con.close()



@app.get("/api/prominent")
async def links_prominent(request: Request, crawl: str = "", min_occ: int = 4, top: int = 100):
    """Prominent terms per indexable page (spider/prominent.py): what each page is about, for link targets."""
    from . import prominent as prominentmod
    _links_auth(request)
    con, own = _links_con(crawl)
    try:
        return await asyncio.to_thread(prominentmod.prominent, con, max(1, min_occ), max(1, min(top, 300)))
    finally:
        if own:
            con.close()

class MentionsBody(BaseModel):
    crawl: str = ""
    phrases: list[dict[str, Any]]
    exclude_pairs: list[list[int]] = []
    per_phrase: int = 50
    min_words: int = 150


@app.post("/api/mentions")
async def links_mentions(request: Request, body: MentionsBody):
    _links_auth(request)
    if not body.phrases:
        return {"scanned": 0, "mentions": []}
    if len(body.phrases) > 500:
        raise HTTPException(400, "At most 500 phrases per request")
    con, own = _links_con(body.crawl)
    try:
        pairs = [(int(p[0]), int(p[1])) for p in body.exclude_pairs if len(p) == 2]
        return await asyncio.to_thread(linksmod.mentions, con, body.phrases, pairs, max(1, min(body.per_phrase, 200)), body.min_words)
    finally:
        if own:
            con.close()


class CrawlUrlBody(BaseModel):
    url: str
    expect: list[dict[str, str]] = []     # [{"href": "/website-crawler", "anchor": "website crawler"}]


@app.post("/api/crawl/url")
async def links_crawl_url(request: Request, body: CrawlUrlBody):
    """Fetch one page now (no crawl needed) and report its status and hyperlinks, plus whether each expected link is
    there: the href resolved the same way the crawler resolves it, and the anchor matched word for word."""
    _links_auth(request)
    from .fetch import fetch, make_client
    from .parse import parse_html
    from .urlnorm import normalize
    url = normalize(body.url)
    if not url:
        raise HTTPException(400, "A valid http(s) URL is required")
    from .netguard import refuse_reason
    why = await refuse_reason(url)
    if why:
        raise HTTPException(400, why)
    cfg = State.cfg
    client = make_client(cfg)
    try:
        res = await fetch(client, url, cfg, want_body=True, max_bytes=8 * 1024 * 1024)
    finally:
        await client.aclose()
    out: dict[str, Any] = {"url": url, "status_code": res.status_code, "error": res.error, "fetched_at": datetime.now().isoformat(timespec="seconds"), "hyperlinks": [], "expect": []}
    if res.error or not res.status_code:
        out["expect"] = [{**e, "found": False} for e in body.expect]
        return out
    pd = parse_html(res.text, url, res.headers, cfg, status_code=res.status_code)
    out["word_count"] = pd.fields.get("word_count")
    out["title"] = pd.fields.get("title")
    links = [{"to": lk.to_url, "anchor": lk.anchor or "", "position": lk.position, "follow": int(lk.follow)} for lk in pd.links if lk.kind == "hyperlink"]
    out["hyperlinks"] = links
    for e in body.expect:
        want = normalize(e.get("href", ""), url) or ""
        need = linksmod.phrase_tokens(e.get("anchor", ""))
        found = any((lk["to"] or "").rstrip("/") == want.rstrip("/") and (not need or linksmod.tokens(lk["anchor"]) == need or (need and all(t in linksmod.tokens(lk["anchor"]) for t in need))) for lk in links)
        out["expect"].append({"href": e.get("href", ""), "anchor": e.get("anchor", ""), "found": found})
    return out

# ----------------------------------------------------------------------------- MCP (Model Context Protocol)
class _McpGate:
    """ASGI wrapper around the MCP endpoint so Config > MCP can switch it off without restarting."""
    def __init__(self, inner):
        self.inner = inner

    async def __call__(self, scope, receive, send):
        if not State.cfg.mcp_enabled:
            await JSONResponse({"detail": "The MCP server is turned off. Enable it in Config > MCP."}, status_code=404)(scope, receive, send)
            return
        await self.inner(scope, receive, send)


def _mcp_snippets(http_url: str) -> dict[str, str]:
    py = str(Path(sys.executable).with_name("python.exe" if os.name == "nt" else "python"))
    script = str(ROOT / "mcp_server.py")
    name = "content-pilot-spider"
    stdio = {"mcpServers": {name: {"command": py, "args": [script]}}}
    http = {"mcpServers": {name: {"url": http_url}}}
    return {
        "claude_code_http": f"claude mcp add --transport http {name} {http_url}",
        "claude_code_stdio": f'claude mcp add {name} -- "{py}" "{script}"',
        "claude_desktop": json.dumps(stdio, indent=2),
        "cursor": json.dumps(http, indent=2),
        "generic_stdio": json.dumps(stdio, indent=2),
        "python": py, "script": script, "http_url": http_url,
    }


@app.get("/api/mcp/info")
async def mcp_info(request: Request):
    """Everything the Config > MCP panel shows: endpoint URL, state, tool count and copy-paste client snippets."""
    http_url = str(request.base_url).rstrip("/") + "/mcp"
    out: dict[str, Any] = {"installed": MCP_INSTALLED, "ready": MCP is not None, "enabled": bool(State.cfg.mcp_enabled) and MCP is not None, "http_url": http_url,
                           "port": request.url.port, "configured_port": State.cfg.app_port, "tools": 0, "snippets": _mcp_snippets(http_url)}
    if MCP is not None:
        tools = await MCP.list_tools()
        out["tools"] = len(tools)
        out["tool_names"] = [t.name for t in tools]
    return out


@app.get("/api/mcp/tools.md")
async def mcp_tools_md(request: Request, download: int = 1):
    if MCP is None:
        raise HTTPException(404, "The mcp package is not installed" if not MCP_INSTALLED else "The MCP server is still starting, try again in a moment")
    from .mcp_server import tools_markdown
    md = await tools_markdown(MCP, str(request.base_url).rstrip("/") + "/mcp")
    headers = {"Content-Disposition": 'attachment; filename="content-pilot-spider-mcp-tools.md"'} if download else {}
    return Response(md, media_type="text/markdown; charset=utf-8", headers=headers)
