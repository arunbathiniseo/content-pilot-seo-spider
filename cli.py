"""Content Pilot Spider command line.

    python cli.py crawl https://example.com [--mode spider|sitemap|list] [--max-urls N] [--set key=value ...]
                      [--export issues|everything|none] [--format xlsx|zip] [--out DIR] [--lighthouse N] [--google]
                      [--no-compare] [--fail-on-high] [--quiet]
    python cli.py list                      saved crawls
    python cli.py open NAME                 open a saved crawl (then issues / export / report work on it)
    python cli.py issues                    issues summary (Markdown) of the open crawl
    python cli.py export [--parts ...] [--format xlsx|zip] [--out DIR]
    python cli.py report NAME [--format csv|xlsx|json] [--out DIR]
    python cli.py sitemap [--out FILE]      XML sitemap from the open crawl
    python cli.py compare [A] [B]           change detection (defaults: open crawl vs previous of the same site)
    python cli.py schedule list|add|remove|run|run-due|install-task|uninstall-task ...
    python cli.py mcp                       print the MCP tool list

Talks to the running desktop app (port from .port / Config → MCP) or starts a headless server for the duration.
Exit codes: 0 ok, 1 error, 2 = --fail-on-high and the crawl has High-priority issues.
"""
from __future__ import annotations

import argparse
import asyncio
import json
import os
import subprocess
import sys
import threading
import time
from datetime import datetime
from pathlib import Path

ROOT = Path(__file__).resolve().parent
sys.path.insert(0, str(ROOT))

from spider.instance import alive, clear_port, pick_port, running_url, wait_ready, write_port  # noqa: E402

_started = False


def ensure_server(quiet: bool = False) -> str:
    global _started
    url = os.environ.get("SPIDER_URL", "").rstrip("/")
    if url and alive(url):
        return url
    url = running_url()
    if url:
        return url
    import uvicorn
    port = pick_port()
    write_port(port)
    threading.Thread(target=lambda: uvicorn.run("spider.server:app", host="127.0.0.1", port=port, log_level="warning"), daemon=True).start()
    url = f"http://127.0.0.1:{port}"
    if not wait_ready(url):
        sys.exit("the app server failed to start")
    _started = True
    if not quiet:
        print(f"(headless server on {url})", file=sys.stderr)
    return url


def shutdown() -> None:
    if _started:
        clear_port()


def api(url: str, method: str, path: str, **kw):
    import httpx
    r = httpx.request(method, url + path, timeout=kw.pop("timeout", 600), **kw)
    if r.status_code >= 400:
        try:
            detail = r.json().get("detail")
        except ValueError:
            detail = r.text[:300]
        sys.exit(f"error: {detail}")
    return r


def kv(pairs: list[str]) -> dict:
    out = {}
    for p in pairs or []:
        if "=" not in p:
            sys.exit(f"--set expects key=value, got {p!r}")
        k, v = p.split("=", 1)
        if v.lower() in ("true", "false"):
            out[k] = v.lower() == "true"
        else:
            try:
                out[k] = int(v) if v.lstrip("-").isdigit() else float(v)
            except ValueError:
                out[k] = v
    return out


