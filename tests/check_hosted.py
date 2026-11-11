"""Hosted mode end to end: the private-address guard (spider/netguard.py) and the five calls the Content Pilot app
makes on the hosted Spider, as an outside caller with X-Spider-Key: GET /api/crawls, POST /api/crawl/start (with
config.max_urls, and 409 while a crawl runs), GET /api/crawl/status, GET /api/audit?crawl=, GET /api/audit/history?crawl=.
Runs a small live crawl (12 URLs) of the fixture crawl's own site (tests/fixture.py), so it needs the network.

    .venv\\Scripts\\python.exe tests\\check_hosted.py
"""
from __future__ import annotations

import asyncio
import http.server
import json
import os
import sys
import threading
import time
import urllib.error
import urllib.parse
import urllib.request
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
from fixture import fixture_start  # noqa: E402

os.environ["SPIDER_CRAWLS_DIR"] = str(Path(os.environ.get("TEMP", ".")) / "claude" / "spider_test_crawls")   # test crawls never land in the real crawls folder
Path(os.environ["SPIDER_CRAWLS_DIR"]).mkdir(parents=True, exist_ok=True)
KEY = "hosted-test-key"

import uvicorn  # noqa: E402

from spider import netguard  # noqa: E402
from spider.config import CrawlConfig  # noqa: E402
from spider.fetch import fetch, make_client  # noqa: E402
from spider.instance import free_port, wait_ready  # noqa: E402

FAILS: list[str] = []


def check(cond: bool, label: str, extra: str = "") -> None:
    print(("  ok   " if cond else "  FAIL ") + label + (f"  ({extra})" if extra else ""))
    if not cond:
        FAILS.append(label)


class H(http.server.BaseHTTPRequestHandler):
    def do_GET(self):  # noqa: N802
        self.send_response(200)
        self.send_header("Content-Type", "text/html")
        self.end_headers()
        self.wfile.write(b"<html><title>private</title></html>")

    def log_message(self, *a):  # noqa: D102
        pass


def call(base: str, method: str, path: str, body: dict | None = None, key: str | None = KEY) -> tuple[int, dict]:
    """As an outside caller: through a proxy (X-Forwarded-For) with the key, like the Worker behind Caddy."""
    headers = {"X-Forwarded-For": "203.0.113.20", "Content-Type": "application/json"}
    if key:
        headers["X-Spider-Key"] = key
    req = urllib.request.Request(base + path, data=json.dumps(body).encode() if body is not None else None, headers=headers, method=method)
    try:
        with urllib.request.urlopen(req, timeout=120) as r:
            return r.status, json.loads(r.read() or b"{}")
    except urllib.error.HTTPError as exc:
        return exc.code, json.loads(exc.read() or b"{}")


async def guard_checks() -> None:
    print("netguard")
    for host in ("127.0.0.1", "localhost", "api.localhost", "10.0.0.1", "172.16.5.4", "192.168.1.1", "169.254.169.254", "100.64.0.1",
                 "0.0.0.0", "::1", "[::1]", "::ffff:127.0.0.1", "fe80::1", "fc00::1", "224.0.0.1"):
        check(not await netguard.public_host(host), f"{host} is not public")
    for host in ("1.1.1.1", "8.8.8.8", "2606:4700:4700::1111"):
        check(await netguard.public_host(host), f"{host} is public")
    os.environ.pop("SPIDER_API_KEY", None)
    check(await netguard.refuse_reason("http://127.0.0.1/") == "", "desktop app (no key): no guard")

    srv = http.server.ThreadingHTTPServer(("127.0.0.1", 0), H)
    threading.Thread(target=srv.serve_forever, daemon=True).start()
    url = f"http://127.0.0.1:{srv.server_address[1]}/"
    cfg = CrawlConfig()
    c = make_client(cfg)
    r = await fetch(c, url, cfg); await c.aclose()
    check(r.status_code == 200, "desktop app fetches a local page", str(r.status_code))
    os.environ["SPIDER_API_KEY"] = KEY
    c = make_client(cfg)
    r = await fetch(c, url, cfg); await c.aclose()
    check(r.status_code is None and "Blocked" in r.error, "hosted: the crawl client refuses a loopback page", r.error[:80])
    srv.shutdown()


