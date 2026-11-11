"""Structured data check: extraction + validation on synthetic HTML, then a small live crawl through the API
(Structured Data tab filters, URL details pane, report).

    .venv\\Scripts\\python.exe tests\\check_structured.py
"""
from __future__ import annotations

import json
import os
import sys
import threading
import time
import urllib.request
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
from fixture import fixture_start  # noqa: E402
os.environ.setdefault("SPIDER_CRAWLS_DIR", str(Path(os.environ.get("TEMP", ".")) / "claude" / "spider_test_crawls"))   # test crawls never land in the real crawls folder
Path(os.environ["SPIDER_CRAWLS_DIR"]).mkdir(parents=True, exist_ok=True)
import uvicorn  # noqa: E402

from spider.instance import free_port, wait_ready  # noqa: E402
from spider.structured import analyse, validate, vocab  # noqa: E402

FAILS: list[str] = []


def check(cond: bool, label: str, extra: str = "") -> None:
    print(("  ok   " if cond else "  FAIL ") + label + (f"  ({extra})" if extra else ""))
    if not cond:
        FAILS.append(label)


def post(url: str, body: dict) -> dict:
    r = urllib.request.urlopen(urllib.request.Request(url, data=json.dumps(body).encode(), headers={"Content-Type": "application/json"}))
    return json.loads(r.read())


