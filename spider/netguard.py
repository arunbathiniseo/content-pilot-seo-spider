"""Hosted mode only: the Spider must not fetch private, loopback, link-local or other non-public addresses.

On the hosted box, loopback holds the Spider itself (trusted without the key from 127.0.0.1) and Caddy's admin API,
and a crawl of those would copy them into a crawl file the app can read. The desktop app keeps crawling intranet
and localhost sites as before: the guard is on only when SPIDER_API_KEY is set. Used by fetch.make_client (every
crawl request, redirect hops included, since the client does not follow redirects itself), render.py (everything
Chromium loads) and the crawl-start and one-page endpoints. The VPS firewall also refuses these ranges for the
spider user, which covers DNS rebinding (contentpilot/docs/SPIDER-HOSTING.md, Site audit plan step 3).
"""
from __future__ import annotations

import asyncio
import ipaddress
import os
import socket
from urllib.parse import urlsplit


def hosted() -> bool:
    return bool(os.environ.get("SPIDER_API_KEY"))


def chromium_no_sandbox() -> bool:
    """Chromium's sandbox stays on for the desktop app, which loads pages from sites it does not control. It is off
    only where it cannot start: the hosted box, a root user, or SPIDER_NO_SANDBOX=1 (containers)."""
    return hosted() or os.environ.get("SPIDER_NO_SANDBOX") == "1" or (hasattr(os, "geteuid") and os.geteuid() == 0)


def _public_ip(ip: str) -> bool:
    a = ipaddress.ip_address(ip.split("%", 1)[0])
    if isinstance(a, ipaddress.IPv6Address) and a.ipv4_mapped:
        a = a.ipv4_mapped
    return a.is_global and not a.is_multicast


async def public_host(host: str) -> bool:
    """True when every address the host resolves to is public. A name that does not resolve counts as public: the
    fetch then fails with its own DNS error, and there is nothing to reach."""
    host = (host or "").strip().strip("[]").rstrip(".")
    if not host:
        return True
    try:
        return _public_ip(host)
    except ValueError:
        pass
    if host.lower() == "localhost" or host.lower().endswith(".localhost"):
        return False
    try:
        infos = await asyncio.get_running_loop().getaddrinfo(host, None, type=socket.SOCK_STREAM)
    except (socket.gaierror, UnicodeError):
        return True
    return all(_public_ip(i[4][0]) for i in infos)


async def refuse_reason(url: str) -> str:
    """'' when the URL may be fetched; otherwise the message for a 400. Always '' outside hosted mode."""
    if not hosted():
        return ""
    host = urlsplit(url).hostname or ""
    if await public_host(host):
        return ""
    return f"'{host}' is not a public website address; the hosted Spider crawls public sites only"
