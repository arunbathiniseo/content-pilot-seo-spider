"""Lighthouse integration (Screaming Frog "PageSpeed" + "Accessibility" tabs).

Two sources, one result shape:
* local: the vendored Lighthouse CLI (ui/vendor/node_modules/lighthouse) driving the Playwright Chromium, headless;
  no key, no quota, ~20-30 s per page, lab data only.
* psi:   Google's PageSpeed Insights API with the person's key; adds CrUX field data (real-user LCP / CLS / INP).

A run stores the four category scores, the lab metrics, field metrics when present, and every failing audit with its
savings and sample items. Audits map to the PageSpeed / Accessibility filters in issues.py via urls.lh_failed.
"""
from __future__ import annotations

import asyncio
import json
import os
import shutil
import sqlite3
import time
from datetime import datetime
from pathlib import Path
from typing import Any

import httpx

from . import db as dbm
from .config import CrawlConfig
from .netguard import chromium_no_sandbox

ROOT = Path(__file__).resolve().parents[1]
CLI = ROOT / "ui" / "vendor" / "node_modules" / "lighthouse" / "cli" / "index.js"
CATEGORIES = ("performance", "accessibility", "best-practices", "seo")
METRICS = {"largest-contentful-paint": "lcp", "cumulative-layout-shift": "cls", "total-blocking-time": "tbt", "first-contentful-paint": "fcp",
           "speed-index": "si", "interactive": "tti"}
# audits that are shown as PageSpeed opportunities / diagnostics (Screaming Frog's filter names), in display order
PAGESPEED_AUDITS: list[tuple[str, str]] = [
    ("render-blocking-resources", "Eliminate Render-Blocking Resources"), ("uses-responsive-images", "Properly Size Images"), ("offscreen-images", "Defer Offscreen Images"),
    ("unminified-css", "Minify CSS"), ("unminified-javascript", "Minify JavaScript"), ("unused-css-rules", "Reduce Unused CSS"), ("unused-javascript", "Reduce Unused JavaScript"),
    ("uses-optimized-images", "Efficiently Encode Images"), ("modern-image-formats", "Serve Images in Next-Gen Formats"), ("uses-text-compression", "Enable Text Compression"),
    ("uses-rel-preconnect", "Preconnect to Required Origins"), ("server-response-time", "Reduce Initial Server Response Time"), ("redirects", "Avoid Multiple Page Redirects"),
    ("uses-rel-preload", "Preload Key Requests"), ("efficient-animated-content", "Use Video Formats for Animated Content"), ("dom-size", "Avoid Excessive DOM Size"),
    ("bootup-time", "Reduce JavaScript Execution Time"), ("mainthread-work-breakdown", "Minimize Main-Thread Work"), ("uses-long-cache-ttl", "Serve Static Assets with an Efficient Cache Policy"),
    ("total-byte-weight", "Avoid Enormous Network Payloads"), ("layout-shifts", "Avoid Large Layout Shifts"), ("unsized-images", "Image Elements Do Not Have Explicit Width & Height"),
    ("long-tasks", "Avoid Long Main-Thread Tasks"), ("third-party-summary", "Reduce the Impact of Third-Party Code"), ("legacy-javascript", "Avoid Serving Legacy JavaScript"),
    ("font-display", "Ensure Text Remains Visible During Webfont Load"), ("lcp-lazy-loaded", "Largest Contentful Paint Image Was Lazily Loaded"), ("viewport", "Has a <meta name=viewport> Tag"),
]
ACCESSIBILITY_AUDITS: list[tuple[str, str]] = [
    ("color-contrast", "Insufficient Colour Contrast"), ("image-alt", "Images Missing Alt Text"), ("link-name", "Links Without Discernible Name"), ("button-name", "Buttons Without Accessible Name"),
    ("html-has-lang", "<html> Missing lang Attribute"), ("html-lang-valid", "<html> lang Not Valid"), ("document-title", "Missing <title>"), ("meta-viewport", "Viewport Disables Zooming"),
    ("heading-order", "Heading Levels Skipped"), ("label", "Form Elements Without Labels"), ("input-image-alt", "Image Buttons Missing Alt"), ("frame-title", "Frames Without Title"),
    ("list", "Lists Contain Non-List Items"), ("listitem", "List Items Outside Lists"), ("tabindex", "tabindex Greater Than 0"), ("duplicate-id-aria", "Duplicate ARIA ids"),
    ("aria-allowed-attr", "ARIA Attributes Not Allowed"), ("aria-required-attr", "ARIA Required Attributes Missing"), ("aria-valid-attr-value", "Invalid ARIA Attribute Values"),
    ("aria-roles", "Invalid ARIA Roles"), ("aria-hidden-focus", "aria-hidden Elements Contain Focusable Content"), ("link-in-text-block", "Links Not Distinguishable Without Colour"),
    ("target-size", "Touch Targets Too Small"), ("label-content-name-mismatch", "Label and Visible Text Mismatch"), ("select-name", "Select Elements Without Name"),
    ("video-caption", "Video Without Captions"), ("td-headers-attr", "Table Cells Reference Missing Headers"), ("th-has-data-cells", "Table Headers Without Data Cells"),
    ("skip-link", "Skip Link Not Focusable"), ("bypass", "No Way to Bypass Repeated Content"),
]
A11Y_IDS = {a for a, _ in ACCESSIBILITY_AUDITS}


