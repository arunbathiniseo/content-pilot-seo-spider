"""Scheduling + CLI check: next-run maths, due detection, API CRUD, a schedule run (small live crawl + export +
compare), the CLI (crawl / list / issues / schedule) and the Schedules modal headless.

    .venv\\Scripts\\python.exe tests\\check_schedule.py
"""
from __future__ import annotations

import json
import os
import subprocess
import sys
import threading
import time
import urllib.request
from datetime import datetime, timedelta
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
from urllib.parse import urlsplit  # noqa: E402
from fixture import fixture_start  # noqa: E402
LIVE_SITE = fixture_start()   # the live site the fixture crawl came from, for the small live crawls
LIVE_HOST = urlsplit(LIVE_SITE).netloc
os.environ.setdefault("SPIDER_CRAWLS_DIR", str(Path(os.environ.get("TEMP", ".")) / "claude" / "spider_test_crawls"))   # test crawls never land in the real crawls folder
Path(os.environ["SPIDER_CRAWLS_DIR"]).mkdir(parents=True, exist_ok=True)
import uvicorn  # noqa: E402

import spider.schedule as sch  # noqa: E402
import spider.server as srv  # noqa: E402
from spider.instance import free_port, wait_ready  # noqa: E402

FAILS: list[str] = []


def check(cond: bool, label: str, extra: str = "") -> None:
    print(("  ok   " if cond else "  FAIL ") + label + (f"  ({extra})" if extra else ""))
    if not cond:
        FAILS.append(label)


def post(url: str, body: dict, method: str = "POST") -> dict:
    r = urllib.request.urlopen(urllib.request.Request(url, data=json.dumps(body).encode(), headers={"Content-Type": "application/json"}, method=method))
    return json.loads(r.read())


def get(url: str):
    return json.loads(urllib.request.urlopen(url).read())


