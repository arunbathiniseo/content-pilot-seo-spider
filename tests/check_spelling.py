"""Spelling & grammar check: rules + dictionary on synthetic pages, a fake LanguageTool server, then the real crawl
through the API (run, ignore word, filter, URL details, report, pane).

    .venv\\Scripts\\python.exe tests\\check_spelling.py
"""
from __future__ import annotations

import json
import os
import sys
import threading
import time
import urllib.request
from http.server import BaseHTTPRequestHandler, HTTPServer
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
from fixture import fixture_crawl, fixture_root  # noqa: E402,F401
import uvicorn  # noqa: E402

import spider.server as srv  # noqa: E402
from spider import db as dbm  # noqa: E402
from spider.config import CrawlConfig  # noqa: E402
from spider.instance import free_port, wait_ready  # noqa: E402
from spider.spelling import compute, findings_for, grammar_rules, languagetool_check, summary_rows  # noqa: E402

FAILS: list[str] = []


def check(cond: bool, label: str, extra: str = "") -> None:
    print(("  ok   " if cond else "  FAIL ") + label + (f"  ({extra})" if extra else ""))
    if not cond:
        FAILS.append(label)


def post(url: str, body: dict) -> dict:
    r = urllib.request.urlopen(urllib.request.Request(url, data=json.dumps(body).encode(), headers={"Content-Type": "application/json"}))
    return json.loads(r.read())


class FakeLT(BaseHTTPRequestHandler):
    def log_message(self, *a):
        pass

    def do_POST(self):
        raw = self.rfile.read(int(self.headers.get("Content-Length", 0))).decode()
        text = urllib.parse.unquote_plus(raw.split("text=", 1)[1].split("&", 1)[0])
        matches = []
        i = text.find("recieve")
        if i >= 0:
            matches.append({"offset": i, "length": 7, "message": "Possible spelling mistake", "replacements": [{"value": "receive"}], "rule": {"id": "MORFOLOGIK_RULE_EN_GB", "category": {"id": "TYPOS"}}})
        i = text.find("the the")
        if i >= 0:
            matches.append({"offset": i, "length": 7, "message": "Possible typo: you repeated a word", "replacements": [{"value": "the"}], "rule": {"id": "ENGLISH_WORD_REPEAT_RULE", "category": {"id": "MISC"}}})
        body = json.dumps({"matches": matches}).encode()
        self.send_response(200); self.send_header("Content-Type", "application/json"); self.send_header("Content-Length", str(len(body))); self.end_headers(); self.wfile.write(body)


