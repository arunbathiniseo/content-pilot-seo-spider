"""HTTP fetching with httpx. Redirects are NOT followed - each hop is its own URL record."""
from __future__ import annotations

import time
from dataclasses import dataclass, field

import httpx

from .config import CrawlConfig
from .netguard import hosted, public_host
from .urlnorm import is_internal


@dataclass
class FetchResult:
    url: str
    status_code: int | None = None
    reason: str = ""
    headers: dict[str, str] = field(default_factory=dict)
    content: bytes = b""
    content_type: str = ""
    encoding: str | None = None
    http_version: str = ""
    elapsed: float = 0.0
    transferred: int = 0
    error: str = ""
    truncated: bool = False

    @property
    def text(self) -> str:
        if not self.content:
            return ""
        enc = self.encoding or "utf-8"
        try:
            return self.content.decode(enc, "replace")
        except (LookupError, TypeError):
            return self.content.decode("utf-8", "replace")


class _PublicOnlyTransport(httpx.AsyncHTTPTransport):
    """Hosted mode: refuse any request whose host is not a public address (netguard), before connecting."""

    async def handle_async_request(self, request: httpx.Request) -> httpx.Response:
        if not await public_host(request.url.host):
            raise httpx.ConnectError(f"Blocked: {request.url.host} is not a public address", request=request)
        return await super().handle_async_request(request)


def make_client(cfg: CrawlConfig, scope_url: str = "") -> httpx.AsyncClient:
    """scope_url (a crawl's start URL): basic auth and the custom headers then go only to that site, not to the
    external links and redirect targets the crawl also checks."""
    client = _make_client(cfg, scoped=bool(scope_url))
    if scope_url:
        client._cp_scope = (scope_url, dict(cfg.custom_headers or {}), (cfg.auth_user, cfg.auth_pass) if cfg.auth_user else None)
    return client


def _make_client(cfg: CrawlConfig, scoped: bool = False) -> httpx.AsyncClient:
    headers = {
        "User-Agent": cfg.user_agent,
        "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,image/avif,image/webp,*/*;q=0.8",
        "Accept-Language": "en-GB,en;q=0.9",
    }
    if not scoped:
        headers.update(cfg.custom_headers or {})
    auth = (cfg.auth_user, cfg.auth_pass) if cfg.auth_user and not scoped else None
    limits = httpx.Limits(max_connections=max(10, cfg.max_threads * 2), max_keepalive_connections=cfg.max_threads * 2)
    timeout = httpx.Timeout(cfg.timeout, connect=min(10.0, cfg.timeout))
    if hosted():   # the transport owns pooling, TLS and HTTP/2 once it is given
        try:
            transport = _PublicOnlyTransport(http2=True, limits=limits, verify=cfg.verify_ssl)
        except ImportError:
            transport = _PublicOnlyTransport(limits=limits, verify=cfg.verify_ssl)
        return httpx.AsyncClient(headers=headers, auth=auth, follow_redirects=False, timeout=timeout, transport=transport)
    try:
        return httpx.AsyncClient(headers=headers, auth=auth, follow_redirects=False, http2=True, limits=limits,
                                 timeout=httpx.Timeout(cfg.timeout, connect=min(10.0, cfg.timeout)), verify=cfg.verify_ssl)
    except ImportError:
        return httpx.AsyncClient(headers=headers, auth=auth, follow_redirects=False, limits=limits,
                                 timeout=httpx.Timeout(cfg.timeout, connect=min(10.0, cfg.timeout)), verify=cfg.verify_ssl)


async def fetch(client: httpx.AsyncClient, url: str, cfg: CrawlConfig, want_body: bool = True, max_bytes: int | None = None) -> FetchResult:
    """GET a URL. want_body=False still reads enough to learn the size cheaply (Content-Length) and closes early."""
    res = FetchResult(url=url)
    limit = max_bytes or cfg.max_page_size_kb * 1024
    start = time.perf_counter()
    attempts = 1 + (cfg.retries_5xx or 0)
    for attempt in range(attempts):
        try:
            scope = getattr(client, "_cp_scope", None)
            if scope and is_internal(url, scope[0], cfg.crawl_subdomains):
                req = client.build_request("GET", url, headers=scope[1] or None)
                resp = await client.send(req, stream=True, auth=scope[2])
            else:
                req = client.build_request("GET", url)
                resp = await client.send(req, stream=True)
            try:
                res.status_code = resp.status_code
                res.reason = resp.reason_phrase or ""
                res.headers = {k.lower(): v for k, v in resp.headers.multi_items()} if hasattr(resp.headers, "multi_items") else dict(resp.headers)
                # keep duplicate Set-Cookie headers
                cookies = [v for k, v in resp.headers.multi_items() if k.lower() == "set-cookie"]
                if cookies:
                    res.headers["set-cookie"] = "\n".join(cookies)
                res.content_type = resp.headers.get("content-type", "")
                res.http_version = resp.http_version
                res.encoding = resp.encoding
                cl = resp.headers.get("content-length")
                if want_body:
                    chunks, total = [], 0
                    async for chunk in resp.aiter_bytes():      # decoded (gzip/br/zstd handled by httpx)
                        total += len(chunk)
                        if total <= limit:
                            chunks.append(chunk)
                        else:
                            res.truncated = True
                            break
                    res.content = b"".join(chunks)
                    res.transferred = resp.num_bytes_downloaded
                    if res.encoding is None:
                        res.encoding = _sniff_charset(res.content, res.content_type)
                else:
                    res.transferred = int(cl) if cl and cl.isdigit() else 0
                    if not cl:
                        async for _ in resp.aiter_raw():
                            if resp.num_bytes_downloaded > limit:
                                break
                        res.transferred = resp.num_bytes_downloaded
            finally:
                await resp.aclose()
            res.elapsed = round(time.perf_counter() - start, 3)
            if res.status_code and res.status_code >= 500 and attempt + 1 < attempts:
                continue
            return res
        except httpx.TimeoutException:
            res.error = "Timeout"
        except httpx.ConnectError as exc:
            res.error = f"Connection error: {exc}" if str(exc) else "Connection error"
        except httpx.RemoteProtocolError as exc:
            res.error = f"Protocol error: {exc}"
        except httpx.HTTPError as exc:
            res.error = f"{type(exc).__name__}: {exc}"
        except Exception as exc:  # noqa: BLE001
            res.error = f"{type(exc).__name__}: {exc}"
        res.elapsed = round(time.perf_counter() - start, 3)
    return res


def _sniff_charset(content: bytes, content_type: str) -> str:
    import re
    m = re.search(r"charset=([\w-]+)", content_type or "", re.I)
    if m:
        return m.group(1)
    head = content[:4096]
    m = re.search(rb'<meta[^>]+charset=["\']?([\w-]+)', head, re.I)
    if m:
        return m.group(1).decode("ascii", "replace")
    return "utf-8"