class LighthouseError(Exception):
    pass


def available() -> dict[str, Any]:
    node = shutil.which("node")
    return {"node": bool(node), "node_path": node or "", "lighthouse": CLI.exists(), "cli": str(CLI)}


def chrome_path() -> str:
    base = Path(os.environ.get("PLAYWRIGHT_BROWSERS_PATH") or (Path(os.environ.get("LOCALAPPDATA", "")) / "ms-playwright"))
    if base.exists():
        for p in sorted(base.glob("chromium*"), reverse=True):
            exe = next(p.rglob("chrome.exe"), None) or next(p.rglob("chrome"), None)
            if exe:
                return str(exe)
    return ""


# ----------------------------------------------------------------------------- normalise
def _num(v: Any) -> float | None:
    try:
        return None if v is None else float(v)
    except (TypeError, ValueError):
        return None


def normalise(lhr: dict[str, Any], source: str, device: str, field: dict[str, Any] | None = None) -> dict[str, Any]:
    """Compact result from a Lighthouse LHR (local or from PSI's `lighthouseResult`)."""
    cats = lhr.get("categories", {})
    audits = lhr.get("audits", {})
    scores = {c: (round(cats[c]["score"] * 100) if cats.get(c, {}).get("score") is not None else None) for c in CATEGORIES}
    metrics: dict[str, Any] = {}
    for aid, key in METRICS.items():
        a = audits.get(aid) or {}
        metrics[key] = _num(a.get("numericValue"))
        metrics[key + "_display"] = (a.get("displayValue") or "").replace("\xa0", " ")
    failed: list[dict[str, Any]] = []
    for aid, a in audits.items():
        sc = a.get("score")
        mode = a.get("scoreDisplayMode", "")
        if sc is None or sc >= 0.9 or mode in ("informative", "notApplicable", "manual", "error"):
            continue
        if aid in METRICS:
            continue
        details = a.get("details") if isinstance(a.get("details"), dict) else {}
        items = [it for it in (details.get("items") or []) if isinstance(it, dict)]
        sample = []
        for it in items[:10]:
            node = it.get("node") if isinstance(it.get("node"), dict) else {}
            src = it.get("source") if isinstance(it.get("source"), dict) else {}
            url_v = it.get("url") if isinstance(it.get("url"), str) else (it.get("url") or {}).get("url", "") if isinstance(it.get("url"), dict) else ""
            try:
                sample.append({k: v for k, v in {
                    "url": url_v or src.get("url") or "", "snippet": (node.get("snippet") or "")[:200], "selector": node.get("selector") or "",
                    "label": node.get("nodeLabel") or "", "wasted_ms": it.get("wastedMs"), "wasted_bytes": it.get("wastedBytes"), "total_bytes": it.get("totalBytes"),
                    "score": it.get("score"), "duration": it.get("duration"), "entity": it.get("entity") if isinstance(it.get("entity"), str) else None}.items() if v not in (None, "", 0)})
            except Exception:  # noqa: BLE001 - an odd item shape must not sink the whole audit
                continue
        group = "accessibility" if aid in A11Y_IDS or any(aid in (r.get("id") for r in cats.get("accessibility", {}).get("auditRefs", [])) for _ in [0]) else "performance"
        failed.append({"id": aid, "title": a.get("title", aid), "score": sc, "display": (a.get("displayValue") or "").replace("\xa0", " "), "group": group,
                       "savings_ms": _num(details.get("overallSavingsMs")), "savings_bytes": _num(details.get("overallSavingsBytes")), "items": len(items), "sample": sample,
                       "description": (a.get("description") or "")[:400]})
    failed.sort(key=lambda f: (f["group"] != "performance", -(f["savings_ms"] or 0), -(f["savings_bytes"] or 0), f["score"]))
    fld: dict[str, Any] = {}
    if field:
        for k, key in (("LARGEST_CONTENTFUL_PAINT_MS", "lcp"), ("CUMULATIVE_LAYOUT_SHIFT_SCORE", "cls"), ("INTERACTION_TO_NEXT_PAINT", "inp"), ("FIRST_CONTENTFUL_PAINT_MS", "fcp"), ("EXPERIMENTAL_TIME_TO_FIRST_BYTE", "ttfb")):
            m = (field.get("metrics") or {}).get(k)
            if m:
                fld[key] = m.get("percentile") / (100.0 if key == "cls" else 1.0)
                fld[key + "_category"] = m.get("category")
        fld["overall_category"] = field.get("overall_category")
    return {"source": source, "device": device, "fetched_at": datetime.now().isoformat(timespec="seconds"), "scores": scores, "metrics": metrics, "field": fld,
            "failed": failed, "final_url": lhr.get("finalDisplayedUrl") or lhr.get("finalUrl") or "", "lighthouse_version": lhr.get("lighthouseVersion", ""),
            "runtime_error": (lhr.get("runtimeError") or {}).get("message", "")}