def main() -> None:
    v = vocab()
    check(len(v["classes"]) > 800 and "Article" in v["classes"] and "headline" in v["props"], "schema.org vocabulary loaded", f"{len(v['classes'])} classes, {len(v['props'])} properties")

    good = {"@context": "https://schema.org", "@type": "Article", "headline": "Pay by bank", "image": "https://x.test/i.jpg", "datePublished": "2026-01-01",
            "author": {"@type": "Person", "name": "A"}, "dateModified": "2026-01-02", "publisher": {"@type": "Organization", "name": "Example Co", "logo": "x", "url": "https://x.test"}}
    e, w = validate(good)
    check(not e, "valid Article: no errors", str(e))
    e, w = validate({"@type": "Article", "headline": "x"})
    check(any("required property 'image'" in m for m in e) and any("datePublished" in m for m in e) and any("recommended property 'author'" in m for m in w), "Article missing image/date -> errors, author -> warning")
    e, w = validate({"@type": "Articel", "headline": "x", "fooBar": 1})
    check(any("Unknown schema.org type 'Articel'" in m for m in e) and any("Unknown schema.org property 'fooBar'" in m for m in e), "unknown type and property flagged")
    e, w = validate({"@type": "Product", "name": "P", "offers": {"@type": "Offer", "price": "9.99"}})
    check(any("Offer is missing required property 'priceCurrency' in offers" in m for m in e), "nested Offer validated with path", str(e))
    e, w = validate({"@type": "FAQPage", "mainEntity": [{"@type": "Question", "name": "Q?", "acceptedAnswer": {"@type": "Answer", "text": "A"}}]})
    check(not e, "FAQPage: schema-valid, no Google feature check (retired)", str(e))
    e, w = validate({"@type": "Organization", "name": "Example Co", "recipeYield": "4"})
    check(any("not expected on Organization" in m for m in w), "property on the wrong type -> warning", str(w))

    html = """<html><head>
      <script type="application/ld+json">{"@context":"https://schema.org","@graph":[{"@type":"Organization","name":"Example Co","url":"https://x.test"},{"@type":"BreadcrumbList","itemListElement":[{"@type":"ListItem","position":1,"name":"Home","item":"https://x.test/"}]}]}</script>
      <script type="application/ld+json">{"@type": "Article", "headline": "broken",}</script>
      </head><body>
      <div itemscope itemtype="https://schema.org/Product"><span itemprop="name">Reader</span><span itemprop="sku">R1</span></div>
      <p vocab="https://schema.org/" typeof="Person"><span property="name">Bob</span></p></body></html>"""
    r = analyse(html, "https://x.test/p")
    c = r["counts"]
    check(c["sd_count"] == 4 and c["sd_jsonld"] == 2 and c["sd_microdata"] == 1 and c["sd_rdfa"] == 1, "counts per syntax (graph expanded, invalid JSON excluded)", str(c))
    check(c["sd_parse_errors"] == 1 and any(i["type"] == "(invalid JSON)" for i in r["items"]), "JSON-LD parse error counted")
    check("Organization" in c["sd_types"] and "Product" in c["sd_types"] and "Person" in c["sd_types"], "types summary", c["sd_types"])
    prod = next(i for i in r["items"] if i["syntax"] == "Microdata")
    check(prod["data"].get("name") == "Reader" and any("recommended property 'offers'" in m for m in prod["warnings"]), "microdata normalised + Google warnings", str(prod["warnings"][:2]))
    person = next(i for i in r["items"] if i["syntax"] == "RDFa")
    check(person["data"].get("name") == "Bob", "RDFa compacted to plain properties", str(person["data"]))
    t0 = time.time(); [analyse(html, "https://x.test/p") for _ in range(50)]; per = (time.time() - t0) / 50 * 1000
    check(per < 60, "analyse() is fast", f"{per:.1f} ms/page")
    r2 = analyse("<html><body><p>plain</p></body></html>", "https://x.test/q")
    check(r2["counts"]["sd_count"] == 0 and not r2["items"], "page without structured data")

    # live crawl via API
    port = free_port()
    threading.Thread(target=lambda: uvicorn.run("spider.server:app", host="127.0.0.1", port=port, log_level="warning"), daemon=True).start()
    url = f"http://127.0.0.1:{port}"
    assert wait_ready(url)
    s0 = post(f"{url}/api/crawl/start", {"start_url": fixture_start(), "mode": "spider",   # a live site the fixture crawl came from
                                         "config": {"max_urls": 25, "crawl_images": False, "crawl_css": False, "crawl_js": False, "check_external": False, "extract_srcset": False}})
    check(s0.get("ok"), "crawl started")
    for _ in range(240):
        st = json.loads(urllib.request.urlopen(f"{url}/api/crawl/status").read())
        if st["state"] in ("finished", "stopped", "error"):
            break
        time.sleep(1)
    check(st["state"] == "finished", "crawl finished", f"{st.get('crawled')} URLs in {st.get('elapsed')}s")
    tabs = json.loads(urllib.request.urlopen(f"{url}/api/tabs").read())["tabs"]
    sd_tab = next((t for t in tabs if t["key"] == "structured_data"), None)
    check(sd_tab is not None and [f["key"] for f in sd_tab["filters"]][:3] == ["structured_data:all", "structured_data:contains", "structured_data:missing"], "Structured Data tab present")
    flt = {f["key"]: f["count"] for f in sd_tab["filters"]}
    print(f"  info filters: {flt}")
    check(flt["structured_data:contains"] + flt["structured_data:missing"] > 0, "pages classified as contains / missing")
    rows = json.loads(urllib.request.urlopen(f"{url}/api/urls?key=structured_data:contains&size=3").read())["data"]
    if rows:
        det = json.loads(urllib.request.urlopen(f"{url}/api/url/{rows[0]['id']}").read())
        check(det["structured_data"] and det["structured_data"][0]["type"], "URL details carry the items", f"{rows[0]['url']} -> {', '.join(i['type'] for i in det['structured_data'])[:90]}")
        check(rows[0].get("sd_types"), "grid row has Schema Types column", rows[0].get("sd_types", "")[:60])
        rep = json.loads(urllib.request.urlopen(f"{url}/api/report/structured_data?format=json").read())["sheets"][0]
        check(rep["rows"] and rep["headers"][2] == "Type", "structured data report", f"{len(rep['rows'])} rows")
        from playwright.sync_api import sync_playwright
        out = str(Path(os.environ.get("TEMP", ".")) / "claude" / "sd_pane.png")
        with sync_playwright() as p:
            b = p.chromium.launch(); pg = b.new_page(viewport={"width": 1500, "height": 900})
            pg.goto(url); pg.wait_for_selector("#splash.hide", state="attached", timeout=20000); pg.wait_for_selector(".tabulator-row", timeout=20000)
            pg.click("#tabs button:has-text('Structured Data')"); pg.wait_for_selector(".tabulator-row", timeout=20000); time.sleep(0.5)
            pg.select_option("#filter", "structured_data:contains"); pg.wait_for_selector(".tabulator-row", timeout=20000); time.sleep(0.5)
            box = pg.query_selector(".tabulator-row").bounding_box(); pg.mouse.click(box["x"] + 150, box["y"] + box["height"] / 2)
            pg.click("#bottom-tabs button[data-pane='structured']"); pg.wait_for_selector(".sd-item", timeout=10000)
            check(pg.evaluate("document.querySelectorAll('.sd-item').length") >= 1, "Structured Data pane renders items")
            pg.screenshot(path=out)
            b.close()
        print("screenshot:", out)
    else:
        print("  info the crawled pages carry no structured data; pane not exercised on real data")
    print(f"{len(FAILS)} FAILED: {FAILS}" if FAILS else "all checks passed")
    sys.exit(1 if FAILS else 0)


if __name__ == "__main__":
    main()
