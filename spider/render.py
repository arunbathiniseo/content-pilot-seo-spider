"""JavaScript rendering with headless Chromium (Playwright). One browser per crawl, one context per page."""
from __future__ import annotations

import asyncio
import time
from dataclasses import dataclass, field
from typing import Awaitable, Callable
from urllib.parse import urlsplit

from .config import CrawlConfig
from .netguard import chromium_no_sandbox, hosted, public_host
from .urlnorm import is_internal

_LOOPBACK = ("127.0.0.1", "localhost", "::1")


@dataclass
class RenderResult:
    html: str = ""
    ok: bool = False
    error: str = ""
    errors: list[str] = field(default_factory=list)        # console errors + uncaught exceptions
    blocked: list[str] = field(default_factory=list)       # resources blocked by robots.txt during render
    failed: list[str] = field(default_factory=list)        # resources that failed to load
    screenshot: bytes | None = None
    elapsed: float = 0.0


class Renderer:
    def __init__(self, cfg: CrawlConfig, allowed: Callable[[str], Awaitable[bool]]):
        self.cfg = cfg
        self.allowed = allowed
        self.pw = None
        self.browser = None
        self.sem = asyncio.Semaphore(max(1, min(cfg.max_threads, cfg.render_concurrency)))

    async def start(self) -> None:
        from playwright.async_api import async_playwright
        self.pw = await async_playwright().start()
        self.browser = await self.pw.chromium.launch(headless=True, chromium_sandbox=not chromium_no_sandbox(),
                                                      args=["--disable-gpu", "--disable-dev-shm-usage"] + (["--no-sandbox"] if chromium_no_sandbox() else []))

    async def close(self) -> None:
        try:
            if self.browser:
                await self.browser.close()
        finally:
            if self.pw:
                await self.pw.stop()
            self.browser = self.pw = None

    async def render(self, url: str) -> RenderResult:
        from playwright.async_api import TimeoutError as PWTimeout
        cfg = self.cfg
        res = RenderResult()
        start = time.perf_counter()
        async with self.sem:
            if self.browser is None:
                res.error = "Browser not started"
                return res
            ctx = None
            _u = urlsplit(url)
            page_origin = f"{_u.scheme}://{_u.netloc}"   # basic auth goes to the page's own origin only
            try:
                ctx = await self.browser.new_context(
                    user_agent=cfg.user_agent,
                    viewport={"width": cfg.render_width, "height": cfg.render_height},
                    device_scale_factor=1,
                    is_mobile=cfg.render_mobile,
                    has_touch=cfg.render_mobile,
                    ignore_https_errors=not cfg.verify_ssl,
                    http_credentials={"username": cfg.auth_user, "password": cfg.auth_pass, "origin": page_origin} if cfg.auth_user else None,
                    accept_downloads=False,
                    java_script_enabled=True,
                )
                page = await ctx.new_page()
                aborted: set[str] = set()

                def on_console(m):
                    if m.type != "error":
                        return
                    text = m.text or ""
                    # loads we aborted ourselves (blocked images/fonts, robots-blocked) are not site errors
                    if "net::ERR_FAILED" in text or "net::ERR_BLOCKED_BY_CLIENT" in text or "net::ERR_ABORTED" in text:
                        return
                    loc = ""
                    try:
                        loc = m.location.get("url", "") if isinstance(m.location, dict) else ""
                    except Exception:  # noqa: BLE001
                        loc = ""
                    res.errors.append((text[:300] + (f"  @ {loc[:150]}" if loc and loc != url else "")))

                page.on("pageerror", lambda e: res.errors.append(f"Uncaught: {str(e).splitlines()[0][:300]}"))
                page.on("console", on_console)
                page.on("requestfailed", lambda r: res.failed.append(r.url) if r.url != url and r.url not in aborted else None)

                async def route(r):
                    req = r.request
                    rt = req.resource_type
                    if hosted() and not await public_host(urlsplit(req.url).hostname or ""):   # netguard: nothing private from the hosted box
                        aborted.add(req.url)
                        await r.abort()
                        return
                    if (urlsplit(req.url).hostname or "") in _LOOPBACK and (_u.hostname or "") not in _LOOPBACK:   # a public page has no business with this machine
                        aborted.add(req.url)
                        await r.abort()
                        return
                    if cfg.render_block_images and rt in ("image", "media", "font"):
                        aborted.add(req.url)
                        await r.abort()
                        return
                    if cfg.respect_robots and req.url != url and rt in ("script", "stylesheet", "xhr", "fetch", "image", "font", "media", "document"):
                        try:
                            if not await self.allowed(req.url):
                                res.blocked.append(req.url)
                                aborted.add(req.url)
                                await r.abort()
                                return
                        except Exception:  # noqa: BLE001
                            pass
                    if cfg.custom_headers and is_internal(req.url, url, cfg.crawl_subdomains):   # custom headers stay on the crawled site
                        await r.continue_(headers={**req.headers, **cfg.custom_headers})
                    else:
                        await r.continue_()

                await page.route("**/*", route)
                try:
                    await page.goto(url, wait_until="load", timeout=int(cfg.timeout * 1000))
                except PWTimeout:
                    res.errors.append("Render: load event timed out")
                try:
                    await page.wait_for_load_state("networkidle", timeout=int(cfg.render_ajax_timeout * 1000))
                except PWTimeout:
                    pass
                if cfg.render_ajax_timeout and cfg.render_wait_extra:
                    await page.wait_for_timeout(int(cfg.render_wait_extra * 1000))
                res.html = await page.content()
                if cfg.render_screenshots:
                    try:
                        res.screenshot = await page.screenshot(type="jpeg", quality=55, full_page=cfg.render_full_page_screenshot)
                    except Exception:  # noqa: BLE001
                        res.screenshot = None
                res.ok = bool(res.html)
            except Exception as exc:  # noqa: BLE001
                res.error = f"{type(exc).__name__}: {str(exc).splitlines()[0][:300]}"
            finally:
                if ctx is not None:
                    try:
                        await ctx.close()
                    except Exception:  # noqa: BLE001
                        pass
        res.elapsed = round(time.perf_counter() - start, 3)
        return res