def main() -> None:
    asyncio.run(guard_checks())
    os.environ["SPIDER_API_KEY"] = KEY
    from spider import server as srv
    port = free_port()
    threading.Thread(target=lambda: uvicorn.run(srv.app, host="127.0.0.1", port=port, log_level="warning"), daemon=True).start()
    base = f"http://127.0.0.1:{port}"
    assert wait_ready(base + "/ui/index.html") or True
    time.sleep(1.0)

    print("hosted API, as the Worker calls it")
    check(call(base, "GET", "/api/crawls", key=None)[0] == 401, "no key: 401")
    for target in ("http://127.0.0.1:2019/config/", "http://localhost:7743/api/config", "http://169.254.169.254/latest/meta-data/",
                   "http://10.0.0.1/", "http://[::1]:7743/"):
        st, d = call(base, "POST", "/api/crawl/start", {"start_url": target, "mode": "spider", "list_urls": [], "config": {"max_urls": 5}})
        check(st == 400 and ("not a public website" in d.get("detail", "") or "does not look like a website" in d.get("detail", "")), f"start refused: {target}", f"{st} {d.get('detail', '')[:70]}")
    st, d = call(base, "POST", "/api/crawl/start", {"start_url": "", "mode": "list", "list_urls": ["https://example.com/", "http://192.168.0.10/"], "config": {"max_urls": 5}})
    check(st == 400 and "192.168.0.10" in d.get("detail", ""), "list mode: one private URL refuses the whole list", d.get("detail", "")[:70])
    st, d = call(base, "POST", "/api/crawl/url", {"url": "http://127.0.0.1:2019/config/", "expect": []})
    check(st == 400, "one-page fetch refused for loopback", str(st))

    site = fixture_start()
    print("pacing and the crawl queue")
    # the test crawls at the hosted ceiling (2 URLs a second) so it finishes in seconds; the default is checked on a job that never runs
    st, d = call(base, "POST", "/api/crawl/start", {"start_url": site, "mode": "spider", "list_urls": [], "config": {"max_urls": 12, "max_urls_per_second": 50, "max_threads": 9}})
    check(st == 200 and d.get("ok") and d.get("db") and d.get("queued") is False and d.get("id") and d.get("state") == "running", "start a public crawl with max_urls 12: runs at once", f"{site} -> {d.get('crawl')}")
    check(d.get("max_urls_per_second") == srv.HOSTED_RATE_MAX and d.get("max_threads") == srv.HOSTED_THREADS_MAX, "a faster pace is clamped to the hosted ceiling", f"{d.get('max_urls_per_second')}/s, {d.get('max_threads')} connections")
    name, first = Path(d.get("db", "")).name, d.get("id")
    st2, d2 = call(base, "POST", "/api/crawl/start", {"start_url": site, "mode": "spider", "list_urls": [], "config": {"max_urls": 3, "max_urls_per_second": 2}})
    check(st2 == 202 and d2.get("queued") is True and d2.get("position") == 1 and d2.get("state") == "queued", "second start while running: 202, queued at position 1", f"{st2} {d2.get('position')}")
    second = d2.get("id")
    st3, d3 = call(base, "POST", "/api/crawl/start", {"start_url": site, "mode": "spider", "list_urls": [], "config": {"max_urls": 3}})
    check(st3 == 202 and d3.get("duplicate") is True and d3.get("id") == second, "the same site again: the waiting job, not a new one", f"{d3.get('id')} vs {second}")
    st4, d4 = call(base, "POST", "/api/crawl/start", {"start_url": "https://example.com/", "mode": "spider", "list_urls": [], "config": {"max_urls": 3}})
    check(st4 == 202 and d4.get("position") == 2, "another site queues behind it", str(d4.get("position")))
    check(d4.get("max_urls_per_second") == srv.HOSTED_RATE and d4.get("max_threads") == 1, "no pace asked: the hosted default, one connection", f"{d4.get('max_urls_per_second')}/s, {d4.get('max_threads')}")
    _, s0 = call(base, "GET", "/api/crawl/status")
    check(s0.get("jobs", {}).get("running") == first and [w["id"] for w in s0["jobs"]["waiting"]] == [second, d4.get("id")], "status names the running job and the waiting ones in order")
    st, c4 = call(base, "POST", f"/api/crawl/jobs/{d4.get('id')}/cancel")
    check(st == 200 and c4.get("state") == "cancelled", "a waiting job can be cancelled")
    st, j1 = call(base, "GET", f"/api/crawl/jobs/{first}")
    check(st == 200 and j1.get("state") == "running" and "progress" in j1, "a job read by id, with progress while it runs")
    check(call(base, "GET", "/api/crawl/jobs/nope")[0] == 404, "an unknown job: 404")
    j1 = {}
    for _ in range(240):
        _, j1 = call(base, "GET", f"/api/crawl/jobs/{first}")
        if j1.get("state") not in ("queued", "running"):
            break
        time.sleep(1)
    check(j1.get("state") == "finished" and j1.get("crawl") == name, "the first job finished", f"{j1.get('crawled')} URLs")
    j2 = {}
    for _ in range(240):
        _, j2 = call(base, "GET", f"/api/crawl/jobs/{second}")
        if j2.get("state") not in ("queued", "running"):
            break
        time.sleep(1)
    check(j2.get("state") == "finished" and j2.get("crawl") and j2.get("crawl") != name and 0 < j2.get("crawled", 0) <= 3, "the queue ran the next job by itself", f"{j2.get('crawl')}: {j2.get('crawled')} URLs")
    _, s = call(base, "GET", "/api/crawl/status")
    check(s.get("jobs", {}).get("running") is None and not s["jobs"]["waiting"], "queue empty afterwards")
    _, jl = call(base, "GET", "/api/crawl/jobs")
    check([j["id"] for j in jl.get("jobs", [])][:3] == [d4.get("id"), second, first], "job list newest first", str([j["state"] for j in jl.get("jobs", [])][:3]))
    _, s1 = call(base, "GET", f"/api/crawl/jobs/{first}")
    check(0 < j1.get("crawled", 0) <= 12, "max_urls honoured", str(j1.get("crawled")))
    _, cl = call(base, "GET", "/api/crawls")
    row = next((c for c in cl.get("crawls", []) if c["name"] == name), None)
    # the list's `urls` also counts the sitemap files the crawl read to discover pages, so it can sit a little above max_urls
    check(row is not None and row["state"] == "finished" and row["start_url"].rstrip("/") == site.rstrip("/") and 0 < row["urls"] <= 12 + 10, "crawl list carries it", str(row and {k: row[k] for k in ("state", "urls", "start_url")}))
    st, a = call(base, "GET", f"/api/audit?crawl={urllib.parse.quote(name)}")
    check(st == 200 and isinstance(a.get("health"), (int, float)) and {"tally", "pages", "themes", "top_issues"} <= set(a), "audit of the named crawl", f"health {a.get('health')}")
    st, h = call(base, "GET", f"/api/audit/history?crawl={urllib.parse.quote(name)}")
    check(st == 200 and any(p["crawl"] == name and p["current"] for p in h.get("points", [])), "history of the named crawl", f"{len(h.get('points', []))} points")
    st, d = call(base, "GET", "/api/audit?crawl=missing.db")
    check(st == 404 and d.get("detail", "").startswith("Crawl file not found"), "unknown crawl: 404", str(d))


if __name__ == "__main__":
    main()
    print("\nall checks passed" if not FAILS else f"\n{len(FAILS)} FAILED: {FAILS}")
    sys.exit(1 if FAILS else 0)
