"""Report API with crawl=<file>: /api/tabs, /api/issues, /api/urls, /api/urls/values, /api/url/{id} and its source
answer for a saved crawl read-only, without opening it, with the same shapes and the same numbers as when that crawl
is open. Read-only: runs against the fixture crawl (tests/fixture.py).

    .venv\\Scripts\\python.exe tests\\check_report.py
"""
from __future__ import annotations

import asyncio
import os
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
sys.path.insert(0, str(ROOT / "tests"))
os.environ.pop("SPIDER_API_KEY", None)

import httpx  # noqa: E402

from fixture import fixture_crawl  # noqa: E402
from spider import server  # noqa: E402

FAILS: list[str] = []


def check(cond: bool, label: str, extra: str = "") -> None:
    print(("  ok   " if cond else "  FAIL ") + label + (f"  ({extra})" if extra else ""))
    if not cond:
        FAILS.append(label)


async def main() -> None:
    db = fixture_crawl()
    name = db.name
    transport = httpx.ASGITransport(app=server.app, client=("127.0.0.1", 1))
    async with httpx.AsyncClient(transport=transport, base_url="http://127.0.0.1", timeout=120) as c:
        async def get(path: str, **params):
            r = await c.get(path, params=params)
            return r.status_code, (r.json() if r.headers.get("content-type", "").startswith("application/json") else r.text)

        print(f"named crawl {name}, not opened")
        server.State.db_path = None
        server.State.crawler = None
        st, iss = await get("/api/issues", crawl=name)
        check(st == 200 and iss["issues"], "issues of the named crawl", f"{len(iss.get('issues', []))} issue types")
        first = iss["issues"][0]
        check({"key", "tab", "name", "kind", "priority", "count", "desc", "fix"} <= set(first), "each issue has key, tab, name, kind, priority, count, desc, fix")
        check(server.State.db_path is None, "reading by name does not open the crawl")
        st, tabs = await get("/api/tabs", crawl=name)
        check(st == 200 and tabs["tabs"] and tabs["columns"], "tabs with counts and columns")
        st, page = await get("/api/urls", crawl=name, key="internal:all", page=1, size=5, sort="inlinks", dir="desc")
        check(st == 200 and len(page["data"]) == 5 and page["total"] >= 5 and "id" in page["data"][0] and "url" in page["data"][0], "URL list paged", f"total {page.get('total')}")
        inl = [r.get("inlinks") for r in page["data"] if r.get("inlinks") is not None]
        check(inl == sorted(inl, reverse=True), "sorted by inlinks, descending", str(inl))
        st, big = await get("/api/urls", crawl=name, key="internal:all", size=50000)
        check(st == 200 and len(big["data"]) <= 1000, "page size capped at 1,000", str(len(big.get("data", []))))
        st, aff = await get("/api/urls", crawl=name, key=first["key"], size=20)
        check(st == 200 and aff["total"] == first["count"], "an issue's URLs: total matches the issue count", f"{first['key']}: {aff.get('total')} vs {first['count']}")
        host_word = page["data"][0]["url"].split("/")[2].split(".")[0]
        st, found = await get("/api/urls", crawl=name, key="internal:all", q=host_word, size=5)
        check(st == 200 and found["total"] > 0, "search by text", f"q={host_word}: {found.get('total')}")
        st, vals = await get("/api/urls/values", crawl=name, key="internal:all", field="status_code")
        check(st == 200 and vals["values"], "column values for a filter", str(vals.get("values", [])[:3]))
        uid = page["data"][0]["id"]
        st, det = await get(f"/api/url/{uid}", crawl=name)
        check(st == 200 and det["url"] == page["data"][0]["url"] and isinstance(det["inlinks"], list) and isinstance(det["issues"], list) and "issue_names" in det, "one URL's details: inlinks, outlinks, issues", f"{len(det.get('inlinks', []))} inlinks, {len(det.get('issues', []))} issues")
        check(isinstance(det.get("headers"), (dict, list)) or det.get("headers") in (None, ""), "headers parsed")
        st, srcd = await get(f"/api/url/{uid}/source", crawl=name)
        check(st in (200, 404), "source by name answers", str(st))
        st, _ = await get(f"/api/url/999999999", crawl=name)
        check(st == 404, "an unknown URL id: 404")
        for path in ("/api/issues", "/api/tabs", "/api/urls", f"/api/url/{uid}"):
            st, d = await get(path, crawl="missing.db")
            check(st == 404, f"{path} with an unknown crawl: 404")
        st, d = await get("/api/issues", crawl="../settings.json")
        check(st == 404, "a path outside the crawls folder is not read")

        print("the same crawl opened: same answers")
        r = await c.post("/api/crawls/open", json={"path": str(db)})
        check(r.status_code == 200, "open the crawl", str(r.status_code))
        st, iss_open = await get("/api/issues")
        check([(i["key"], i["count"]) for i in iss_open["issues"]] == [(i["key"], i["count"]) for i in iss["issues"]], "issues match the open crawl")
        st, page_open = await get("/api/urls", key="internal:all", page=1, size=5, sort="inlinks", dir="desc")
        check([r["id"] for r in page_open["data"]] == [r["id"] for r in page["data"]], "URL page matches the open crawl")
        st, det_open = await get(f"/api/url/{uid}")
        check(det_open["issues"] == det["issues"] and len(det_open["inlinks"]) == len(det["inlinks"]), "URL details match the open crawl")


if __name__ == "__main__":
    asyncio.run(main())
    print("\nall checks passed" if not FAILS else f"\n{len(FAILS)} FAILED: {FAILS}")
    sys.exit(1 if FAILS else 0)