def main() -> None:
    tmp = Path(os.environ.get("TEMP", ".")) / "claude"
    sch.SCHEDULES_PATH = tmp / "schedules_test.json"           # never touch the real schedules.json
    if sch.SCHEDULES_PATH.exists():
        sch.SCHEDULES_PATH.unlink()
    now = datetime(2026, 10, 16, 10, 0)
    d = sch.normalise({"url": "x.test", "frequency": "daily", "time": "03:00"})
    check(d["url"] == "https://x.test" and sch.next_run(d, now) == datetime(2026, 10, 17, 3, 0), "daily: next run tomorrow 03:00 when today's slot passed")
    check(sch.next_run(sch.normalise({"url": "https://x.test", "frequency": "daily", "time": "18:30"}), now) == datetime(2026, 10, 16, 18, 30), "daily: later today")
    w = sch.normalise({"url": "https://x.test", "frequency": "weekly", "day": "fri", "time": "07:00"})
    check(sch.next_run(w, now) == datetime(2026, 10, 16, 7, 0) + timedelta(days=7), "weekly: Friday (today) slot passed -> next Friday", str(sch.next_run(w, now)))
    h = sch.normalise({"url": "https://x.test", "frequency": "hourly", "every_hours": 4})
    check(sch.next_run({**h, "last_run": "2026-10-16T08:00:00"}, now) == datetime(2026, 10, 16, 12, 0), "hourly: last run + N hours")
    for bad in ({"url": ""}, {"url": "x", "frequency": "monthly"}, {"url": "x", "time": "25:00"}, {"url": "x", "frequency": "weekly", "day": "xyz"}):
        try:
            sch.normalise(bad); check(False, f"normalise rejects {bad}")
        except ValueError:
            check(True, f"normalise rejects {bad}")
    s1 = sch.upsert({"name": "nightly", "url": "https://x.test", "frequency": "daily", "time": "03:00", "created": "2026-10-16T10:00:00"})   # created before the 17 Oct slot the checks below use
    s2 = sch.upsert({"name": "hourly", "url": "https://y.test", "frequency": "hourly", "every_hours": 1})
    check(len(sch.list_schedules()) == 2 and all(x.get("next_run") for x in sch.list_schedules()), "upsert + list with next_run")
    sch.upsert({**s1, "time": "04:00"})
    check(len(sch.list_schedules()) == 2 and sch.get(s1["id"])["time"] == "04:00", "upsert updates in place")
    due_now = {x["name"] for x in sch.due(datetime(2026, 10, 17, 4, 30))}
    check(due_now == {"nightly", "hourly"}, "due: daily past its slot + hourly never run", str(due_now))
    sch.record(s1["id"], {"started": "2026-10-17T04:05:00", "ok": True})
    check("nightly" not in {x["name"] for x in sch.due(datetime(2026, 10, 17, 4, 30))} and "nightly" in {x["name"] for x in sch.due(datetime(2026, 10, 18, 4, 30))}, "recorded run clears today's due; due again tomorrow")
    once = sch.upsert({"name": "once", "url": "https://z.test", "frequency": "once", "once_at": "2026-10-16T09:00"})
    check("once" in {x["name"] for x in sch.due(now)}, "once: due after its time")
    sch.record(once["id"], {"started": now.isoformat(), "ok": True})
    check(sch.get(once["id"])["enabled"] is False, "once: disabled after it ran")
    check(sch.remove(s2["id"]) and not sch.remove("nope"), "remove")
    # the server below runs a real scheduler loop on this file: leave none of the fake-date schedules for it to run
    sch.remove(s1["id"]); sch.remove(once["id"])

    # API + a real schedule run on a small crawl
    scratch = tmp / "settings_sched_test.json"
    if scratch.exists():
        scratch.unlink()
    srv.SETTINGS_PATH = scratch; srv.State.cfg = srv.CrawlConfig()
    port = free_port()
    threading.Thread(target=lambda: uvicorn.run(srv.app, host="127.0.0.1", port=port, log_level="warning"), daemon=True).start()
    url = f"http://127.0.0.1:{port}"
    assert wait_ready(url)
    out_dir = tmp / "sched_exports"
    r = post(f"{url}/api/schedules", {"name": "docs test", "url": LIVE_SITE, "frequency": "daily", "time": "03:00", "config": {"max_urls": 12, "crawl_images": False, "crawl_css": False, "crawl_js": False, "check_external": False},
                                       "export": "issues", "export_format": "xlsx", "out_dir": str(out_dir), "compare": True})
    sid = r["schedule"]["id"]
    check(r["ok"] and r["schedule"]["next_run"], "api: schedule created", r["schedule"]["next_run"])
    check(any(s["id"] == sid for s in get(f"{url}/api/schedules")["schedules"]), "api: listed")
    r = post(f"{url}/api/schedules/{sid}/run", {})
    check(r["ok"], "api: run now started")
    time.sleep(2)
    for _ in range(240):
        st = get(f"{url}/api/crawl/status")
        if st["state"] in ("finished", "stopped", "error"):
            break
        time.sleep(1)
    for _ in range(60):
        hist = (next(s for s in get(f"{url}/api/schedules")["schedules"] if s["id"] == sid).get("history") or [])
        if hist and hist[0].get("finished"):
            break
        time.sleep(1)
    h = hist[0] if hist else {}
    check(len(hist) == 1, "run now was not started twice (scheduler loop skipped the running schedule)", f"{len(hist)} history entries")
    check(h.get("ok") and h.get("urls", 0) > 0 and h.get("issues") is not None, "schedule run recorded in history", f"{h.get('urls')} URLs, {h.get('issues')} issue types, {h.get('elapsed')}s")
    check(h.get("export") and Path(h["export"]).exists() and Path(h["export"]).stat().st_size > 1000, "export written to the schedule's folder", str(h.get("export")))
    check(isinstance(h.get("compare"), dict), "compare summary recorded", str(h.get("compare"))[:100])
    check(next(s for s in get(f"{url}/api/schedules")["schedules"] if s["id"] == sid)["last_run"], "last_run set")
    try:
        post(f"{url}/api/schedules", {"url": "", "frequency": "daily"}); check(False, "api rejects invalid schedule")
    except urllib.error.HTTPError as exc:
        check(exc.code == 400, "api rejects invalid schedule")
    check(post(f"{url}/api/schedules/{sid}", {}, method="DELETE")["ok"], "api: delete")

    # CLI against the same server
    env = {**os.environ, "SPIDER_URL": url}
    py = sys.executable
    r = subprocess.run([py, "-X", "utf8", str(ROOT / "cli.py"), "list"], capture_output=True, text=True, env=env, timeout=120)
    check(r.returncode == 0 and "URLs" in r.stdout and LIVE_HOST in r.stdout, "cli list", r.stdout.splitlines()[0][:80] if r.stdout else r.stderr[:120])
    r = subprocess.run([py, "-X", "utf8", str(ROOT / "cli.py"), "crawl", LIVE_SITE, "--max-urls", "8", "--set", "crawl_images=false", "crawl_css=false", "crawl_js=false", "check_external=false",
                        "--export", "issues", "--out", str(out_dir), "--fail-on-high"], capture_output=True, text=True, env=env, timeout=300)
    check(r.returncode in (0, 2) and "finished" in r.stdout and "exported" in r.stdout, "cli crawl + export", (r.stdout.strip().splitlines() or [r.stderr[:120]])[-1][:100])
    check(("High-priority" in r.stdout) and (r.returncode == 2) == ("0 High-priority" not in r.stdout), "cli --fail-on-high exit code matches the issue list", f"rc={r.returncode}")
    r = subprocess.run([py, "-X", "utf8", str(ROOT / "cli.py"), "issues"], capture_output=True, text=True, env=env, timeout=120)
    check(r.returncode == 0 and r.stdout.startswith("# SEO issues"), "cli issues (markdown)")
    r = subprocess.run([py, "-X", "utf8", str(ROOT / "cli.py"), "compare"], capture_output=True, text=True, env=env, timeout=120)
    check(r.returncode == 0 and "pages" in r.stdout, "cli compare", r.stdout.splitlines()[1][:90] if r.stdout.count("\n") > 1 else r.stdout[:90])
    r = subprocess.run([py, "-X", "utf8", str(ROOT / "cli.py"), "schedule", "add", "--name", "cli nightly", "--url", LIVE_SITE, "--frequency", "daily", "--time", "02:30", "--max-urls", "5"], capture_output=True, text=True, env=env, timeout=120)
    check(r.returncode == 0 and "added" in r.stdout, "cli schedule add", r.stdout.strip()[:80])
    r = subprocess.run([py, "-X", "utf8", str(ROOT / "cli.py"), "schedule", "list"], capture_output=True, text=True, env=env, timeout=120)
    check(r.returncode == 0 and "cli nightly" in r.stdout, "cli schedule list")
    r = subprocess.run([py, "-X", "utf8", str(ROOT / "cli.py"), "schedule", "run-due"], capture_output=True, text=True, env=env, timeout=120)
    check(r.returncode == 0 and "nothing due" in r.stdout, "cli schedule run-due with nothing due")
    # NOTE: cli.py reads the REAL schedules.json (module default) - the CLI tests above wrote to it; remove what they added
    real = sch.SCHEDULES_PATH
    sch.SCHEDULES_PATH = ROOT / "schedules.json"
    for s in sch.list_schedules():
        if s["name"] == "cli nightly":
            sch.remove(s["id"])
    sch.SCHEDULES_PATH = real
    # modal
    from playwright.sync_api import sync_playwright
    out = str(tmp / "schedules_modal.png")
    with sync_playwright() as p:
        b = p.chromium.launch(); pg = b.new_page(viewport={"width": 1400, "height": 900})
        pg.goto(url); pg.wait_for_selector("#splash.hide", state="attached", timeout=20000)
        pg.click("#btn-help"); pg.click("#help-schedules"); pg.wait_for_selector("#sch-new")
        pg.click("#sch-new"); pg.wait_for_selector("#sch-form")
        pg.fill("[data-s='name']", "UI nightly"); pg.fill("[data-s='url']", LIVE_SITE); pg.fill("[data-s='max_urls']", "10")
        pg.click("#sch-save"); pg.wait_for_function("document.querySelector('#schedules-body table') && document.querySelector('#schedules-body').innerText.includes('UI nightly')", timeout=10000)
        check("daily 03:00" in pg.inner_text("#schedules-body"), "modal: schedule created and listed")
        pg.screenshot(path=out)
        b.close()
    print("screenshot:", out)
    print(f"{len(FAILS)} FAILED: {FAILS}" if FAILS else "all checks passed")
    sys.exit(1 if FAILS else 0)


if __name__ == "__main__":
    main()