def main() -> None:
    import urllib.parse  # noqa: F401  (used by FakeLT)
    # grammar rules
    g = grammar_rules("We accept the the card. It is a honest offer and an unique one. this sentence is lowercase. You should of paid, really!! Pay now,please. Alot of users.")
    rules = {x["rule"] for x in g}
    check({"repeated_word", "a_an", "sentence_case", "double_punct", "space_after_comma", "confusion"} <= rules, "all grammar rules fire", str(sorted(rules)))
    check(not grammar_rules("Products.\npayments for restaurants\nthe\nthe\nTry Zorvex\nZorvex makes billing easy."), "rules do not cross line breaks (extracted-text blocks)")
    check(not grammar_rules("An SEO audit of a URL. E.g. this is fine. Open banking is a universal win, an hour later."), "no false positives on acronyms, abbreviations, 'a universal', 'an hour'", str(grammar_rules("An SEO audit of a URL. E.g. this is fine. Open banking is a universal win, an hour later.")))

    # dictionary + learnt vocabulary on a synthetic crawl
    tmp = Path(os.environ.get("TEMP", ".")) / "claude" / "spell_test.db"
    for suf in ("", "-wal", "-shm"):
        p = Path(str(tmp) + suf)
        if p.exists():
            p.unlink()
    con = dbm.connect(tmp)
    brand = "Zorvex lets merchants take payments by bank. Zorvex fees are low. "
    pages = [brand + "We recieve your paymnet instantly. Tap the the button.", brand + "Customers love Zorvex because transfers settle fast.", brand + "Open banking checkout with Zorvex saves fees.",
             brand + "A quick guide to Zorvex for restaurants.", brand + "The teh sentence has a typo.", brand + "Dr. Smith uses Zorvex. e.g. for tips."]
    for i, t in enumerate(pages, start=1):
        con.execute("INSERT INTO urls(id, url, is_internal, type, status_code, crawled, indexable, lang) VALUES (?,?,?,?,?,?,?,?)", (i, f"https://t.test/p{i}", 1, "HTML", 200, 1, 1, "en-GB"))
        con.execute("INSERT INTO bodies(url_id, text) VALUES (?,?)", (i, dbm.compress(t)))
    con.commit()
    cfg = CrawlConfig(spell_check=True, grammar_check=True, spell_learn_min_pages=3, spell_learn_share=30)
    out = compute(con, cfg, log=lambda *_: None)
    f1 = findings_for(con, 1)
    words = {x["text"] for x in f1 if x["kind"] == "spelling"}
    check({"recieve", "paymnet"} <= words and "zorvex" not in words, "misspellings flagged, brand learnt as site vocabulary", str(sorted(words)))
    check(any(x["suggestion"] == "receive" for x in f1), "suggestion offered")
    check(any(x["kind"] == "grammar" and x["rule"] == "repeated_word" for x in f1), "grammar finding on the same page")
    check(out["spelling"] >= 3 and out["grammar"] >= 1, "totals", str(out))
    rows = {r[0]: r for r in con.execute("SELECT url, spelling_errors, grammar_errors FROM urls")}
    check(rows["https://t.test/p1"][1] == 2 and rows["https://t.test/p2"][1] == 0, "per-page counts")
    sm = summary_rows(con)
    check(sm and sm[0][3] >= 1 and {r[1] for r in sm} >= {"recieve", "paymnet"}, "summary report rows", str(sm[:2]))
    cfg.spell_ignore = ["paymnet"]
    compute(con, cfg, log=lambda *_: None)
    check("paymnet" not in {x["text"] for x in findings_for(con, 1)}, "ignore list respected")

    # LanguageTool path
    lt_port = free_port()
    lt = HTTPServer(("127.0.0.1", lt_port), FakeLT); threading.Thread(target=lt.serve_forever, daemon=True).start()
    sp, gr = languagetool_check(f"http://127.0.0.1:{lt_port}", "We recieve the the money.", "en-GB")
    check(len(sp) == 1 and sp[0]["suggestion"] == "receive" and len(gr) == 1, "LanguageTool matches split into spelling / grammar")
    cfg2 = CrawlConfig(spell_check=True, grammar_check=True, grammar_lt_url=f"http://127.0.0.1:{lt_port}")
    out2 = compute(con, cfg2, log=lambda *_: None)
    check(out2["spelling"] >= 1 and out2["grammar"] >= 1 and all(x["rule"] in ("MORFOLOGIK_RULE_EN_GB", "ENGLISH_WORD_REPEAT_RULE") for x in findings_for(con, 1)), "compute uses LanguageTool when a URL is set", str(out2))
    lt.shutdown(); con.close()
    for suf in ("", "-wal", "-shm"):
        p = Path(str(tmp) + suf)
        if p.exists():
            p.unlink()

    # real crawl via the API (scratch settings so the real settings.json stays untouched)
    scratch = Path(os.environ.get("TEMP", ".")) / "claude" / "settings_spell_test.json"
    if scratch.exists():
        scratch.unlink()
    srv.SETTINGS_PATH = scratch; srv.State.cfg = srv.CrawlConfig()
    port = free_port()
    threading.Thread(target=lambda: uvicorn.run(srv.app, host="127.0.0.1", port=port, log_level="warning"), daemon=True).start()
    url = f"http://127.0.0.1:{port}"
    assert wait_ready(url)
    try:
        crawls = json.loads(urllib.request.urlopen(f"{url}/api/crawls").read())["crawls"]
        target = next(c for c in crawls if c["name"] == fixture_crawl().name)
        post(f"{url}/api/crawls/open", {"path": target["path"]})
        post(f"{url}/api/config", {"spell_check": True, "grammar_check": True})
        t0 = time.time(); r = post(f"{url}/api/spelling/run", {}); el = time.time() - t0
        check(r["ok"] and r["pages"] > 300, "spelling run on the real crawl", f"{r['pages']} pages, {r['spelling']} spelling, {r['grammar']} grammar in {el:.1f}s")
        tabs = json.loads(urllib.request.urlopen(f"{url}/api/tabs").read())["tabs"]
        flt = {f["key"]: f["count"] for t in tabs if t["key"] == "content" for f in t["filters"]}
        check(flt.get("content:spelling_errors", 0) > 0, "Content > Spelling Errors filter populated", f"{flt.get('content:spelling_errors')} pages")
        rows = json.loads(urllib.request.urlopen(f"{url}/api/urls?key=content:spelling_errors&size=3").read())["data"]
        det = json.loads(urllib.request.urlopen(f"{url}/api/url/{rows[0]['id']}").read())
        sp = [x for x in det["spelling"] if x["kind"] == "spelling"]
        check(sp and sp[0]["text"], "URL details list the misspellings", ", ".join(x["text"] for x in sp[:6]))
        rep = json.loads(urllib.request.urlopen(f"{url}/api/report/spelling?format=json").read())["sheets"][0]
        check(rep["rows"] and rep["headers"][0] == "Kind", "spelling summary report", f"{len(rep['rows'])} distinct findings; top: {rep['rows'][0][1]} on {rep['rows'][0][3]} pages")
        top_word = next((x[1] for x in rep["rows"] if x[0] == "spelling"), None)
        before = flt["content:spelling_errors"]
        ig = post(f"{url}/api/spelling/ignore", {"word": top_word})
        cfg_now = json.loads(urllib.request.urlopen(f"{url}/api/config").read())["config"]
        check(top_word in cfg_now["spell_ignore"] and ig["spelling"] < r["spelling"], "Add to dictionary saves the word and lowers the count", f"'{top_word}': {r['spelling']} -> {ig['spelling']}")
        # pane
        from playwright.sync_api import sync_playwright
        out_png = str(Path(os.environ.get("TEMP", ".")) / "claude" / "spelling_pane.png")
        with sync_playwright() as p:
            b = p.chromium.launch(); pg = b.new_page(viewport={"width": 1500, "height": 900})
            pg.goto(url); pg.wait_for_selector("#splash.hide", state="attached", timeout=20000); pg.wait_for_selector(".tabulator-row", timeout=20000)
            pg.click("#tabs button:has-text('Content')"); pg.select_option("#filter", "content:spelling_errors"); pg.wait_for_selector(".tabulator-row", timeout=20000); time.sleep(0.5)
            box = pg.query_selector(".tabulator-row").bounding_box(); pg.mouse.click(box["x"] + 150, box["y"] + box["height"] / 2)
            pg.click("#bottom-tabs button[data-pane='spelling']"); pg.wait_for_selector("#bottom-body [data-ignore]", timeout=10000)
            check(pg.evaluate("document.querySelectorAll('#bottom-body [data-ignore]').length") >= 1, "Spelling & Grammar pane with Add-to-dictionary buttons")
            pg.screenshot(path=out_png)
            pg.click("#btn-config"); pg.click(".cfg-nav button[data-g='Spelling & grammar']"); pg.wait_for_selector("#cfg-spell-run")
            check(pg.is_checked("[data-k='spell_check']"), "config panel shows the setting")
            b.close()
        print("screenshot:", out_png)
    finally:
        # restore the real crawl's columns to "off" state so Arun's open crawl is not left with test findings
        try:
            post(f"{url}/api/config", {"spell_check": False, "grammar_check": False, "spell_ignore": []}); post(f"{url}/api/spelling/run", {})
        except Exception:  # noqa: BLE001
            pass
        if scratch.exists():
            scratch.unlink()
    print(f"{len(FAILS)} FAILED: {FAILS}" if FAILS else "all checks passed")
    sys.exit(1 if FAILS else 0)


if __name__ == "__main__":
    main()
