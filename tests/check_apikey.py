"""Hosted-mode check: with SPIDER_API_KEY set, every route (API, UI, /mcp) needs X-Spider-Key, except the app's own
in-process clients and a browser on the box itself (loopback with no forwarding header). Without the key set, nothing
changes for the desktop app. The app's browser sign-in: a signed token of an hour at most opens the UI through a
cookie that never unlocks /mcp, and writes signed in that way need this host's Origin.

    .venv\\Scripts\\python.exe tests\\check_apikey.py
"""
from __future__ import annotations

import asyncio
import os
import sys
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
os.environ.setdefault("SPIDER_CRAWLS_DIR", str(ROOT / "tests"))

import httpx  # noqa: E402

from spider import server  # noqa: E402

FAILS: list[str] = []


def check(cond: bool, label: str, extra: str = "") -> None:
    print(("  ok   " if cond else "  FAIL ") + label + (f"  ({extra})" if extra else ""))
    if not cond:
        FAILS.append(label)


async def status(client_addr: tuple[str, int], path: str, headers: dict[str, str] | None = None, base: str = "http://spider.local") -> int:
    transport = httpx.ASGITransport(app=server.app, client=client_addr)
    async with httpx.AsyncClient(transport=transport, base_url=base) as c:
        return (await c.get(path, headers=headers or {})).status_code


async def main() -> None:
    remote = ("203.0.113.7", 40000)
    local = ("127.0.0.1", 123)

    print("no key set (desktop app)")
    os.environ.pop("SPIDER_API_KEY", None)
    own = "http://127.0.0.1:7743"
    check(await status(local, "/api/crawls", base=own) == 200, "API open on this machine's own address")
    check(await status(local, "/ui/index.html", base=own) == 200, "UI open")
    check(await status(local, "/?token=anything", base=own) == 200, "a token means nothing without a key")
    check(await status(remote, "/api/crawls") == 403, "another host name is refused (DNS rebinding)")

    print("key set (hosted)")
    os.environ["SPIDER_API_KEY"] = "s3cret-key"
    try:
        for path in ("/api/crawls", "/api/config", "/ui/index.html", "/", "/mcp", "/api/graph?crawl=smoke.db"):
            check(await status(remote, path) == 401, f"{path} refused without the key")
        check(await status(remote, "/api/crawls", {"X-Spider-Key": "wrong"}) == 401, "wrong key refused")
        check(await status(remote, "/api/crawls", {"X-Spider-Key": "s3cret-key"}) == 200, "right key accepted")
        check(await status(remote, "/ui/index.html", {"X-Spider-Key": "s3cret-key"}) == 200, "UI with the key")
        check(await status(local, "/api/crawls") == 200, "in-process / SSH-forwarded caller trusted")
        check(await status(local, "/api/crawls", {"X-Forwarded-For": "198.51.100.4"}) == 401, "proxied request from loopback still needs the key")
        check(await status(local, "/api/crawls", {"CF-Connecting-IP": "198.51.100.4"}) == 401, "Cloudflare header from loopback still needs the key")
        check(await status(("::1", 123), "/api/crawls") == 200, "IPv6 loopback trusted")

        print("browser sign-in from the app")
        now = int(time.time())
        good = server.make_ui_token("s3cret-key", "uid-1", now + 3600)
        transport = httpx.ASGITransport(app=server.app, client=remote)
        async with httpx.AsyncClient(transport=transport, base_url="http://spider.local") as c:
            r = await c.get(f"/?token={good}")
            sc = r.headers.get("set-cookie", "")
            check(r.status_code == 303 and r.headers.get("location") == "/", "/?token= redirects to / so the token leaves the address", str(r.status_code))
            check(sc.startswith(f"{server.UI_COOKIE}=") and "HttpOnly" in sc and "samesite=lax" in sc.lower(), "sets an HttpOnly SameSite=Lax cookie", sc[:80])
            check(r.headers.get("cache-control") == "no-store", "sign-in answer is not cached")
            check((await c.get("/")).status_code == 200, "UI opens with the cookie")
            check((await c.get("/api/crawls")).status_code == 200, "API reads with the cookie")
            check((await c.get("/mcp")).status_code == 401, "the cookie does not unlock /mcp")
            check((await c.post("/mcp", json={})).status_code == 401, "nor a POST to /mcp")
            check((await c.post("/api/crawl/url", json={"url": ""})).status_code == 401, "a write without Origin is refused")
            check((await c.post("/api/crawl/url", json={"url": ""}, headers={"Origin": "https://other.example"})).status_code == 401, "a write from another origin is refused")
            check((await c.post("/api/crawl/url", json={"url": ""}, headers={"Origin": "https://bing.spider.local"})).status_code == 401, "a write from a sibling subdomain is refused")
            w = await c.post("/api/crawl/url", json={"url": ""}, headers={"Origin": "http://spider.local"})
            check(w.status_code not in (401, 403), "a write from the Spider's own page passes the gate", str(w.status_code))
        async with httpx.AsyncClient(transport=transport, base_url="http://spider.local") as c:
            r = await c.get(f"/auth/ui?token={good}")
            check(r.status_code == 204 and f"{server.UI_COOKIE}=" in r.headers.get("set-cookie", ""), "/auth/ui refreshes the cookie with 204", str(r.status_code))
            check((await c.get("/api/crawls")).status_code == 200, "refreshed cookie reads")

        bad = {
            "expired": server.make_ui_token("s3cret-key", "uid-1", now - 5),
            "longer than an hour": server.make_ui_token("s3cret-key", "uid-1", now + 3 * 3600),
            "signed with another key": server.make_ui_token("other-key", "uid-1", now + 600),
            "tampered uid": good.replace(good.split(".")[0], "dWlkLTI", 1),
            "the API key itself": "s3cret-key",
            "garbage": "a.b.c",
        }
        for label, tok in bad.items():
            async with httpx.AsyncClient(transport=transport, base_url="http://spider.local") as c:
                r = await c.get(f"/?token={tok}")
                check(r.status_code == 401 and "set-cookie" not in r.headers, f"token {label}: refused, no cookie", str(r.status_code))
                c.cookies.set(server.UI_COOKIE, tok)
                check((await c.get("/api/crawls")).status_code == 401, f"cookie {label}: refused")
    finally:
        os.environ.pop("SPIDER_API_KEY", None)


if __name__ == "__main__":
    asyncio.run(main())
    print("\nall checks passed" if not FAILS else f"\n{len(FAILS)} FAILED: {FAILS}")
    sys.exit(1 if FAILS else 0)