# ----------------------------------------------------------------------------- runners
async def run_local(url: str, device: str = "mobile", timeout: float = 150.0) -> dict[str, Any]:
    av = available()
    if not av["node"]:
        raise LighthouseError("Node.js is not installed (needed to run Lighthouse locally). Install Node, or add a PageSpeed Insights API key in Config → PageSpeed.")
    if not av["lighthouse"]:
        raise LighthouseError("Lighthouse is not installed: run  cd ui\\vendor && npm install  in the app folder.")
    chrome = chrome_path()
    if not chrome:
        raise LighthouseError("Headless Chromium not found: run  .venv\\Scripts\\python.exe -m playwright install chromium")
    args = [av["node_path"], str(CLI), url, "--output=json", "--output-path=stdout", "--quiet", "--chrome-flags=--headless=new " + ("--no-sandbox " if chromium_no_sandbox() else "") + "--disable-gpu --disable-dev-shm-usage",
            "--only-categories=" + ",".join(CATEGORIES), f"--form-factor={'desktop' if device == 'desktop' else 'mobile'}", "--max-wait-for-load=45000"]
    if device == "desktop":
        args += ["--screenEmulation.disabled", "--throttling.cpuSlowdownMultiplier=1"]
    else:
        args += ["--screenEmulation.mobile"]
    env = {**os.environ, "CHROME_PATH": chrome}
    # The app runs under pythonw, which has no console, so a console-subsystem child such as Node would get a brand-new
    # visible console window for every audit. CREATE_NO_WINDOW starts Node without one; Chrome is a GUI process and
    # needs nothing. (Do not use CREATE_NEW_CONSOLE + hidden window here: Node/Chrome hang on it.)
    flags: dict[str, Any] = {"creationflags": 0x08000000} if os.name == "nt" else {}       # subprocess.CREATE_NO_WINDOW
    proc = await asyncio.create_subprocess_exec(*args, stdout=asyncio.subprocess.PIPE, stderr=asyncio.subprocess.PIPE, env=env, **flags)
    try:
        out, err = await asyncio.wait_for(proc.communicate(), timeout=timeout)
    except asyncio.TimeoutError:
        proc.kill()
        raise LighthouseError(f"Lighthouse timed out after {int(timeout)} s on {url}")
    if proc.returncode != 0 or not out.strip():
        msg = err.decode("utf-8", "replace").strip().splitlines()
        raise LighthouseError(f"Lighthouse failed on {url}: {(msg[-1] if msg else 'no output')[:300]}")
    try:
        lhr = json.loads(out.decode("utf-8", "replace"))
    except ValueError as exc:
        raise LighthouseError(f"Lighthouse returned invalid JSON for {url}: {exc}")
    res = normalise(lhr, "local", device)
    if res["runtime_error"]:
        raise LighthouseError(f"Lighthouse could not load {url}: {res['runtime_error'][:200]}")
    return res


