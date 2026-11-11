"""Windows regression test: run the server exactly as the desktop app does (pythonw, no console), run a Lighthouse
audit through it, and fail if any console window appears. Also asserts the UI contains no emoji / pictographs.

    .venv\\Scripts\\python.exe tests\\check_windowless.py
"""
from __future__ import annotations

import json
import os
import re
import subprocess
import sys
import time
import urllib.request
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
from fixture import fixture_crawl, fixture_root  # noqa: E402,F401
from spider.instance import free_port, wait_ready  # noqa: E402

FAILS: list[str] = []


def check(cond: bool, label: str, extra: str = "") -> None:
    print(("  ok   " if cond else "  FAIL ") + label + (f"  ({extra})" if extra else ""))
    if not cond:
        FAILS.append(label)


def visible_console_windows() -> list[str]:   # keyed on process id only: window titles change (a terminal running something else must not count as new)
    cmd = "Get-Process cmd,conhost,node,WindowsTerminal,powershell,pwsh -ErrorAction SilentlyContinue | Where-Object { $_.MainWindowHandle -ne 0 } | ForEach-Object { $_.ProcessName + ':' + $_.Id }"
    out = subprocess.run(["powershell", "-NoProfile", "-Command", cmd], capture_output=True, text=True, creationflags=0x08000000).stdout
    return [l.strip() for l in out.splitlines() if l.strip()]


def main() -> None:
    # 1. no emoji in the interface
    glyph = re.compile(r"[⌀-⏿①-➿⤀-⧿⬀-⯿\U0001F000-\U0001FAFF■-◿]")
    for f in ("ui/index.html", "ui/app.js", "ui/styles.css"):
        found = sorted(set(glyph.findall((ROOT / f).read_text(encoding="utf-8"))))
        check(not found, f"{f} has no emoji / pictographs", "".join(found))
    if os.name != "nt":
        print("windowless check is Windows-only"); sys.exit(1 if FAILS else 0)

    # 2. windowless server + Lighthouse: no console windows may appear
    pythonw = Path(sys.executable).with_name("pythonw.exe")
    port = free_port()
    before = set(visible_console_windows())
    log = Path(os.environ.get("TEMP", ".")) / "claude" / "windowless_server.log"
    code = (f"import sys; sys.path.insert(0, r'{ROOT}'); f = open(r'{log}', 'a', encoding='utf-8', buffering=1); sys.stdout = sys.stderr = f; "   # pythonw has no console streams, as run.py handles
            f"import uvicorn; uvicorn.run('spider.server:app', host='127.0.0.1', port={port}, log_level='warning')")
    proc = subprocess.Popen([str(pythonw), "-c", code], cwd=str(ROOT))
    try:
        url = f"http://127.0.0.1:{port}"
        check(wait_ready(url), "windowless server (pythonw) starts")
        db = fixture_crawl()
        urllib.request.urlopen(urllib.request.Request(f"{url}/api/crawls/open", data=json.dumps({"path": str(db)}).encode(), headers={"Content-Type": "application/json"})).read()
        rows = json.loads(urllib.request.urlopen(f"{url}/api/urls?key=internal:html&size=2").read())["data"]
        r = json.loads(urllib.request.urlopen(urllib.request.Request(f"{url}/api/lighthouse/run", data=json.dumps({"ids": [rows[0]["id"]]}).encode(), headers={"Content-Type": "application/json"})).read())
        check(r["ok"], "Lighthouse run started from the windowless server")
        seen: set[str] = set()
        for _ in range(60):
            st = json.loads(urllib.request.urlopen(f"{url}/api/lighthouse/status").read())
            seen |= set(visible_console_windows()) - before
            if st["state"] in ("done", "error"):
                break
            time.sleep(1)
        check(st["state"] == "done" and st["ok"] == 1, "Lighthouse finished", str(st["errors"][:1]))
        check(not seen, "no console window appeared during the audit", "; ".join(sorted(seen))[:200])
        after = set(visible_console_windows()) - before
        check(not after, "no console window left behind", "; ".join(sorted(after))[:200])
    finally:
        proc.kill()
    print(f"{len(FAILS)} FAILED: {FAILS}" if FAILS else "all checks passed")
    sys.exit(1 if FAILS else 0)


if __name__ == "__main__":
    main()
