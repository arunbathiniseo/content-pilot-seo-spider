"""Single-instance plumbing shared by the desktop launcher (run.py) and the stdio MCP launcher (mcp_server.py).

The app binds a fixed port (Config > MCP > App port, default 7743) so MCP clients can find it, and records the port
it actually got in `.port` next to run.py. A second launcher attaches to a live server instead of starting another.
"""
from __future__ import annotations

import json
import os
import socket
import time
import urllib.request
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
PORT_FILE = ROOT / ".port"
SETTINGS_PATH = ROOT / "settings.json"
DEFAULT_PORT = 7743          # "SPID" on a phone keypad


def settings_port() -> int:
    try:
        v = int(json.loads(SETTINGS_PATH.read_text(encoding="utf-8")).get("app_port") or DEFAULT_PORT)
        return v if 1024 <= v <= 65535 else DEFAULT_PORT
    except (OSError, ValueError, TypeError, AttributeError):
        return DEFAULT_PORT


def port_free(port: int) -> bool:
    with socket.socket() as s:
        try:
            s.bind(("127.0.0.1", port))
            return True
        except OSError:
            return False


def free_port() -> int:
    with socket.socket() as s:
        s.bind(("127.0.0.1", 0))
        return s.getsockname()[1]


def pick_port() -> int:
    env = os.environ.get("SEO_SPIDER_PORT")
    if env and env.isdigit() and port_free(int(env)):
        return int(env)
    want = settings_port()
    return want if port_free(want) else free_port()


def alive(url: str, timeout: float = 1.5) -> bool:
    """True when a Content Pilot Spider server answers at `url`."""
    try:
        with urllib.request.urlopen(url.rstrip("/") + "/api/system/check", timeout=timeout) as r:
            return "crawls_dir" in json.loads(r.read().decode("utf-8", "replace"))
    except Exception:  # noqa: BLE001 - any failure means "not ours / not up"
        return False


def running_url(grace: float = 8.0) -> str | None:
    """URL of an already running app (from .port), or None. A .port file written in the last few seconds belongs to
    a server that is still booting, so give it `grace` seconds to answer before giving up."""
    try:
        d = json.loads(PORT_FILE.read_text(encoding="utf-8"))
        url = f"http://127.0.0.1:{int(d['port'])}"
        fresh = time.time() - PORT_FILE.stat().st_mtime < 30
    except (OSError, ValueError, KeyError, TypeError):
        return None
    end = time.time() + (grace if fresh else 0)
    while True:
        if alive(url):
            return url
        if time.time() >= end:
            return None
        time.sleep(0.3)


def write_port(port: int) -> None:
    PORT_FILE.write_text(json.dumps({"port": port, "pid": os.getpid(), "started": time.strftime("%Y-%m-%d %H:%M:%S")}), encoding="utf-8")


def clear_port() -> None:
    """Remove .port only if this process wrote it."""
    try:
        if json.loads(PORT_FILE.read_text(encoding="utf-8")).get("pid") == os.getpid():
            PORT_FILE.unlink()
    except (OSError, ValueError):
        pass


def wait_ready(url: str, timeout: float = 90.0) -> bool:
    end = time.time() + timeout
    while time.time() < end:
        if alive(url, timeout=1.0):
            return True
        time.sleep(0.2)
    return False
