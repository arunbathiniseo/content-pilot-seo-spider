"""stdio MCP launcher for Content Pilot Spider.

    python mcp_server.py            -> MCP server over stdio (what Claude Desktop / Claude Code / Cursor run)
    python mcp_server.py --tools    -> print the tool list as Markdown and exit

If the desktop app is running it is used as-is (its port is read from .port), so tools act on the crawl you see in
the window. Otherwise a headless copy of the app server is started inside this process and the desktop app will
attach to it if launched later. Prefer the HTTP endpoint (http://127.0.0.1:<port>/mcp, Config > MCP) when the app
is already open; use this launcher for clients that only speak stdio.

Nothing is printed to stdout here except the MCP protocol itself.
"""
from __future__ import annotations

import asyncio
import os
import sys
import threading

ROOT = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, ROOT)

from spider.instance import alive, clear_port, pick_port, running_url, wait_ready, write_port  # noqa: E402


def _serve(port: int) -> None:
    import uvicorn
    uvicorn.run("spider.server:app", host="127.0.0.1", port=port, log_level="warning")


def _ensure_server() -> tuple[str, bool]:
    """URL of an app server, starting a headless one if needed. Second value: whether we started it."""
    url = os.environ.get("SPIDER_URL", "").rstrip("/")
    if url and alive(url):
        return url, False
    url = running_url()
    if url:
        return url, False
    port = pick_port()
    write_port(port)
    threading.Thread(target=_serve, args=(port,), daemon=True).start()
    url = f"http://127.0.0.1:{port}"
    if not wait_ready(url):
        clear_port()
        sys.stderr.write("Content Pilot Spider: the app server failed to start\n")
        sys.exit(1)
    return url, True


def main() -> None:
    import httpx
    from spider.mcp_server import build_mcp, tools_markdown

    if "--tools" in sys.argv:
        srv = build_mcp(lambda: httpx.AsyncClient(base_url="http://127.0.0.1"))
        sys.stdout.write(asyncio.run(tools_markdown(srv)))
        return

    url, started = _ensure_server()
    srv = build_mcp(lambda: httpx.AsyncClient(base_url=url, timeout=httpx.Timeout(900.0, connect=5.0)))
    try:
        srv.run("stdio")
    finally:
        if started:
            clear_port()


if __name__ == "__main__":
    main()
