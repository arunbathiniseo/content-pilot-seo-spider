"""Desktop launcher: starts the local API server and opens it in a native window (Edge WebView2 via pywebview).

    python run.py            -> desktop window
    python run.py --browser  -> open in the default browser instead

The server binds the fixed port from Config > MCP (default 7743, so MCP clients can find it) and records the port in
`.port`. If a server is already running (e.g. started headless by mcp_server.py) this launcher just opens a window on
it instead of starting a second copy.
"""
from __future__ import annotations

import os
import sys
import threading
import time

import uvicorn

ROOT = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, ROOT)

# Under pythonw.exe (the .bat / shortcut) there is no console: stdout/stderr are None and any library that logs to them
# kills the thread doing it. Send everything to a log file instead.
if sys.stdout is None or sys.stderr is None:
    _log = open(os.path.join(ROOT, "spider.log"), "a", encoding="utf-8", buffering=1)
    sys.stdout = sys.stdout or _log
    sys.stderr = sys.stderr or _log

from spider.instance import clear_port, pick_port, running_url, wait_ready, write_port  # noqa: E402


def serve(port: int):
    uvicorn.run("spider.server:app", host="127.0.0.1", port=port, log_level="warning")


def main():
    url = running_url()
    if url is None:
        port = pick_port()
        write_port(port)
        url = f"http://127.0.0.1:{port}"
        t = threading.Thread(target=serve, args=(port,), daemon=True)
        t.start()
        if not wait_ready(url):
            clear_port()
            print("Server failed to start")
            sys.exit(1)
    if "--browser" in sys.argv:
        import webbrowser
        webbrowser.open(url)
        print(f"Content Pilot Spider running at {url}  (Ctrl+C to quit)")
        try:
            while True:
                time.sleep(3600)
        except KeyboardInterrupt:
            pass
        clear_port()
        return
    import webview
    # pywebview cancels all downloads by default, which silently broke every CSV / Excel export in the window.
    webview.settings["ALLOW_DOWNLOADS"] = True
    webview.create_window("Content Pilot Spider", url, width=1540, height=960, min_size=(1100, 700))
    webview.start(set_window_icon, private_mode=False)
    clear_port()
    os._exit(0)


def set_window_icon():
    """Windows: pywebview has no icon option, so set it on the native window once it exists (title bar + taskbar)."""
    if not sys.platform.startswith("win"):
        return
    ico = os.path.join(ROOT, "ui", "icon.ico")
    if not os.path.exists(ico):
        return
    try:
        import ctypes
        u32 = ctypes.windll.user32
        IMAGE_ICON, LR_LOADFROMFILE, WM_SETICON = 1, 0x10, 0x80
        small = u32.LoadImageW(None, ico, IMAGE_ICON, 16, 16, LR_LOADFROMFILE)
        big = u32.LoadImageW(None, ico, IMAGE_ICON, 32, 32, LR_LOADFROMFILE)
        for _ in range(50):
            hwnd = u32.FindWindowW(None, "Content Pilot Spider")
            if hwnd:
                u32.SendMessageW(hwnd, WM_SETICON, 0, small)
                u32.SendMessageW(hwnd, WM_SETICON, 1, big)
                return
            time.sleep(0.2)
    except Exception:  # noqa: BLE001 - cosmetic only
        pass


if __name__ == "__main__":
    main()

