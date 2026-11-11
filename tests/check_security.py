"""Security checks for the desktop app: it has no sign-in, so nothing but this machine's own pages may reach it,
and text from crawled sites must stay text.

    .venv\\Scripts\\python.exe tests\\check_security.py
"""
from __future__ import annotations

import gzip
import http.client
import io
import os
import sys
import threading
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
os.environ.pop("SPIDER_API_KEY", None)        # desktop mode
os.environ.pop("SPIDER_ALLOWED_HOSTS", None)
os.environ.setdefault("SPIDER_CRAWLS_DIR", str(Path(os.environ.get("TEMP", ".")) / "claude" / "spider_test_crawls"))
Path(os.environ["SPIDER_CRAWLS_DIR"]).mkdir(parents=True, exist_ok=True)
import uvicorn  # noqa: E402

import spider.server as srv  # noqa: E402
from spider.config import CRAWL_FILE_SECRETS  # noqa: E402
from spider.exports import to_csv, to_xlsx  # noqa: E402
from spider.instance import free_port, wait_ready  # noqa: E402
from spider.robots import gunzip_capped, parse_sitemap  # noqa: E402

FAILS: list[str] = []


def check(cond: bool, label: str, extra: str = "") -> None:
    print(("  ok   " if cond else "  FAIL ") + label + (f"  ({extra})" if extra else ""))
    if not cond:
        FAILS.append(label)


def call(port: int, method: str, path: str, headers: dict | None = None, body: bytes | None = None):
    c = http.client.HTTPConnection("127.0.0.1", port, timeout=30)
    c.putrequest(method, path, skip_host=True)
    h = {"Host": f"127.0.0.1:{port}", **(headers or {})}
    if body is not None:
        h["Content-Length"] = str(len(body))
    for k, v in h.items():
        c.putheader(k, v)
    c.endheaders(body)
    r = c.getresponse()
    data = r.read().decode("utf-8", "replace")
    c.close()
    return r.status, {k.lower(): v for k, v in r.getheaders()}, data


