"""Quality gate: run every check_*.py and summarise. Exit code 1 if any suite fails.

Only check_*.py files are suites (they exit non-zero on failure). The inspect_*.py scripts next to them are ad hoc
data explorers from phase 1 and are not run here.

    .venv\\Scripts\\python.exe tests\\run_all.py [--quick]      (--quick skips the suites that run live crawls or Lighthouse)
"""
from __future__ import annotations

import os
import shutil
import subprocess
import sys
import tempfile
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
SLOW = {"check_lighthouse.py", "check_schedule.py", "check_email.py", "check_structured.py", "check_usage.py", "check_windowless.py", "check_hosted.py"}
WINDOWS_ONLY = {"check_windowless.py"}   # pythonw and console windows; the hosted Linux Spider has neither


def main() -> int:
    quick = "--quick" in sys.argv
    os.environ.setdefault("TEMP", tempfile.gettempdir())   # the suites put scratch files under %TEMP%; Linux has no TEMP by default
    scratch = Path(os.environ.get("TEMP", ".")) / "claude" / "spider_test_crawls"   # where the suites put their test crawls
    shutil.rmtree(scratch, ignore_errors=True)
    scratch.parent.mkdir(parents=True, exist_ok=True)   # %TEMP%\claude exists on the laptop; a fresh server has no such folder
    suites = sorted(p for p in (ROOT / "tests").glob("check_*.py"))
    results = []
    for s in suites:
        if (quick and s.name in SLOW) or (os.name != "nt" and s.name in WINDOWS_ONLY):
            continue
        t0 = time.time()
        r = subprocess.run([sys.executable, "-X", "utf8", str(s)], capture_output=True, text=True, cwd=str(ROOT), timeout=1800)
        lines = [l for l in r.stdout.splitlines() if l.startswith("  FAIL") or "FAILED" in l or "all checks passed" in l]
        ok = r.returncode == 0
        results.append((s.name, ok, round(time.time() - t0), lines))
        print(f"{'PASS' if ok else 'FAIL'}  {s.name:<26} {round(time.time() - t0):>4}s" + ("" if ok else "\n       " + "\n       ".join(lines[-6:] or [r.stderr.strip().splitlines()[-1] if r.stderr.strip() else "no output"])))
    failed = [n for n, ok, _, _ in results if not ok]
    print(f"\n{len(results) - len(failed)} of {len(results)} suites passed" + (f"; failed: {', '.join(failed)}" if failed else ""))
    return 1 if failed else 0


if __name__ == "__main__":
    sys.exit(main())
