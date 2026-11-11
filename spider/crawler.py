"""The crawl loop: breadth-first, asyncio workers, one SQLite file per crawl."""
from __future__ import annotations

import asyncio
import json
import re
import time
import traceback
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from . import db as dbm
from .analysis import run_analysis
from .config import CRAWL_FILE_SECRETS, CrawlConfig
from .fetch import FetchResult, fetch, make_client
from .parse import LinkData, parse_html
from .render import RenderResult, Renderer
from .robots import RobotsInfo, build_robots, parse_sitemap, robots_token, robots_url_for
from .urlnorm import classify, folder_depth, guess_type_from_url, host_of, in_start_folder, is_internal, normalize, url_checks

ANALYSIS_INTERVAL = 15.0      # seconds between incremental analysis passes while crawling
COMMIT_INTERVAL = 1.0

# social share-intent URLs: one per post, add nothing to an audit, and the networks throttle bots
SHARE_RE = re.compile(
    r"^https?://(www\.)?(facebook\.com/(sharer|share\.php|dialog)|twitter\.com/(intent|share)|x\.com/intent|"
    r"linkedin\.com/(shareArticle|sharing|cws/share)|pinterest\.com/pin/create|reddit\.com/submit|"
    r"t\.me/share|api\.whatsapp\.com/send|wa\.me/\?text|telegram\.me/share|tumblr\.com/(share|widgets))", re.I)

_REASONS = {
    200: "OK", 201: "Created", 204: "No Content", 301: "Moved Permanently", 302: "Found", 303: "See Other",
    304: "Not Modified", 307: "Temporary Redirect", 308: "Permanent Redirect", 400: "Bad Request", 401: "Unauthorized",
    403: "Forbidden", 404: "Not Found", 405: "Method Not Allowed", 406: "Not Acceptable", 408: "Request Timeout",
    410: "Gone", 418: "I'm a teapot", 429: "Too Many Requests", 500: "Internal Server Error", 502: "Bad Gateway",
    503: "Service Unavailable", 504: "Gateway Timeout",
}


def _now() -> str:
    return datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M:%S")


def indexability(status: int | None, blocked: int, meta_robots: str, x_robots: str, canonical: str, url: str, error: str) -> tuple[int, str]:
    if blocked:
        return 0, "Blocked by robots.txt"
    if status is None:
        return 0, "No Response" if not error else error[:40]
    if 300 <= status < 400:
        return 0, "Redirected"
    if 400 <= status < 500:
        return 0, "Client Error"
    if status >= 500:
        return 0, "Server Error"
    if status != 200 and status not in (201, 203, 206):
        return 0, f"Non-200 ({status})"
    tokens = {t.strip().lower() for t in re.split(r"[,\s]+", f"{meta_robots or ''},{x_robots or ''}") if t.strip()}
    if "noindex" in tokens or "none" in tokens:
        return 0, "Noindex"
    if canonical and canonical != url:
        return 0, "Canonicalised"
    return 1, ""


