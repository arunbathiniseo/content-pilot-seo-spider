# Open-source crawler survey (verified 14 Oct 2026)

| Project | Repo | Lang | Licence | Last push | Stars | JS render | Storage | Run as |
|---|---|---|---|---|---|---|---|---|
| Greenflare | github.com/beb7/gflare-tk | Python/Tkinter | GPL-3 | 2023-07 | 198 | No | SQLite | Desktop |
| SEOnaut | github.com/StJudeWasHere/seonaut | Go | MIT | 2026-10 | 807 | No | MySQL | Web (Docker) |
| LibreCrawl | github.com/PhialsBasement/LibreCrawl | Python/Flask | MIT | 2026-09 | 1,029 | Playwright (opt.) | SQLite | Web |
| Open SEO Crawler | github.com/puneetindersingh/open-seo-crawler | Python/Flask | MIT | 2026-09 | 61 | Playwright (opt.) | files | Web |
| Unlighthouse | github.com/harlan-zw/unlighthouse | TS | MIT | 2026-10 | 4,893 | Puppeteer | files | CLI + UI |
| LinkChecker | github.com/linkchecker/linkchecker | Python | GPL-2 | 2026-09 | 1,076 | No | CSV/SQL | CLI |
| advertools | github.com/eliasdabbas/advertools | Python/Scrapy | MIT | 2026-06 | 1,476 | scrapy-playwright | .jl | Library |
| Crawlee Python | github.com/apify/crawlee-python | Python | Apache-2 | 2026-10 | 9,589 | Playwright | mem/fs/SQL | Library |
| SiteOne Crawler | github.com/janreges/siteone-crawler | Rust | MIT | 2026-09 | 947 | CDP | reports | CLI + Tauri |
| Crawlie | github.com/spronta/crawlie | Rust | MIT | 2026-07 | 114 | headless Chrome | ? | CLI/MCP/Tauri |
| Screaming Toad | github.com/lovecatisgood-sudo/… | Go + React | MIT | 2026-10 | 7 | optional | SQLite | web + CLI |

Decision: none is an importable Python SEO crawler with a data model that can carry ~320 Screaming Frog
issues. Build the crawl loop ourselves on httpx + lxml + Playwright, borrow extraction ideas from
LibreCrawl / advertools (both MIT), and use these as sidecars/libraries:

- pywebview 6.2.1 (BSD-3, active) — desktop window on Edge WebView2 (installed on this machine).
- extruct (BSD-3, active) — JSON-LD / microdata / RDFa extraction. No Python lib validates Google rich results.
- @adobe/structured-data-validator (Node, Apache-2, active) — schema.org + Google Rich Results validation.
- Lighthouse (Node) — PageSpeed, Mobile, Accessibility (axe-core).
- protego (BSD) — Google-compatible robots.txt parser.
- datasketch MinHash — near-duplicate detection. textstat — readability.