def cmd_crawl(a: argparse.Namespace) -> int:
    url = ensure_server(a.quiet)
    cfg = kv(a.set)
    if a.max_urls is not None:
        cfg["max_urls"] = a.max_urls
    body = {"start_url": a.url, "mode": a.mode, "list_urls": a.list_urls or [], "config": cfg}
    r = api(url, "POST", "/api/crawl/start", json=body).json()
    if not a.quiet:
        print(f"crawling {a.url} ({a.mode}) -> {Path(r['db']).name}")
    last = -1
    while True:
        st = api(url, "GET", "/api/crawl/status").json()
        if st["state"] not in ("running", "stopping"):
            break
        if not a.quiet and st["crawled"] != last:
            last = st["crawled"]
            print(f"\r  {st['crawled']:>6} / {st['total']:<6} URLs  {st['rate']} URL/s  {int(st['elapsed'])}s", end="", flush=True)
        time.sleep(1)
    if not a.quiet:
        print(f"\r  {st['state']}: {st['crawled']} URLs in {int(st['elapsed'])}s" + " " * 20)
    issues = api(url, "GET", "/api/issues").json()["issues"]
    high = [i for i in issues if i["priority"] == "High" and i["kind"] == "Issue"]
    if not a.quiet:
        print(f"  {len(issues)} issue types, {len(high)} High-priority issues")
        for i in issues[:12]:
            print(f"    {i['priority']:<7} {i['kind']:<12} {i['count']:>6}  {i['tab']}: {i['name']}")
    if a.export != "none":
        parts = "overview,issues,issue_urls,issue_inlinks,matrix" if a.export == "issues" else "overview,issues,issue_urls,issue_inlinks,matrix,tabs,filters,links,reports,fixes"
        r = api(url, "GET", "/api/export/bundle", params={"parts": parts, "format": a.format})
        out = Path(a.out or ROOT / "exports"); out.mkdir(parents=True, exist_ok=True)
        from urllib.parse import urlsplit
        p = out / f"{urlsplit(a.url).hostname or 'crawl'}_{datetime.now().strftime('%Y%m%d_%H%M')}.{a.format}"
        p.write_bytes(r.content)
        print(f"  exported {p}")
    if not a.no_compare:
        import httpx
        c = httpx.get(url + "/api/compare", params={"limit": 1}, timeout=120)
        if c.status_code == 200:
            s = c.json()["summary"]
            print(f"  vs {c.json()['a'].get('started_at', '')[:16]}: issues {s['issues_up']} up / {s['issues_down']} down, {s['issues_new']} new, {s['issues_fixed']} fixed; pages +{s['added']} -{s['removed']} ~{s['changed']}")
    if a.lighthouse:
        api(url, "POST", "/api/lighthouse/run", json={"sample": a.lighthouse})
        while api(url, "GET", "/api/lighthouse/status").json()["state"] == "running":
            time.sleep(3)
        lh = api(url, "GET", "/api/lighthouse/status").json()
        print(f"  Lighthouse: {lh['ok']} pages audited, {lh['error_count']} errors")
    if a.google:
        api(url, "POST", "/api/google/run", json={"gsc": True, "ga4": True, "inspect": -1})
        while api(url, "GET", "/api/google/status").json()["job"].get("state") == "running":
            time.sleep(2)
        g = api(url, "GET", "/api/google/status").json()["job"]
        print(f"  Google: {g.get('result') or g.get('errors')}")
    return 2 if a.fail_on_high and high else 0


def cmd_list(a) -> int:
    url = ensure_server(True)
    for c in api(url, "GET", "/api/crawls").json()["crawls"]:
        print(f"{'*' if c['open'] else ' '} {c['name']:<48} {c['urls']:>7} URLs  {c['state']:<9} {c['start_url']}")
    return 0


def cmd_open(a) -> int:
    url = ensure_server(True)
    crawls = api(url, "GET", "/api/crawls").json()["crawls"]
    hit = next((c for c in crawls if c["name"] == a.name or a.name in c["name"]), None)
    if not hit:
        sys.exit(f"no crawl matches {a.name!r}")
    api(url, "POST", "/api/crawls/open", json={"path": hit["path"]})
    print(f"opened {hit['name']} ({hit['urls']} URLs)")
    return 0


def cmd_issues(a) -> int:
    url = ensure_server(True)
    sys.stdout.write(api(url, "GET", "/api/export/issues.md").text)
    return 0


def cmd_export(a) -> int:
    url = ensure_server(True)
    r = api(url, "GET", "/api/export/bundle", params={"parts": a.parts, "format": a.format})
    out = Path(a.out or ROOT / "exports"); out.mkdir(parents=True, exist_ok=True)
    p = out / f"spider-export_{datetime.now().strftime('%Y%m%d_%H%M')}.{a.format}"
    p.write_bytes(r.content); print(p)
    return 0


def cmd_report(a) -> int:
    url = ensure_server(True)
    r = api(url, "GET", f"/api/report/{a.name}", params={"format": a.format})
    if a.format == "json":
        sys.stdout.write(r.text)
        return 0
    out = Path(a.out or ROOT / "exports"); out.mkdir(parents=True, exist_ok=True)
    p = out / f"{a.name}.{a.format}"; p.write_bytes(r.content); print(p)
    return 0