class Crawler:
    def __init__(self, db_path: str | Path, cfg: CrawlConfig):
        self.db_path = Path(db_path)
        self.cfg = cfg
        self.con = None
        self.state = "idle"             # idle | running | paused | stopping | finished | stopped | error
        self.error = ""
        self._pause = asyncio.Event()
        self._stop = False
        self.queue: asyncio.Queue = asyncio.Queue()
        self.ids: dict[str, int] = {}
        self.robots: dict[str, RobotsInfo | None] = {}
        self._robots_locks: dict[str, asyncio.Lock] = {}
        self.crawled = 0
        self.pages = 0                  # internal HTML pages fetched
        self.enqueued = 0
        self._breakdown: tuple[float, dict[str, int]] = (0.0, {})
        self.started_at = 0.0
        self.finished_at = 0.0
        self._last_commit = 0.0
        self._last_analysis = 0.0
        self._rate_lock = asyncio.Lock()
        self._last_request = 0.0
        self.current: list[str] = []
        self.robots_ua = robots_token(cfg.user_agent)
        self.include = [re.compile(p) for p in cfg.include_regex if p.strip()]
        self.exclude = [re.compile(p) for p in cfg.exclude_regex if p.strip()]
        self.start_url = normalize(cfg.start_url, remove_params=cfg.remove_params) or cfg.start_url
        self.sitemaps_seen: set[str] = set()
        self.analysis_running = False
        self.renderer: Renderer | None = None
        self._client = None

    # ------------------------------------------------------------------ control
    def pause(self):
        if self.state == "running":
            self.state = "paused"
            self._pause.clear()

    def resume(self):
        if self.state == "paused":
            self.state = "running"
            self._pause.set()

    def stop(self):
        self._stop = True
        self.state = "stopping"
        self._pause.set()

    def _queue_breakdown(self) -> dict[str, int]:
        """Queued URLs by kind (pages / images / resources / external), refreshed at most every 2s."""
        now = time.time()
        if now - self._breakdown[0] < 2.0 or self.con is None:
            return self._breakdown[1]
        out = {"pages": 0, "images": 0, "resources": 0, "external": 0}
        try:
            for is_int, typ, n in self.con.execute("SELECT is_internal, type, count(*) FROM urls WHERE crawled=0 GROUP BY 1,2"):
                if not is_int:
                    out["external"] += n
                elif typ == "Images":
                    out["images"] += n
                elif typ in ("JavaScript", "CSS", "Media", "Fonts"):
                    out["resources"] += n
                else:
                    out["pages"] += n
        except Exception:  # noqa: BLE001
            pass
        self._breakdown = (now, out)
        return out

    def status(self) -> dict[str, Any]:
        elapsed = (self.finished_at or time.time()) - self.started_at if self.started_at else 0
        return {
            "state": self.state,
            "error": self.error,
            "crawled": self.crawled,
            "pages": self.pages,
            "queue": self._queue_breakdown() if self.state in ("running", "paused") else {},
            "queued": self.queue.qsize(),
            "total": self.enqueued,
            "elapsed": round(elapsed, 1),
            "rate": round(self.crawled / elapsed, 2) if elapsed > 0 else 0,
            "current": self.current[:5],
            "start_url": self.start_url,
            "db": str(self.db_path),
            "analysis_running": self.analysis_running,
            "rendering": self.renderer is not None,
            "mode": self.cfg.mode,
        }

    # ------------------------------------------------------------------ run
    async def run(self):
        self.state = "running"
        self._pause.set()
        self.started_at = time.time()
        self.con = dbm.connect(self.db_path)
        safe_cfg = {k: v for k, v in self.cfg.to_dict().items() if k not in CRAWL_FILE_SECRETS}
        dbm.set_meta(self.con, "config", safe_cfg)
        dbm.set_meta(self.con, "start_url", self.start_url)
        dbm.set_meta(self.con, "started_at", _now())
        dbm.set_meta(self.con, "state", "running")
        self.con.commit()
        self._load_existing()
        client = make_client(self.cfg, self.start_url)
        self._client = client
        try:
            async with client:
                if self.cfg.rendering == "javascript":
                    self.renderer = Renderer(self.cfg, self._render_allowed)
                    try:
                        await self.renderer.start()
                    except Exception as exc:  # noqa: BLE001
                        self.error = f"JavaScript rendering unavailable ({type(exc).__name__}: {str(exc).splitlines()[0][:120]}). Crawling text only."
                        self.renderer = None
                await self._seed(client)
                workers = [asyncio.create_task(self._worker(client, i)) for i in range(max(1, self.cfg.max_threads))]
                monitor = asyncio.create_task(self._monitor())
                await self.queue.join()
                self._stop = True
                for w in workers:
                    w.cancel()
                monitor.cancel()
                await asyncio.gather(*workers, monitor, return_exceptions=True)
                if self.renderer is not None:
                    await self.renderer.close()
                    self.renderer = None
            self.state = "finished" if not self.state == "stopping" else "stopped"
        except Exception as exc:  # noqa: BLE001
            self.state = "error"
            self.error = f"{type(exc).__name__}: {exc}"
            traceback.print_exc()
        finally:
            self.finished_at = time.time()
            try:
                self.current = []
                self.analysis_running = True
                run_analysis(self.con, self.cfg, final=True)
            except Exception:  # noqa: BLE001
                traceback.print_exc()
            finally:
                self.analysis_running = False
            dbm.set_meta(self.con, "state", self.state)
            dbm.set_meta(self.con, "finished_at", _now())
            dbm.set_meta(self.con, "crawled", self.crawled)
            self.con.commit()
            self.con.close()

    def _load_existing(self):
        for row in self.con.execute("SELECT id, url FROM urls"):
            self.ids[row["url"]] = row["id"]

    async def _render_allowed(self, url: str) -> bool:
        info = await self._robots_for(self._client, url)
        return info is None or info.allowed(url, self.robots_ua)

    async def _seed(self, client):
        if self.cfg.mode == "list":
            for raw in self.cfg.list_urls:
                u = normalize(raw, remove_params=self.cfg.remove_params)
                if u:
                    self._add_url(u, 0, None, None, force_fetch=True)
            return
        if self.cfg.mode == "sitemap":
            sitemaps = [s for s in self.cfg.sitemap_urls if s.strip()] or [self.start_url]
            for s in sitemaps:
                await self._crawl_sitemap(client, s, parent=None)
            if self.enqueued == 0:
                # not a sitemap - fall back to the host's robots.txt sitemaps, then /sitemap.xml
                info = await self._robots_for(client, self.start_url)
                found = list(info.sitemaps) if info is not None else []
                if not found:
                    found = [f"{self.start_url.split('/', 3)[0]}//{host_of(self.start_url)}/sitemap.xml"]
                for s in found:
                    await self._crawl_sitemap(client, s, parent=None)
            return
        self._add_url(self.start_url, 0, None, None, force_fetch=True)
        if self.cfg.crawl_sitemaps or self.cfg.sitemap_urls:
            info = await self._robots_for(client, self.start_url)
            sm = list(self.cfg.sitemap_urls)
            if info is not None and self.cfg.crawl_sitemaps:
                sm.extend(info.sitemaps)
            if self.cfg.crawl_sitemaps and not sm:
                sm.append(f"{self.start_url.split('/', 3)[0]}//{host_of(self.start_url)}/sitemap.xml")
            for s in sm:
                await self._crawl_sitemap(client, s, parent=None)

    async def _monitor(self):
        while True:
            await asyncio.sleep(1.0)
            now = time.time()
            if now - self._last_commit > COMMIT_INTERVAL:
                self.con.commit()
                self._last_commit = now
            if now - self._last_analysis > ANALYSIS_INTERVAL and self.state == "running":
                self._last_analysis = now
                try:
                    self.analysis_running = True
                    run_analysis(self.con, self.cfg, final=False)
                    self.con.commit()
                except Exception:  # noqa: BLE001
                    traceback.print_exc()
                finally:
                    self.analysis_running = False

    # ------------------------------------------------------------------ URL bookkeeping
    def _in_scope(self, url: str, depth: int, internal: bool, kind: str) -> bool:
        cfg = self.cfg
        if cfg.max_urls and self.enqueued >= cfg.max_urls:
            return False
        if cfg.mode in ("list", "sitemap") and kind != "redirect":
            return False
        if kind == "form":
            return False
        if len(url) > cfg.max_url_length:
            return False
        if cfg.max_query_strings and url.count("&") + (1 if "?" in url else 0) > cfg.max_query_strings:
            return False
        if self.exclude and any(r.search(url) for r in self.exclude):
            return False
        if self.include and not any(r.search(url) for r in self.include) and url != self.start_url:
            return False
        t = guess_type_from_url(url)
        if not internal:
            if not cfg.check_external:
                return False
            if cfg.skip_share_links and SHARE_RE.search(url):
                return False
            if t == "Images" or kind == "image":
                return cfg.crawl_images
            if t == "CSS" or kind == "css":
                return cfg.crawl_css
            if t == "JavaScript" or kind == "js":
                return cfg.crawl_js
            if t == "Media" or kind == "media":
                return cfg.crawl_media
            return True
        if cfg.max_depth and depth > cfg.max_depth:
            return False
        if not cfg.crawl_outside_start_folder and not in_start_folder(url, self.start_url) and kind == "hyperlink":
            return False
        if kind == "image" or t == "Images":
            return cfg.crawl_images
        if kind == "css" or t == "CSS":
            return cfg.crawl_css
        if kind == "js" or t == "JavaScript":
            return cfg.crawl_js
        if kind == "media" or t == "Media":
            return cfg.crawl_media
        if kind == "iframe":
            return cfg.crawl_iframes
        if kind == "canonical":
            return cfg.crawl_canonicals
        if kind == "hreflang":
            return cfg.crawl_hreflang
        if kind == "pagination":
            return cfg.crawl_pagination
        if kind == "amp":
            return cfg.crawl_amp
        if kind == "meta_refresh":
            return cfg.crawl_meta_refresh
        return True

    def _add_url(self, url: str, depth: int, from_id: int | None, link: LinkData | None, force_fetch: bool = False) -> int:
        """Insert the URL row (if new), record the link, and enqueue if in scope. Returns url id."""
        internal = is_internal(url, self.start_url, self.cfg.crawl_subdomains, self.cfg.cdn_domains)
        kind = link.kind if link else "redirect" if from_id is not None else "start"
        uid = self.ids.get(url)
        new = uid is None
        if new:
            checks = url_checks(url, self.cfg.url_max_chars) if internal else {}
            cols = ["url", "is_internal", "type", "depth", "folder_depth", "discovered_from", "crawled"] + list(checks)
            vals = [url, int(internal), guess_type_from_url(url), depth, folder_depth(url), from_id, 2] + list(checks.values())
            cur = self.con.execute(f"INSERT INTO urls({','.join(cols)}) VALUES ({','.join('?' * len(vals))})", vals)
            uid = cur.lastrowid
            self.ids[url] = uid
        else:
            row = self.con.execute("SELECT depth FROM urls WHERE id=?", (uid,)).fetchone()
            if row and (row["depth"] is None or depth < row["depth"]):
                self.con.execute("UPDATE urls SET depth=? WHERE id=?", (depth, uid))
        if from_id is not None and link is not None:
            dbm.insert_links(self.con, [(from_id, uid, link.kind, link.anchor, link.alt, link.rel, link.follow, link.target,
                                          link.position, link.path, int(internal), link.has_size_attrs, link.is_bg, link.hreflang, link.js_only)])
        elif from_id is not None and kind == "redirect":
            dbm.insert_links(self.con, [(from_id, uid, "redirect", "", None, "", 1, "", "", "", int(internal), 1, 0, "")])
        if new and (force_fetch or self._in_scope(url, depth, internal, kind)):
            self.con.execute("UPDATE urls SET crawled=0 WHERE id=?", (uid,))
            self.enqueued += 1
            self.queue.put_nowait((uid, url, depth, internal, kind))
        return uid

    # ------------------------------------------------------------------ robots / sitemaps
    async def _robots_for(self, client, url: str) -> RobotsInfo | None:
        host = host_of(url)
        if host in self.robots:
            return self.robots[host]
        lock = self._robots_locks.setdefault(host, asyncio.Lock())
        async with lock:
            if host in self.robots:
                return self.robots[host]
            rurl = robots_url_for(url)
            res = await fetch(client, rurl, self.cfg, want_body=True, max_bytes=512 * 1024)
            info = build_robots(rurl, res.status_code, res.content if res.status_code == 200 else None)
            self.robots[host] = info
            return info

    async def _crawl_sitemap(self, client, url: str, parent: str | None, depth: int = 0):
        url = normalize(url) or url
        if url in self.sitemaps_seen or depth > 5 or len(self.sitemaps_seen) > 1000:
            return
        self.sitemaps_seen.add(url)
        res = await fetch(client, url, self.cfg, want_body=True, max_bytes=60 * 1024 * 1024)
        is_index, count, size, children = 0, 0, 0, []
        if res.status_code == 200 and res.content:
            sm = parse_sitemap(url, res.content)
            is_index, size, children = int(sm.is_index), sm.size, sm.children
            count = len(sm.urls)
            if sm.urls:
                self.con.executemany("INSERT OR IGNORE INTO sitemap_urls(sitemap, url, lastmod) VALUES (?,?,?)",
                                     [(url, u, lm) for u, lm in sm.urls])
        self.con.execute("INSERT OR REPLACE INTO sitemaps(url, status_code, is_index, url_count, size, parent) VALUES (?,?,?,?,?,?)",
                         (url, res.status_code, is_index, count, size, parent))
        # record the sitemap itself as a crawled XML URL
        uid = self.ids.get(url)
        if uid is None:
            uid = self._add_url(url, 0, None, None)
        self._store_response(uid, url, res, internal=True)
        self.con.execute("UPDATE urls SET crawled=1, type='XML' WHERE id=?", (uid,))
        if res.status_code == 200 and not is_index and res.content:
            if self.cfg.mode == "spider":
                for u, _ in sm.urls:
                    if is_internal(u, self.start_url, self.cfg.crawl_subdomains, self.cfg.cdn_domains):
                        self._add_url(u, 1, None, None)
            elif self.cfg.mode == "sitemap":
                for u, _ in sm.urls:
                    if self.cfg.max_urls and self.enqueued >= self.cfg.max_urls:
                        break
                    self._add_url(u, 0, None, None, force_fetch=True)
        for child in children:
            await self._crawl_sitemap(client, child, parent=url, depth=depth + 1)
        self.con.commit()

    # ------------------------------------------------------------------ workers
    async def _throttle(self):
        if not self.cfg.max_urls_per_second:
            return
        async with self._rate_lock:
            gap = 1.0 / self.cfg.max_urls_per_second
            wait = self._last_request + gap - time.time()
            if wait > 0:
                await asyncio.sleep(wait)
            self._last_request = time.time()

    async def _worker(self, client, n: int):
        while True:
            item = await self.queue.get()
            try:
                await self._pause.wait()
                if self._stop:
                    continue
                uid, url, depth, internal, kind = item
                self.current.append(url)
                try:
                    await self._process(client, uid, url, depth, internal, kind)
                finally:
                    if url in self.current:
                        self.current.remove(url)
            except asyncio.CancelledError:
                raise
            except Exception as exc:  # noqa: BLE001
                traceback.print_exc()
                try:
                    self.con.execute("UPDATE urls SET crawled=1, error=?, status='Crawler error' WHERE id=?", (f"{type(exc).__name__}: {exc}"[:300], item[0]))
                except Exception:  # noqa: BLE001
                    pass
            finally:
                self.queue.task_done()

    def _store_response(self, uid: int, url: str, res: FetchResult, internal: bool) -> dict[str, Any]:
        ctype = res.content_type
        typ = classify(url, ctype) if (res.status_code and res.status_code < 300) or ctype else guess_type_from_url(url)
        if res.status_code and 300 <= res.status_code < 400:
            typ = classify(url, None) if not ctype or "html" in ctype else classify(url, ctype)
            if typ == "Unknown":
                typ = "HTML"
        status = res.error or _REASONS.get(res.status_code or 0, res.reason) if res.status_code else (res.error or "No Response")
        guess = guess_type_from_url(url)
        bad_ct = int(bool(ctype) and guess != "Unknown" and guess != "HTML" and classify(url, ctype) != guess and res.status_code == 200)
        cookies = [c.split("=", 1)[0].strip() for c in res.headers.get("set-cookie", "").split("\n") if c.strip()]
        fields = {
            "crawled": 1,
            "status_code": res.status_code,
            "status": status,
            "content_type": ctype,
            "type": typ,
            "response_time": res.elapsed,
            "size": len(res.content) if res.content else res.transferred,
            "transferred": res.transferred,
            "http_version": res.http_version,
            "last_modified": res.headers.get("last-modified", ""),
            "headers": json.dumps(res.headers),
            "cookies": json.dumps(cookies),
            "crawl_ts": _now(),
            "error": res.error or None,
            "bad_content_type": bad_ct,
            "x_robots": res.headers.get("x-robots-tag", ""),
            "http_refresh": res.headers.get("refresh", ""),
        }
        if res.status_code and 300 <= res.status_code < 400 and res.headers.get("location"):
            target = normalize(res.headers["location"], url, self.cfg.remove_params)
            fields["redirect_url"] = target or res.headers["location"]
            fields["redirect_type"] = "HTTP Redirect"
        dbm.update_url(self.con, uid, fields)
        return fields

    async def _process(self, client, uid: int, url: str, depth: int, internal: bool, kind: str):
        cfg = self.cfg
        blocked = 0
        if cfg.respect_robots or cfg.robots_report_only:
            info = await self._robots_for(client, url)
            if info is not None and not info.allowed(url, self.robots_ua):
                blocked = 1
                if not cfg.robots_report_only:
                    dbm.update_url(self.con, uid, {"crawled": 3, "blocked_robots": 1, "status": "Blocked by robots.txt",
                                                   "indexable": 0, "indexability_status": "Blocked by robots.txt",
                                                   "type": guess_type_from_url(url), "crawl_ts": _now()})
                    self.crawled += 1
                    return
        await self._throttle()
        want_body = internal and kind not in ("image", "css", "js", "media", "iframe") and guess_type_from_url(url) in ("HTML", "Unknown", "XML", "Other")
        if not internal and guess_type_from_url(url) == "Unknown":
            want_body = False
        res = await fetch(client, url, cfg, want_body=want_body)
        fields = self._store_response(uid, url, res, internal)
        fields["blocked_robots"] = blocked
        self.crawled += 1
        if res.status_code and 300 <= res.status_code < 400 and fields.get("redirect_url"):
            target = fields["redirect_url"]
            idx, istat = indexability(res.status_code, blocked, "", fields["x_robots"], "", url, res.error)
            dbm.update_url(self.con, uid, {"indexable": idx, "indexability_status": istat, "blocked_robots": blocked})
            if target != url:
                self._add_url(target, depth, uid, None)
            return
        parse_it = fields["type"] == "HTML" and bool(res.content) and internal and res.status_code is not None \
            and (res.status_code < 400 or res.status_code in (404, 410, 500, 503))
        if parse_it:
            self.pages += 1
            rr = None
            if self.renderer is not None and res.status_code == 200:
                rr = await self.renderer.render(url)
            self._handle_html(uid, url, depth, res, fields, blocked, rr)
        else:
            idx, istat = indexability(res.status_code, blocked, "", fields["x_robots"], "", url, res.error)
            dbm.update_url(self.con, uid, {"indexable": idx, "indexability_status": istat, "blocked_robots": blocked})

    def _handle_html(self, uid: int, url: str, depth: int, res: FetchResult, fields: dict[str, Any], blocked: int,
                     rr: RenderResult | None = None):
        cfg = self.cfg
        html = res.text
        pd_raw = parse_html(html, url, res.headers, cfg, status_code=res.status_code or 0)
        pd = pd_raw
        extra: dict[str, Any] = {}
        if rr is not None:
            if rr.ok:
                pd = parse_html(rr.html, url, res.headers, cfg, status_code=res.status_code or 0)
                raw, fr = pd_raw.fields, pd.fields
                raw_links = {l.to_url for l in pd_raw.links if l.kind == "hyperlink"}
                js_links = 0
                for l in pd.links:
                    if l.kind == "hyperlink" and l.to_url not in raw_links:
                        l.js_only = 1
                        js_links += 1
                wc_raw, wc = raw.get("word_count") or 0, fr.get("word_count") or 0
                extra = {
                    "rendered": 1, "render_time": rr.elapsed, "render_error": None,
                    "orig_title": raw.get("title", ""), "orig_meta_desc": raw.get("meta_desc", ""), "orig_h1": raw.get("h1", ""),
                    "orig_canonical": raw.get("canonical", ""), "orig_meta_robots": raw.get("meta_robots", ""), "orig_word_count": wc_raw,
                    "js_word_delta": wc - wc_raw, "js_word_pct": round(100.0 * (wc - wc_raw) / wc, 1) if wc else 0.0,
                    "js_links_count": js_links, "js_content": int(wc > wc_raw),
                    "js_errors": len(rr.errors), "js_errors_text": json.dumps(rr.errors[:50]),
                    "js_blocked": len(rr.blocked), "js_blocked_text": json.dumps(rr.blocked[:50]),
                    "meta_fragment": max(raw.get("meta_fragment", 0), fr.get("meta_fragment", 0)),
                }
            else:
                extra = {"rendered": 0, "render_error": rr.error or "; ".join(rr.errors[:3]) or "Render failed", "render_time": rr.elapsed}
        f = pd.fields
        f.update(extra)
        f["blocked_robots"] = blocked
        idx, istat = indexability(res.status_code, blocked, f.get("meta_robots", ""), fields.get("x_robots", ""), f.get("canonical", ""), url, res.error)
        f["indexable"], f["indexability_status"] = idx, istat
        if f.get("meta_refresh", "").startswith("http") and f["meta_refresh"] != url:
            f["redirect_url"] = f["meta_refresh"]
            f["redirect_type"] = "Meta Refresh Redirect"
        elif "url=" in (fields.get("http_refresh") or "").lower():
            m = re.search(r"url\s*=\s*['\"]?([^'\"]+)", fields["http_refresh"], re.I)
            if m:
                t = normalize(m.group(1), url)
                if t and t != url:
                    f["redirect_url"] = t
                    f["redirect_type"] = "HTTP Refresh Redirect"
        dbm.update_url(self.con, uid, f)
        if cfg.store_html:
            self.con.execute("INSERT OR REPLACE INTO bodies(url_id, html, text, rendered, screenshot) VALUES (?,?,?,?,?)",
                             (uid, dbm.compress(html), dbm.compress(pd.text),
                              dbm.compress(rr.html) if rr is not None and rr.ok else None,
                              rr.screenshot if rr is not None else None))
        if pd.uncrawlable:
            self.con.executemany("INSERT INTO uncrawlable(from_id, href, anchor, reason) VALUES (?,?,?,?)",
                                 [(uid, h, a, r) for h, a, r in pd.uncrawlable[:500]])
        if cfg.extract_structured_data and (res.status_code or 0) < 400:
            try:
                from .structured import analyse as analyse_sd
                sd = analyse_sd(html, url, cfg.validate_schema_org, cfg.validate_google)
                self.con.execute("DELETE FROM structured_data WHERE url_id=?", (uid,))
                self.con.executemany("INSERT INTO structured_data(url_id, syntax, type, data, errors, warnings) VALUES (?,?,?,?,?,?)",
                                     [(uid, i["syntax"], i["type"], json.dumps(i["data"], ensure_ascii=False)[:200000], json.dumps(i["errors"]), json.dumps(i["warnings"])) for i in sd["items"]])
                dbm.update_url(self.con, uid, sd["counts"])
            except Exception as exc:  # noqa: BLE001 - structured data must never break a crawl
                dbm.update_url(self.con, uid, {"sd_parse_errors": 1})
                print(f"[structured] {url}: {exc}")
        if cfg.respect_noindex and idx == 0 and istat == "Noindex":
            return
        follow_ok = True
        tokens = {t.strip().lower() for t in re.split(r"[,\s]+", f.get("meta_robots", "") + "," + (fields.get("x_robots") or "")) if t.strip()}
        page_nofollow = "nofollow" in tokens or "none" in tokens
        for link in pd.links:
            if self.cfg.mode in ("list", "sitemap"):
                self._add_url(link.to_url, depth + 1, uid, link)   # record only; _in_scope blocks the fetch
                continue
            if link.kind == "hyperlink":
                target_internal = is_internal(link.to_url, self.start_url, cfg.crawl_subdomains, cfg.cdn_domains)
                if not link.follow or page_nofollow:
                    if target_internal and not cfg.follow_internal_nofollow:
                        self._record_only(link.to_url, depth + 1, uid, link)
                        continue
                    if not target_internal and not cfg.follow_external_nofollow:
                        self._record_only(link.to_url, depth + 1, uid, link)
                        continue
            self._add_url(link.to_url, depth + 1, uid, link)
        if f.get("redirect_url"):
            self._add_url(f["redirect_url"], depth, uid, None)

    def _record_only(self, url: str, depth: int, from_id: int, link: LinkData):
        """Record the URL and link without ever fetching it."""
        internal = is_internal(url, self.start_url, self.cfg.crawl_subdomains, self.cfg.cdn_domains)
        uid = self.ids.get(url)
        if uid is None:
            cur = self.con.execute("INSERT INTO urls(url, is_internal, type, depth, folder_depth, discovered_from, crawled) VALUES (?,?,?,?,?,?,2)",
                                   (url, int(internal), guess_type_from_url(url), depth, folder_depth(url), from_id))
            uid = cur.lastrowid
            self.ids[url] = uid
        dbm.insert_links(self.con, [(from_id, uid, link.kind, link.anchor, link.alt, link.rel, link.follow, link.target,
                                      link.position, link.path, int(internal), link.has_size_attrs, link.is_bg, link.hreflang, link.js_only)])
