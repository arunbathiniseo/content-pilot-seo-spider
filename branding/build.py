"""Render the brand assets: PNG icon sizes, Windows .ico, favicon, logo lockups, social card and README banner.
Run from the project root:  .venv\\Scripts\\python.exe branding\\build.py"""
from __future__ import annotations

import asyncio
import re
from pathlib import Path

from PIL import Image

HERE = Path(__file__).resolve().parent
OUT = HERE / "out"
OUT.mkdir(exist_ok=True)

FONT = '<link rel="stylesheet" href="https://fonts.googleapis.com/css2?family=IBM+Plex+Sans:wght@500;600&family=IBM+Plex+Mono:wght@500&display=swap">'
MARK = (HERE / "mark.svg").read_text(encoding="utf-8")


def sized(svg: str, w: int, h: int) -> str:
    svg = re.sub(r'width="\d+"', f'width="{w}"', svg, count=1)
    return re.sub(r'height="\d+"', f'height="{h}"', svg, count=1)


def page_html(body: str, bg: str = "transparent") -> str:
    return f'<!doctype html><html><head><meta charset="utf-8">{FONT}</head><body style="margin:0;background:{bg}">{body}</body></html>'


SOCIAL = f"""<!doctype html><html><head><meta charset="utf-8">{FONT}<style>
body{{margin:0;width:1200px;height:630px;background:#15171a;font-family:'IBM Plex Sans',system-ui,sans-serif;color:#f4f5f7;position:relative;overflow:hidden}}
.wrap{{display:flex;flex-direction:column;justify-content:center;height:100%;padding:0 96px;gap:30px}}
.row{{display:flex;align-items:center;gap:30px}}
.mark{{width:120px;height:120px}}
h1{{margin:0;font-size:66px;font-weight:600;letter-spacing:-2.4px;line-height:1.05}}
.sub{{font-family:'IBM Plex Mono',monospace;font-size:15px;font-weight:500;letter-spacing:4px;color:#2ea043;margin-top:8px}}
p{{margin:0;font-size:28px;font-weight:500;color:#c9ced6;max-width:900px;line-height:1.35}}
.rule{{height:3px;width:120px;background:#2ea043}}
.foot{{position:absolute;right:96px;bottom:44px;font-family:'IBM Plex Mono',monospace;font-size:16px;letter-spacing:2px;color:#9aa3ad}}
</style></head><body><div class="wrap">
<div class="row"><img class="mark" src="mark.svg" alt=""><div><h1>Content Pilot Spider</h1><div class="sub">SEO CRAWLER · LOCAL · OPEN SOURCE</div></div></div>
<div class="rule"></div>
<p>Crawl any site like Screaming Frog, review AI-written fixes, push them to staging or live. No URL limit. Your own API key. Runs on your machine.</p>
</div><div class="foot">MIT · PYTHON · WINDOWS / MAC / LINUX</div></body></html>"""

BANNER = f"""<!doctype html><html><head><meta charset="utf-8">{FONT}<style>
body{{margin:0;width:1600px;height:400px;background:#f4f5f7;font-family:'IBM Plex Sans',system-ui,sans-serif;color:#15171a;position:relative;overflow:hidden}}
.wrap{{display:flex;align-items:center;gap:44px;height:100%;padding:0 96px}}
.mark{{width:168px;height:168px}}
h1{{margin:0;font-size:74px;font-weight:600;letter-spacing:-2.8px;line-height:1.05}}
.sub{{font-family:'IBM Plex Mono',monospace;font-size:17px;font-weight:500;letter-spacing:4px;color:#1f7a3f;margin-top:10px}}
p{{margin:18px 0 0;font-size:26px;font-weight:500;color:#3d434b}}
.edge{{position:absolute;left:0;top:0;bottom:0;width:10px;background:#2ea043}}
</style></head><body><div class="edge"></div><div class="wrap">
<img class="mark" src="mark.svg" alt=""><div><h1>Content Pilot Spider</h1><div class="sub">SEO CRAWLER · LOCAL · OPEN SOURCE</div><p>Crawl, find issues, fix them with AI, push to your site.</p></div>
</div></body></html>"""


async def main():
    from playwright.async_api import async_playwright
    (OUT / "social-card.html").write_text(SOCIAL, encoding="utf-8")
    (OUT / "readme-banner.html").write_text(BANNER, encoding="utf-8")
    (OUT / "mark.svg").write_text(MARK, encoding="utf-8")
    async with async_playwright() as pw:
        browser = await pw.chromium.launch()
        page = await browser.new_page(device_scale_factor=1)
        for size in (16, 24, 32, 48, 64, 128, 256, 512, 1024):
            await page.set_viewport_size({"width": size, "height": size})
            await page.set_content(page_html(sized(MARK, size, size)))
            await page.screenshot(path=str(OUT / f"icon-{size}.png"), omit_background=True)
        for name in ("logo-horizontal.svg", "logo-horizontal-dark.svg"):
            svg = (HERE / name).read_text(encoding="utf-8")
            bg = "#15171a" if "dark" in name else "#f4f5f7"
            await page.set_viewport_size({"width": 1440, "height": 320})
            await page.set_content(page_html(sized(svg, 1440, 320), bg))
            await page.wait_for_timeout(900)
            await page.screenshot(path=str(OUT / name.replace(".svg", "@2x.png")))
        for name, w, h in (("social-card", 1200, 630), ("readme-banner", 1600, 400)):
            await page.set_viewport_size({"width": w, "height": h})
            await page.goto((OUT / f"{name}.html").as_uri())
            await page.wait_for_timeout(1000)
            await page.screenshot(path=str(OUT / f"{name}.png"))
        await browser.close()
    base = Image.open(OUT / "icon-256.png").convert("RGBA")
    base.save(OUT / "icon.ico", sizes=[(16, 16), (24, 24), (32, 32), (48, 48), (64, 64), (128, 128), (256, 256)])
    Image.open(OUT / "icon-32.png").save(OUT / "favicon-32.png")
    print("written:", sorted(p.name for p in OUT.iterdir()))


asyncio.run(main())