def main() -> None:
    scratch = Path(os.environ.get("TEMP", ".")) / "claude" / "settings_security_test.json"
    srv.SETTINGS_PATH = scratch; srv.State.cfg = srv.CrawlConfig()
    port = free_port()
    threading.Thread(target=lambda: uvicorn.run("spider.server:app", host="127.0.0.1", port=port, log_level="warning"), daemon=True).start()
    assert wait_ready(f"http://127.0.0.1:{port}")
    me = f"http://127.0.0.1:{port}"

    # the Host header: only this machine's own names (DNS rebinding)
    st, hd, _ = call(port, "GET", "/api/crawls")
    check(st == 200, "own address is answered", str(st))
    check(hd.get("x-frame-options") == "DENY" and "frame-ancestors" in hd.get("content-security-policy", ""), "the app cannot be framed by a web page")
    check(hd.get("x-content-type-options") == "nosniff", "nosniff header")
    for host in ("evil.example", f"evil.example:{port}", f"127.0.0.1.evil.example:{port}", ""):
        st, _, _ = call(port, "GET", "/api/config", {"Host": host})
        check(st == 403, f"Host '{host}' is refused", str(st))
    st, _, _ = call(port, "GET", "/api/crawls", {"Host": f"localhost:{port}"})
    check(st == 200, "localhost is answered", str(st))
    os.environ["SPIDER_ALLOWED_HOSTS"] = "spider.example.com"
    st, _, _ = call(port, "GET", "/api/crawls", {"Host": "spider.example.com"})
    check(st == 200, "SPIDER_ALLOWED_HOSTS adds a tunnel's host name", str(st))
    os.environ.pop("SPIDER_ALLOWED_HOSTS")

    # writes from another site (a page open in the user's browser)
    st, _, _ = call(port, "POST", "/api/crawl/stop", {"Origin": "https://evil.example"}, b"")
    check(st == 403, "a POST from another origin is refused", str(st))
    st, _, _ = call(port, "POST", "/api/crawl/stop", {"Origin": "null"}, b"")
    check(st == 403, "a POST from a sandboxed page (Origin: null) is refused", str(st))
    st, _, _ = call(port, "POST", "/api/crawl/stop", {"Sec-Fetch-Site": "cross-site"}, b"")
    check(st == 403, "a cross-site POST without an Origin header is refused", str(st))
    st, _, _ = call(port, "POST", "/api/crawl/stop", {"Origin": me}, b"")
    check(st != 403, "the app's own page may POST", str(st))
    st, _, _ = call(port, "POST", "/api/crawl/stop", None, b"")
    check(st != 403, "a local tool with no Origin (the CLI, MCP) may POST", str(st))

    # the Google callback page prints what it is given as text
    st, _, body = call(port, "GET", "/api/google/callback?error=%3Cscript%3Ealert(1)%3C/script%3E")
    check(st == 400 and "<script>" not in body and "&lt;script&gt;" in body, "callback page escapes the error text", body[body.find("<p>"):][:80])

    # saved keys are masked in the settings API, and the mask coming back leaves them as saved
    import json
    jh = {"Content-Type": "application/json", "Origin": me}
    st, _, _ = call(port, "POST", "/api/config", jh, json.dumps({"ai_api_key": "sk-test-123", "smtp_pass": "pw-1"}).encode())
    st, _, body = call(port, "GET", "/api/config")
    shown = json.loads(body)["config"]
    check(st == 200 and shown["ai_api_key"] == srv.SECRET_MASK and shown["smtp_pass"] == srv.SECRET_MASK and "sk-test-123" not in body and "pw-1" not in body, "saved keys are masked in GET /api/config")
    check(shown["psi_api_key"] == "", "an empty key stays empty")
    st, _, body = call(port, "POST", "/api/config", jh, json.dumps({**shown, "smtp_pass": "pw-2", "max_threads": 3}).encode())
    check(st == 200 and srv.State.cfg.ai_api_key == "sk-test-123" and srv.State.cfg.smtp_pass == "pw-2" and srv.State.cfg.max_threads == 3 and "sk-test-123" not in body,
          "saving the form keeps an unedited key and takes an edited one", f"{st}")
    check(json.loads(scratch.read_text(encoding="utf-8"))["ai_api_key"] == "sk-test-123", "the real key is what is stored on disk")

    tgt = {"name": "Blog", "type": "wordpress", "env": "staging", "base_url": "https://example.com", "username": "editor", "secret": "app-pw-1"}
    st, _, _ = call(port, "POST", "/api/config", jh, json.dumps({"targets": [tgt], "custom_headers": {"X-Staging": "let-me-in", "Accept-Language": "en-GB"}}).encode())
    st, _, body = call(port, "GET", "/api/config")
    shown = json.loads(body)["config"]
    check(st == 200 and "app-pw-1" not in body and "let-me-in" not in body and shown["targets"][0]["secret"] == srv.SECRET_MASK and shown["targets"][0]["username"] == "editor"
          and shown["custom_headers"]["X-Staging"] == srv.SECRET_MASK, "connector secrets and custom header values are masked")
    shown["targets"][0]["name"] = "Blog (staging)"; shown["custom_headers"]["X-New"] = "v2"
    st, _, _ = call(port, "POST", "/api/config", jh, json.dumps(shown).encode())
    c2 = srv.State.cfg
    check(st == 200 and c2.targets[0]["secret"] == "app-pw-1" and c2.targets[0]["name"] == "Blog (staging)" and c2.custom_headers == {"X-Staging": "let-me-in", "Accept-Language": "en-GB", "X-New": "v2"},
          "saving keeps masked connector secrets and header values, even when the target is renamed", f"{st} {c2.custom_headers}")
    st, _, _ = call(port, "POST", "/api/config", jh, json.dumps({"targets": [], "custom_headers": {}}).encode())

    # exports: crawled text never becomes a formula
    csv_text = to_csv(["Title"], [["=HYPERLINK(\"http://x\")"], ["+1"], ["-2"], ["@a"], ["Plain"], [7]]).decode("utf-8")
    check(all(f'"\'{s}' in csv_text for s in ("=HYPERLINK", "+1", "-2", "@a")) and '"Plain"' in csv_text and '"7"' in csv_text, "CSV neutralises formula cells only")
    from openpyxl import load_workbook
    ws = load_workbook(io.BytesIO(to_xlsx([("S", ["Title"], [["=1+1"], ["Plain"], [7]])]))).active
    check(ws["A2"].data_type == "s" and ws["A2"].value == "'=1+1" and ws["A3"].value == "Plain" and ws["A4"].value == 7, "XLSX stores a formula-like title as text", f"{ws['A2'].data_type} {ws['A2'].value}")

    # sitemaps: capped decompression, entities not resolved
    try:
        gunzip_capped(gzip.compress(b"a" * 50_000), 10_000)
        check(False, "decompression is capped")
    except ValueError:
        check(True, "decompression is capped")
    bomb = b'<?xml version="1.0"?><!DOCTYPE u [<!ENTITY a "aaaaaaaaaa"><!ENTITY b "&a;&a;&a;&a;&a;&a;&a;&a;&a;&a;">]><urlset xmlns="http://www.sitemaps.org/schemas/sitemap/0.9"><url><loc>https://example.com/&b;</loc></url><url><loc>https://example.com/ok</loc></url></urlset>'
    res = parse_sitemap("https://example.com/sitemap.xml", bomb)
    check(all("aaaaaaaaaa" not in u for u, _ in res.urls), "XML entities are not expanded", str(res.urls)[:120])
    good = parse_sitemap("https://example.com/sitemap.xml.gz", gzip.compress(b'<urlset xmlns="http://www.sitemaps.org/schemas/sitemap/0.9"><url><loc>https://example.com/a</loc></url></urlset>'))
    check([u for u, _ in good.urls] == ["https://example.com/a"], "a normal gzipped sitemap still parses", str(good.urls))

    # secrets stay out of crawl files; grid tooltips are text
    check(srv.SECRET_KEYS <= CRAWL_FILE_SECRETS and "psi_api_key" in CRAWL_FILE_SECRETS, "every secret setting is kept out of crawl files", str(sorted(srv.SECRET_KEYS - CRAWL_FILE_SECRETS)))
    app_js = (ROOT / "ui" / "app.js").read_text(encoding="utf-8")
    check("tooltip: true" not in app_js and "tooltip: textTip" in app_js, "grid tooltips render crawled text as text")
    from spider.netguard import chromium_no_sandbox
    check(chromium_no_sandbox() is False or hasattr(os, "geteuid"), "Chromium sandbox is on for the desktop app")

    if scratch.exists():
        scratch.unlink()
    print("all checks passed" if not FAILS else f"{len(FAILS)} FAILED: {FAILS}")
    sys.stdout.flush()
    os._exit(1 if FAILS else 0)


if __name__ == "__main__":
    main()