async def run_psi(url: str, api_key: str, device: str = "mobile", timeout: float = 120.0) -> dict[str, Any]:
    params = [("url", url), ("strategy", "desktop" if device == "desktop" else "mobile"), ("key", api_key)] + [("category", c) for c in CATEGORIES]
    async with httpx.AsyncClient(timeout=timeout) as client:
        try:
            r = await client.get("https://www.googleapis.com/pagespeedonline/v5/runPagespeed", params=params)
        except httpx.HTTPError as exc:
            raise LighthouseError(f"PageSpeed Insights unreachable: {exc}")
    if r.status_code == 400 and "API key" in r.text:
        raise LighthouseError("PageSpeed Insights rejected the API key.")
    if r.status_code == 429:
        raise LighthouseError("PageSpeed Insights quota exceeded (429). Lower the page count or wait.")
    if r.status_code >= 400:
        raise LighthouseError(f"PageSpeed Insights error {r.status_code}: {r.text[:200]}")
    data = r.json()
    return normalise(data.get("lighthouseResult") or {}, "psi", device, data.get("loadingExperience"))


# ----------------------------------------------------------------------------- storage
def ensure_table(con: sqlite3.Connection) -> None:
    con.execute("""CREATE TABLE IF NOT EXISTS lighthouse (url_id INTEGER PRIMARY KEY, device TEXT, source TEXT, fetched_at TEXT, scores TEXT, metrics TEXT, field TEXT,
                   failed TEXT, final_url TEXT, version TEXT)""")


def store(con: sqlite3.Connection, uid: int, res: dict[str, Any]) -> None:
    ensure_table(con)
    con.execute("INSERT OR REPLACE INTO lighthouse(url_id, device, source, fetched_at, scores, metrics, field, failed, final_url, version) VALUES (?,?,?,?,?,?,?,?,?,?)",
                (uid, res["device"], res["source"], res["fetched_at"], json.dumps(res["scores"]), json.dumps(res["metrics"]), json.dumps(res["field"]),
                 json.dumps(res["failed"], ensure_ascii=False), res["final_url"], res["lighthouse_version"]))
    s, m, f = res["scores"], res["metrics"], res["field"]
    failed_ids = "," + ",".join(x["id"] for x in res["failed"]) + "," if res["failed"] else ""
    dbm.update_url(con, uid, {
        "lh_perf": s.get("performance"), "lh_a11y": s.get("accessibility"), "lh_bp": s.get("best-practices"), "lh_seo": s.get("seo"),
        "lh_lcp": m.get("lcp"), "lh_cls": m.get("cls"), "lh_tbt": m.get("tbt"), "lh_fcp": m.get("fcp"), "lh_si": m.get("si"), "lh_tti": m.get("tti"),
        "lh_field_lcp": f.get("lcp"), "lh_field_cls": f.get("cls"), "lh_field_inp": f.get("inp"),
        "lh_failed": failed_ids, "lh_a11y_fails": sum(1 for x in res["failed"] if x["group"] == "accessibility"), "lh_perf_fails": sum(1 for x in res["failed"] if x["group"] == "performance"),
        "lh_at": res["fetched_at"], "lh_device": res["device"], "lh_source": res["source"],
    })