def cmd_sitemap(a) -> int:
    url = ensure_server(True)
    r = api(url, "GET", "/api/sitemap/generate")
    p = Path(a.out) if a.out else ROOT / "exports" / ("sitemap.zip" if r.headers.get("content-type", "").startswith("application/zip") else "sitemap.xml")
    p.parent.mkdir(parents=True, exist_ok=True); p.write_bytes(r.content)
    print(f"{p}  ({r.headers.get('X-Sitemap-URLs', '?')} URLs)")
    return 0


def cmd_compare(a) -> int:
    url = ensure_server(True)
    c = api(url, "GET", "/api/compare", params={"a": a.a or "", "b": a.b or "", "limit": 50}).json()
    s = c["summary"]
    print(f"A {c['a']['name']} ({c['a']['started_at']})  ->  B {c['b']['name']} ({c['b']['started_at']})")
    print(f"pages {s['pages_before']} -> {s['pages_after']}: +{s['added']} added, -{s['removed']} removed, {s['changed']} changed; issues {s['issues_up']} up / {s['issues_down']} down, {s['issues_new']} new, {s['issues_fixed']} fixed")
    for i in c["issues"][:20]:
        if i["delta"]:
            print(f"  {i['delta']:+5}  {i['before']:>5} -> {i['after']:<5} {i['name']}")
    return 0


def cmd_mcp(a) -> int:
    url = ensure_server(True)
    sys.stdout.write(api(url, "GET", "/api/mcp/tools.md", params={"download": 0}).text)
    return 0


# ----------------------------------------------------------------------------- schedules
def cmd_schedule(a) -> int:
    from spider import schedule as sch
    if a.action == "list":
        for s in sch.list_schedules():
            last = (s.get("history") or [{}])[0]
            print(f"{'on ' if s.get('enabled', True) else 'off'} {s['id']}  {s['name']:<28} {s['frequency']:<7} {s['time'] if s['frequency'] in ('daily', 'weekly') else ('every ' + str(s['every_hours']) + 'h' if s['frequency'] == 'hourly' else s.get('once_at', ''))}"
                  f"{(' ' + s['day']) if s['frequency'] == 'weekly' else ''}  next {s.get('next_run') or '-'}  last {s.get('last_run') or '-'}{(' ' + ('ok' if last.get('ok') else 'FAILED')) if last else ''}  {s['url']}")
        return 0
    if a.action == "add":
        s = sch.upsert({"name": a.name, "url": a.url, "mode": a.mode, "frequency": a.frequency, "time": a.time, "day": a.day, "every_hours": a.every_hours,
                        "config": kv(a.set) | ({"max_urls": a.max_urls} if a.max_urls is not None else {}), "export": a.export, "export_format": a.format, "out_dir": a.out or "",
                        "compare": not a.no_compare, "lighthouse": a.lighthouse or 0, "google": a.google})
        print(f"added {s['id']}  next run {sch.next_run(s).isoformat(timespec='minutes')}")
        return 0
    if a.action == "remove":
        print("removed" if sch.remove(a.id) else "no such schedule")
        return 0
    if a.action in ("run", "run-due"):
        url = ensure_server(a.quiet)
        import httpx
        targets = [sch.get(a.id)] if a.action == "run" else sch.due()
        targets = [t for t in targets if t]
        if a.action == "run" and not targets:
            sys.exit("no such schedule")
        if not targets:
            print("nothing due")
            return 0

        async def go():
            async with httpx.AsyncClient(base_url=url, timeout=600) as client:
                for s in targets:
                    e = await sch.run_schedule(s, client, print)
                    if e.get("export"):
                        print(f"  {e['export']}")
        asyncio.run(go())
        return 0
    if a.action in ("install-task", "uninstall-task"):
        name = "ContentPilotSpider-Schedules"
        if a.action == "uninstall-task":
            r = subprocess.run(["schtasks", "/Delete", "/TN", name, "/F"], capture_output=True, text=True)
            print(r.stdout.strip() or r.stderr.strip()); return r.returncode
        py = str(Path(sys.executable).with_name("pythonw.exe")) if os.name == "nt" else sys.executable
        cmd = f'"{py}" "{ROOT / "cli.py"}" schedule run-due --quiet'
        r = subprocess.run(["schtasks", "/Create", "/TN", name, "/TR", cmd, "/SC", "MINUTE", "/MO", str(a.every_minutes), "/F"], capture_output=True, text=True)
        print(r.stdout.strip() or r.stderr.strip())
        print(f"Windows Task Scheduler runs due schedules every {a.every_minutes} minutes even when the app is closed (it starts a headless server if needed).")
        return r.returncode
    return 1


