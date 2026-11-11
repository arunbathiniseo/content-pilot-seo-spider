"""Build the Windows download: a folder that runs on a PC with nothing installed, and the installer made from it.

    .venv\\Scripts\\python.exe packaging\\build.py --python-embed C:\\path\\python-3.12.x-embed-amd64.zip [--iscc C:\\path\\ISCC.exe]

What goes in the folder (dist\\stage\\Content Pilot Spider):
  - the app as it is in the repository (run.py, cli.py, mcp_server.py, spider\\, ui\\), without tests and docs
  - python\\: the official Python "embeddable" runtime, the same minor version as .venv, plus .venv's site-packages
  - ui\\vendor\\node_modules: only the three libraries the interface loads (Tabulator, D3, d3-cloud)

The app keeps its settings and crawls next to itself, so the installer puts it in the user's own programs folder
(no administrator rights, and the folder stays writable). Lighthouse audits on the installed app use the PageSpeed
Insights API unless Node.js and `npm install` in ui\\vendor are added by hand.

With --iscc (Inno Setup 6's command-line compiler) the installer is written to dist\\ContentPilotSpider-Setup-<version>.exe;
without it the folder is zipped to dist\\ContentPilotSpider-<version>-portable.zip only.
"""
from __future__ import annotations

import argparse
import os
import shutil
import subprocess
import sys
import tempfile
import zipfile
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
DIST = ROOT / "dist"
APP_NAME = "Content Pilot Spider"
VERSION = (ROOT / "packaging" / "VERSION").read_text(encoding="utf-8").strip()

APP_FILES = ["run.py", "cli.py", "mcp_server.py", "LICENSE", "README.md", "SECURITY.md", "requirements.txt"]
APP_DIRS = ["spider", "ui"]
UI_LIBS = ["tabulator-tables/dist", "tabulator-tables/package.json", "tabulator-tables/LICENSE", "d3/dist", "d3/package.json", "d3/LICENSE",
           "d3-cloud/build", "d3-cloud/package.json", "d3-cloud/LICENSE"]
SKIP_PACKAGES = {"pip", "setuptools", "wheel", "_distutils_hack", "pkg_resources", "pyinstaller", "PyInstaller"}


def ignore(_dir: str, names: list[str]) -> set[str]:
    return {n for n in names if n in ("__pycache__", "node_modules", ".pytest_cache") or n.endswith((".pyc", ".pyo"))}


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--python-embed", required=True, help="python-3.x.y-embed-amd64.zip from python.org")
    ap.add_argument("--iscc", default="", help="path to Inno Setup's ISCC.exe")
    a = ap.parse_args()

    stage = DIST / "stage" / APP_NAME
    if stage.exists():
        shutil.rmtree(stage)
    stage.mkdir(parents=True)

    for f in APP_FILES:
        shutil.copy2(ROOT / f, stage / f)
    for d in APP_DIRS:
        shutil.copytree(ROOT / d, stage / d, ignore=ignore)
    nm = ROOT / "ui" / "vendor" / "node_modules"
    for rel in UI_LIBS:
        src, dst = nm / rel, stage / "ui" / "vendor" / "node_modules" / rel
        if not src.exists():
            if rel.endswith("LICENSE"):
                continue
            raise SystemExit(f"missing {src}: run  npm install  in ui\\vendor first")
        dst.parent.mkdir(parents=True, exist_ok=True)
        shutil.copytree(src, dst) if src.is_dir() else shutil.copy2(src, dst)

    # the Python runtime
    py = stage / "python"
    with zipfile.ZipFile(a.python_embed) as z:
        z.extractall(py)
    pth = next(py.glob("python3*._pth"))
    want = f"python{sys.version_info.major}{sys.version_info.minor}"
    if pth.stem != want:
        raise SystemExit(f"the embeddable runtime is {pth.stem} but .venv is {want}: compiled packages would not load")
    # an embeddable runtime ignores the script's folder and PYTHONPATH: its ._pth file is the whole search path
    pth.write_text(f"{pth.stem}.zip\n.\nLib\\site-packages\n..\nimport site\n", encoding="ascii")
    sp_src = Path(sys.prefix) / "Lib" / "site-packages"
    sp_dst = py / "Lib" / "site-packages"
    sp_dst.mkdir(parents=True)
    for item in sorted(sp_src.iterdir()):
        base = item.name.split("-")[0].lower()
        if item.name in SKIP_PACKAGES or base in {s.lower() for s in SKIP_PACKAGES} or item.name.startswith("~") or item.name in ("__pycache__", "distutils-precedence.pth"):
            continue
        (shutil.copytree(item, sp_dst / item.name, ignore=lambda d, n: {x for x in n if x == "__pycache__" or x.endswith(".pyc")})
         if item.is_dir() else shutil.copy2(item, sp_dst / item.name))

    # double-click launchers for the portable folder (the installer makes its own shortcuts)
    (stage / f"{APP_NAME}.bat").write_text('@echo off\r\ncd /d "%~dp0"\r\nstart "" "%~dp0python\\pythonw.exe" "%~dp0run.py"\r\n', encoding="ascii", newline="")
    (stage / "Install browser for JavaScript rendering.bat").write_text(
        '@echo off\r\ncd /d "%~dp0"\r\necho Downloading Chromium for JavaScript rendering and PageSpeed audits (about 150 MB)...\r\n'
        '"%~dp0python\\python.exe" -m playwright install chromium\r\necho.\r\necho Done. You can close this window.\r\npause\r\n', encoding="ascii", newline="")

    size = sum(f.stat().st_size for f in stage.rglob("*") if f.is_file())
    print(f"staged {stage}  ({size / 1048576:.0f} MB, {sum(1 for _ in stage.rglob('*'))} entries)")

    # smoke test with the bundled runtime only
    r = subprocess.run([str(py / "python.exe"), "-c",
                        "import sys, spider.server, spider.render, webview, playwright, uvicorn, lxml, httpx, openpyxl, PIL; "
                        "print('runtime ok', sys.version.split()[0], 'from', sys.prefix)"], cwd=str(stage), capture_output=True, text=True,
                       env={**os.environ, "PYTHONDONTWRITEBYTECODE": "1", "SPIDER_CRAWLS_DIR": tempfile.mkdtemp(prefix="spider_build_")})   # leave nothing behind in the folder
    print((r.stdout or r.stderr).strip()[-600:])
    if r.returncode:
        return 1

    if a.iscc:
        r = subprocess.run([a.iscc, f"/DAppVersion={VERSION}", f"/DStageDir={stage}", f"/DOutDir={DIST}", str(ROOT / "packaging" / "spider.iss")])
        if r.returncode:
            return r.returncode
        print("installer:", next(DIST.glob(f"ContentPilotSpider-Setup-{VERSION}.exe")))
    else:
        out = DIST / f"ContentPilotSpider-{VERSION}-portable"
        zip_path = Path(str(out) + ".zip")   # not with_suffix: the version's dots would be taken for a suffix
        if zip_path.exists():
            zip_path.unlink()
        shutil.make_archive(str(out), "zip", root_dir=stage.parent, base_dir=APP_NAME)
        print("portable zip:", zip_path, f"({zip_path.stat().st_size / 1048576:.0f} MB)")
    return 0


if __name__ == "__main__":
    sys.exit(main())