def result_for(con: sqlite3.Connection, uid: int) -> dict[str, Any] | None:
    try:
        r = con.execute("SELECT * FROM lighthouse WHERE url_id=?", (uid,)).fetchone()
    except sqlite3.OperationalError:
        return None
    if not r:
        return None
    return {"device": r["device"], "source": r["source"], "fetched_at": r["fetched_at"], "scores": json.loads(r["scores"]), "metrics": json.loads(r["metrics"]),
            "field": json.loads(r["field"] or "{}"), "failed": json.loads(r["failed"] or "[]"), "final_url": r["final_url"], "version": r["version"]}


def sample_ids(con: sqlite3.Connection, cfg: CrawlConfig, n: int) -> list[int]:
    """Pages worth auditing: the start page, then indexable 200 HTML pages by inlinks and shallowness."""
    ids = [r[0] for r in con.execute("SELECT id FROM urls WHERE crawled=1 AND is_internal=1 AND type='HTML' AND status_code=200 AND indexable=1 ORDER BY inlinks DESC, depth ASC, id LIMIT ?", (max(1, n),))]
    if cfg.start_url:
        root = con.execute("SELECT id FROM urls WHERE url IN (?, ?) LIMIT 1", (cfg.start_url, cfg.start_url.rstrip("/") + "/")).fetchone()
        if root and root[0] not in ids:
            ids = [root[0]] + ids[:-1]
    return ids


# ----------------------------------------------------------------------------- job
class LighthouseJob:
    def __init__(self) -> None:
        self.state = "idle"       # idle | running | done | cancelled | error
        self.total = self.done = self.ok = 0
        self.errors: list[str] = []
        self.current: list[str] = []
        self.started = 0.0
        self._cancel = False

    def cancel(self) -> None:
        self._cancel = True

    def status(self) -> dict[str, Any]:
        return {"state": self.state, "total": self.total, "done": self.done, "ok": self.ok, "errors": self.errors[-5:], "error_count": len(self.errors),
                "current": self.current[:4], "elapsed": round(time.time() - self.started, 1) if self.started else 0}


async def run_job(con: sqlite3.Connection, cfg: CrawlConfig, ids: list[int], job: LighthouseJob) -> None:
    job.state, job.started, job.total = "running", time.time(), len(ids)
    device = cfg.lighthouse_device or "mobile"
    key = (cfg.psi_api_key or "").strip()
    sem = asyncio.Semaphore(max(1, min(4, cfg.lighthouse_concurrency or 1)) if not key else 2)
    rows = {r["id"]: r["url"] for r in con.execute(f"SELECT id, url FROM urls WHERE id IN ({','.join('?' * len(ids))})", ids)} if ids else {}

    async def one(uid: int) -> None:
        if job._cancel:
            return
        url = rows.get(uid)
        if not url:
            job.done += 1
            return
        async with sem:
            if job._cancel:
                return
            job.current.append(url)
            try:
                res = await (run_psi(url, key, device) if key else run_local(url, device))
                store(con, uid, res)
                con.commit()
                job.ok += 1
            except LighthouseError as exc:
                job.errors.append(str(exc))
            except Exception as exc:  # noqa: BLE001
                job.errors.append(f"{url}: {type(exc).__name__}: {exc}")
            finally:
                job.done += 1
                if url in job.current:
                    job.current.remove(url)
    try:
        await asyncio.gather(*(one(uid) for uid in ids))
        from .issues import rebuild_issue_table
        rebuild_issue_table(con, cfg)
        con.commit()
        job.state = "cancelled" if job._cancel else "done"
    except Exception as exc:  # noqa: BLE001
        job.state = "error"
        job.errors.append(f"{type(exc).__name__}: {exc}")