def main(argv: list[str] | None = None) -> int:
    p = argparse.ArgumentParser(prog="cli.py", description="Content Pilot Spider CLI")
    sp = p.add_subparsers(dest="cmd", required=True)
    c = sp.add_parser("crawl"); c.add_argument("url"); c.add_argument("--mode", default="spider", choices=["spider", "sitemap", "list"]); c.add_argument("--list-urls", nargs="*")
    c.add_argument("--max-urls", type=int); c.add_argument("--set", nargs="*", default=[], metavar="KEY=VALUE"); c.add_argument("--export", default="issues", choices=["issues", "everything", "none"])
    c.add_argument("--format", default="xlsx", choices=["xlsx", "zip"]); c.add_argument("--out"); c.add_argument("--lighthouse", type=int, default=0); c.add_argument("--google", action="store_true")
    c.add_argument("--no-compare", action="store_true"); c.add_argument("--fail-on-high", action="store_true"); c.add_argument("--quiet", action="store_true"); c.set_defaults(fn=cmd_crawl)
    sp.add_parser("list").set_defaults(fn=cmd_list)
    o = sp.add_parser("open"); o.add_argument("name"); o.set_defaults(fn=cmd_open)
    sp.add_parser("issues").set_defaults(fn=cmd_issues)
    e = sp.add_parser("export"); e.add_argument("--parts", default="overview,issues,issue_urls,issue_inlinks,matrix"); e.add_argument("--format", default="xlsx", choices=["xlsx", "zip"]); e.add_argument("--out"); e.set_defaults(fn=cmd_export)
    r = sp.add_parser("report"); r.add_argument("name"); r.add_argument("--format", default="csv", choices=["csv", "xlsx", "json"]); r.add_argument("--out"); r.set_defaults(fn=cmd_report)
    s = sp.add_parser("sitemap"); s.add_argument("--out"); s.set_defaults(fn=cmd_sitemap)
    cp = sp.add_parser("compare"); cp.add_argument("a", nargs="?"); cp.add_argument("b", nargs="?"); cp.set_defaults(fn=cmd_compare)
    sp.add_parser("mcp").set_defaults(fn=cmd_mcp)
    sc = sp.add_parser("schedule"); sc.add_argument("action", choices=["list", "add", "remove", "run", "run-due", "install-task", "uninstall-task"])
    sc.add_argument("--id"); sc.add_argument("--name"); sc.add_argument("--url"); sc.add_argument("--mode", default="spider", choices=["spider", "sitemap", "list"])
    sc.add_argument("--frequency", default="daily", choices=["hourly", "daily", "weekly", "once"]); sc.add_argument("--time", default="03:00"); sc.add_argument("--day", default="mon"); sc.add_argument("--every-hours", type=int, default=6)
    sc.add_argument("--max-urls", type=int); sc.add_argument("--set", nargs="*", default=[]); sc.add_argument("--export", default="issues", choices=["issues", "everything", "none"]); sc.add_argument("--format", default="xlsx", choices=["xlsx", "zip"]); sc.add_argument("--out")
    sc.add_argument("--no-compare", action="store_true"); sc.add_argument("--lighthouse", type=int, default=0); sc.add_argument("--google", action="store_true"); sc.add_argument("--every-minutes", type=int, default=15); sc.add_argument("--quiet", action="store_true")
    sc.set_defaults(fn=cmd_schedule)
    a = p.parse_args(argv)
    try:
        return a.fn(a)
    finally:
        shutdown()


if __name__ == "__main__":
    sys.exit(main())
