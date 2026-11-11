# Content Pilot Spider: reference

The full technical reference: every feature, the HTTP API, the hosted mode, the command line, the MCP server,
the tests and the code layout. For an overview and install steps see the [README](../README.md).

## Features in detail

- **Modes**: Spider (follows links, discovers XML sitemaps via robots.txt), Sitemap (fetch every URL in an XML
  sitemap or sitemap index, no link following) and List (paste URLs).
- **JavaScript rendering** (Config > Rendering): headless Chromium via Playwright. Keeps the raw and the rendered
  HTML, so the **JavaScript tab** reports title/meta/H1/canonical only-in-rendered or changed-by-JS, noindex/nofollow
  only in the original HTML, canonical mismatch, JS-only links and content, resources blocked by robots.txt during
  render, JavaScript console errors, old AJAX scheme. Lower pane gets **Rendered Page** (screenshot) and
  **Console Log**; View Source toggles original/rendered HTML.
- **Tabs / filters** (~180, all named as in Screaming Frog): Internal, External, Security, Response Codes, URL,
  Page Titles, Meta Description, Meta Keywords, H1, H2, Content, Images, Canonicals, Pagination, Directives,
  Hreflang, JavaScript, Links, Validation, Sitemaps, Custom Search, Custom Extraction.
- **Spelling & grammar** (Config → Spelling & grammar, off by default like Screaming Frog): offline dictionary
  check (en/de/fr/es/pt/it/nl/ru) that understands British spellings, plurals, possessives and hyphenated words and
  learns your site's own vocabulary (unknown words on many pages are brand terms, not typos); grammar via built-in
  high-precision rules or a LanguageTool server (self-hosted or languagetool.org) which then handles spelling too.
  Content → Spelling Errors / Grammar Errors filters, a Spelling & Grammar lower pane with "Add to dictionary", and a
  Spelling & Grammar Summary report (each finding with page count). Runs at the end of a crawl, on Analyse or "Run
  check now"; no re-crawl needed.
- **Structured Data tab**: JSON-LD, Microdata and RDFa extracted from every page and validated offline against the
  bundled schema.org vocabulary (unknown types / properties, properties on the wrong type) and Google's rich-result
  requirements (required = error, recommended = warning; Article, Product/Offer, Breadcrumb, HowTo, Event,
  LocalBusiness, Recipe, Review, Video, Job, Course, Dataset, Sitelinks search box… FAQ deliberately not checked).
  Filters: Contains / Missing / Validation Errors / Warnings / Parse Errors / per syntax; Structured Data lower pane
  with the messages and the JSON; Structured Data report.
- **AMP tab**: AMP pages (the amp or lightning-bolt attribute) validated structurally (doctype, charset, viewport, canonical, runtime
  script, boilerplate, amp-custom limits, !important, disallowed img/video/audio/iframe and custom JavaScript) plus
  cross-page checks: non-200 AMP responses, missing non-AMP return link, non-indexable canonical, indexable AMP URL,
  linked as amphtml without `<html amp>`.
- **Site Audit dashboard** (the Semrush / Ahrefs style overview): one Site Health score out of 100 with its
  formula shown (100 minus, per issue type, weight × priority × share of pages affected × 25), errors / warnings /
  notices with type and page counts, crawled-pages breakdown (healthy, with errors, redirects, broken, blocked),
  nine thematic scores (Crawlability, HTTPS, Core Web Vitals, Performance, Internal linking, On-page, Markup,
  International, Accessibility) each with its costliest issues, a Core Web Vitals summary when Lighthouse ran, the
  Site Health trend across crawls of the site, the top issues ranked by points cost (click → filter), and a Markdown
  report. A toast gives the score when a crawl finishes. MCP: `site_audit`, `site_audit_history`.
- **Content Pilot launcher**: contentpilot.cloud's Site audit page opens the Spider in a new tab (`/?url=…&start=1`
  pre-fills and starts the crawl); the spider answers CORS + Private Network Access preflights for contentpilot.cloud
  on four read-only endpoints so the card can show whether it is running and the latest Site Health.
- **Issues sidebar**: every triggered issue with type (Issue / Warning / Opportunity), priority, description, fix.
- **Lower pane**: URL Details, Inlinks, Outlinks (+ uncrawlable links), Image Details, Resources, SERP Snippet,
  HTTP Headers, Cookies, View Source, Issues.
- **Crawl analysis**: inlink/outlink counts, Link Score (PageRank), duplicates (title/meta/H1/H2/exact hash),
  near duplicates (MinHash over 3-word shingles, threshold in Config → Preferences, pairs in the Duplicates pane),
  redirect chains & loops, canonical targets, pagination checks, full hreflang reciprocity checks, image alt
  aggregation, sitemap membership / orphans. Runs every 15s during a crawl and once at the end (Analyse re-runs it).
- **Exports**: current filter to CSV/XLSX; everything to one XLSX workbook; Reports: Crawl Overview, Issues Overview,
  All Inlinks/Outlinks, External Links, Anchor Text, Redirect Chains, Redirects to Errors, Canonical Chains, Orphan
  Pages, Insecure Content, SERP Summary, HTTP Headers, Cookies, XML Sitemaps, Uncrawlable Links.
- **XML sitemap generation** (Export → XML sitemap…): indexable 2xx HTML pages by default, options for noindex /
  canonicalised / paginated pages and PDFs, lastmod from the Last-Modified header (else crawl time), priority and
  changefreq by crawl depth, image entries; live preview of what is included and excluded; splits into sitemap1.xml…
  + sitemap_index.xml above 50,000 URLs (zip). MCP: `export_sitemap`.
- **Visualisations** (Visualise): crawl tree and force-directed crawl diagram (shortest click path from the start
  page over internal links, orphans counted), directory tree and force-directed directory diagram (URL folders),
  inlink anchor text and body text word clouds. Nodes coloured by status / indexability, sized by inlinks, hover for
  facts, click to filter the grid to that URL, zoom and pan, download as SVG. Rendered with D3 (vendored). MCP:
  `crawl_tree`, `word_frequencies`.
- **PageSpeed & Accessibility** (PageSpeed): Lighthouse audits of selected rows, a whole filter, or the top pages
  (start page + most linked), locally in the bundled headless Chromium (~25 s per page, no key) or through the
  PageSpeed Insights API with your key (adds CrUX field data: real-user LCP / CLS / INP). PageSpeed tab: scores,
  lab + field metrics, and Screaming Frog's opportunity filters (render-blocking resources, image sizing, unused
  CSS/JS, cache policy, DOM size…); Accessibility tab: score + axe-core checks (colour contrast, alt text, link
  names, lang, headings, labels, ARIA…). PageSpeed lower pane with the four scores, metrics, every failing audit with
  estimated savings and sample elements, and a re-run button. Optional automatic run after each crawl (Config →
  PageSpeed). MCP: `lighthouse_run`, `lighthouse_status`.
- **Search Console & GA4** (Data menu): sign in with your own Google Cloud OAuth client (Desktop app; loopback
  redirect to the app, refresh token kept in settings.json). Per-page clicks, impressions, CTR and position from
  Search Analytics, sessions / engaged sessions / engagement rate / bounce rate / views / key events from GA4,
  merged into the crawl for the chosen date range; URLs with traffic that the crawl never reached become Orphan URL
  rows. Optional URL Inspection (index verdict, coverage state, Google's canonical, last crawl) for the top pages.
  Search Console tab: Clicks / Impressions Above 0, No Data, Non-Indexable with Data, Orphan URLs, Position Above 10,
  Low CTR, URL Is Not on Google, Indexable URL Not Indexed, User-Declared Canonical Not Selected. Analytics tab:
  Sessions Above 0, Bounce Rate Above 70%, No GA4 Data, Non-Indexable with GA4 Data, Orphan URLs, Key Events Above 0.
  Optional automatic fetch after each crawl. MCP: `google_fetch`, `google_status`. `tests\mock_google.py` fakes
  Google for trying it without credentials.
- **Link Metrics** (Data menu): Ahrefs (URL Rating, Domain Rating, live backlinks, referring domains, organic
  keywords and traffic), Moz (Page / Domain Authority, Spam Score, linking root domains, external links) and Majestic
  (Trust Flow, Citation Flow, backlinks, referring domains) per page, with your own API keys (Config → Link
  metrics). Only providers with a key are queried; runs are capped (default 100 URLs, most-linked pages first)
  because every URL costs credits. Filters: Has Link Metrics, No Backlinks, Low / High Page Authority, Moz Spam Score
  30%+, Backlinks but Few Internal Links, Backlinks to Non-Indexable Page. MCP: `linkmetrics_run`.
- **Config**: crawl toggles, limits, robots handling, speed/threads, user-agent presets, custom headers,
  basic auth, include/exclude regex, parameter removal, CDN domains, all Screaming Frog preference thresholds,
  custom search (contains / does not contain / regex) and custom extraction (XPath / CSS / regex).

## Compare (change detection)

**Compare** diffs two crawls of a site: HTML pages added / removed / changed (status, indexability, title, meta
description, H1, canonical, robots, word count, inlinks, depth, redirect, shown before → after per page) and every
issue's count before vs after, with new and fixed issues called out. The newest crawl and the previous completed
crawl of the same site are preselected, and every finished crawl is compared automatically with its predecessor
(toast with the headline numbers). Export the whole diff as one workbook. MCP: `compare_crawls`, `export_compare`.

## Crawl-complete email

**Config → Email**: recipients, SMTP server (Gmail / Outlook app passwords, SendGrid, a local relay…), STARTTLS /
SSL / none, and "Send test email". When a crawl finishes you get a plain-text + HTML report: totals, what changed
since the previous crawl of the site, and every triggered issue with type, priority, URL count and the fix. The
SMTP password lives only in `settings.json`. `tests\mock_smtp.py` is a local sink for trying it without a mail account.

## Usage & costs

**? Help → Usage & costs**: crawls run, URLs and HTML pages fetched, crawl time, AI requests, tokens in/out and the
estimated AI cost, cumulative or per run, plus a per-provider/model table. Token counts come from each provider's
response. Anthropic prices are built in (official price list); add other providers' prices on the Prices tab
(USD per million tokens). Stored locally in `usage.json`. MCP: `usage_stats`.

## AI fixes and push (bring your own key)

1. **Config → AI**: pick a provider (Anthropic Claude via the official SDK, OpenAI, Google Gemini, Ollama, or any
   OpenAI-compatible endpoint), paste your key, optionally set the model, brand name and house style. The model box
   is checked live against the provider's own model list (dropdown of valid names, warning if the typed one does not
   exist, also on Test connection and Save). "Advanced: system prompt" replaces the default persona per provider;
   task, brand, house style and the JSON output rules are always appended. "Test connection" sends a one-word
   prompt. Keys live only in `settings.json` on your machine (gitignored).
2. Pick a filter (e.g. Page Titles → Over 60 Characters) and click **Fix with AI** → all URLs in the filter or
   just the selected rows. Titles, meta descriptions, H1s, image alt text and 404 redirects are written by the model.
   Mechanical issues never touch the model (no key needed) and are built from the crawl evidence instead
   (`spider/fixkinds.py`): protocol-relative and mixed-content resources, unsafe target=_blank links, insecure forms,
   broken #bookmarks, canonical / title / meta / H1 markup problems, URL rewrites with the inlinks to update, links
   pointing at redirects (with the final URL), image size and dimensions, each with exact before → after snippets.
   Markup that recurs on most affected pages is recognised as a **template** problem and becomes one fix for the
   theme instead of hundreds of copies; security headers and HTTP→HTTPS become one **site-level** fix with the server
   config snippet (nginx / Apache / Cloudflare / IIS / WordPress, detected from response headers). Remaining issues
   get an AI brief that is fed the same per-page evidence so it cannot fall back to generic advice.
3. **Fixes** opens the review queue: edit the proposal inline, approve/reject per row or in bulk, export to
   Excel/CSV/JSON.
4. **Config → Connectors**: add push targets — WordPress (REST API + application password), WordPress XML-RPC, or
   a Webhook for any custom site/CMS (your endpoint receives the approved fixes as JSON with an HMAC signature).
   Each target is `staging` or `live`; pushing to a live target requires typing its name. Every push result is
   recorded on the fix row. Add more platforms by subclassing `Connector` in `spider/connectors.py`.

`tests/mock_ai.py` runs a fake provider + webhook receiver so the whole flow can be tried without a key.

## Internal links API (read by the Content Pilot app)

The app's Internal links module reads the crawl through three endpoints in `spider/links.py` + `spider/server.py`,
for the open crawl or a named one (`crawl=<file name>` in `crawls/`). Set `SPIDER_API_KEY` in the environment of a
hosted Spider and every path (API, UI, `/mcp`) needs the same value in the `X-Spider-Key` header, except calls from
the box itself with no forwarding header (the scheduler, MCP, an SSH port forward); `tests/check_apikey.py` covers it.
The app's Site audit page frames the interface for its admins with a one-hour sign-in signed from the same key
(`/?token=` sets the `cp_spider_ui` cookie; the cookie works on every path but `/mcp`, and writes with it need this
host's Origin). The desktop app has no key and answers 127.0.0.1 only. Hosting runbook: `contentpilot/docs/SPIDER-HOSTING.md`.

- `GET /api/graph?crawl=&page=1&size=1000`: internal HTML pages (id, url, status, indexable, canonical, depth,
  inlinks, outlinks, link_score, word_count, sitemap and orphan flags, title, h1, Search Console figures when
  fetched) and the hyperlinks between them (from, to, anchor, position, follow), 1,000 pages a call.
- `GET /api/prominent?crawl=&min_occ=4&top=100`: prominent terms per indexable page (`spider/prominent.py`, Yoast's
  method: stemmed terms, title, H1, meta description and H2 words counted three times, at least `min_occ`
  occurrences, the top `top` terms, capped at 300); `tests/check_prominent.py`.
- `POST /api/mentions` `{crawl, phrases: [{id, text, variants[], target_id}], exclude_pairs: [[from, to]],
  per_phrase: 50, min_words: 150}`: where each phrase or variant is mentioned in the stored HTML of indexable 200
  pages, with the sentence, the block kind (paragraph, list, table, heading, quote, caption, code), the occurrence
  number and whether the mention is already inside a link; a hit is `linkable` only in a paragraph, list item or
  table cell outside a link. Navigation, header, footer, sidebar and form text are never scanned. The tokenizer
  is a port of the app's `src/lib/perf/pageText.ts` (stemmed, possessives dropped, stop words forgiven).
- `POST /api/crawl/url` `{url, expect: [{href, anchor}]}`: fetch one page now and report its status, hyperlinks and
  whether each expected link is present (href resolved like the crawler resolves it, anchor matched by tokens);
  the day-7 verify step of a shipped link batch.

`tests\check_links.py` covers the tokenizer, the block walker, the three endpoints against `tests/smoke.db` and a
page served from the test itself.

## Report API (read by the Content Pilot app)

Every report endpoint answers for the open crawl, or, with `crawl=<file name>` (a file in `crawls/`), for any saved
crawl: read-only, with the settings that crawl was made with, without opening it. Same shapes either way;
`tests/check_report.py` checks that a named read equals the same crawl opened. A name outside `crawls/` or unknown
answers 404. With `SPIDER_API_KEY` set they need `X-Spider-Key` like every route.

- `GET /api/issues?crawl=`: `{issues: [{key, tab, name, kind, priority, count, desc, fix}]}`, only issues with a
  count, High first then by count. `kind` is issue, warning or opportunity; `key` is `tab:filter`.
- `GET /api/tabs?crawl=`: every tab with every filter and its count (`{tabs: [{key, name, filters: [{key, name, kind,
  priority, count, desc, fix, issue_only, fixKind}]}], columns: {tab: [{field, title}]}}`).
- `GET /api/urls?crawl=&key=internal:all&page=1&size=200&sort=&dir=asc&q=&filters=`: one page of a filter's URLs
  (`key` = any filter key, so an issue's affected URLs are `key=<issue key>`), `{data: [{id, url, ...the tab's
  columns}], last_page, total, columns}`; `size` at most 1,000; `q` searches the URL and text columns; `filters` is
  the grid's JSON list of column filters. The internal tab's columns include status_code, indexable,
  indexability_status, title, title_len, meta_desc, h1, word_count, depth, inlinks, unique_inlinks, outlinks,
  link_score, canonical, response_time and size.
- `GET /api/urls/values?crawl=&key=&field=`: the distinct values of one column with counts, for a filter menu.
- `GET /api/url/{id}?crawl=`: one URL: every stored field, `headers` parsed, `inlinks` and `outlinks` (source,
  destination, anchor, alt, follow, rel, position, status), `images`, `resources`, `issues` (keys) and
  `issue_names`, `uncrawlable`, exact and near duplicates, spelling, Lighthouse, structured data, sitemaps.
- `GET /api/url/{id}/source?crawl=&kind=html|text|rendered`: the stored source.

## Hosted pacing and the crawl queue

With `SPIDER_API_KEY` set (the hosted Spider) every crawl is paced and crawls queue instead of colliding:

- Pace: `config.max_urls_per_second` defaults to 0.3 (about 500 URLs in 28 minutes) and is clamped to 0.05 to 2.0;
  `config.max_threads` (parallel connections) defaults to 1 and is clamped to 2; `render_concurrency` to 2. Only
  the values in the start request count, never a faster pace left in the shared settings.
- `POST /api/crawl/start` answers 200 `{ok, queued: false, id, state: "running", db, crawl, position: 0, ...}` when
  the box is free, else 202 `{ok, queued: true, id, state: "queued", position, ...}`; a start for a host that
  already has a waiting job returns that job with `duplicate: true`; 429 when 100 jobs wait. Every job carries
  `id, state, start_url, host, mode, created_at, started_at, finished_at, db, crawl, error, crawled, position,
  max_urls, max_urls_per_second, max_threads`, plus `progress` while it runs. States: queued, running, finished,
  stopped, error, cancelled, interrupted (the service restarted mid-crawl; the partial file is kept).
- `GET /api/crawl/jobs/{id}`, `GET /api/crawl/jobs` (newest first, the last 200 ended kept),
  `POST /api/crawl/jobs/{id}/cancel` (drops a waiting job, stops a running one). `GET /api/crawl/status` adds
  `jobs: {running: <id or null>, waiting: [{id, start_url, host, position, created_at}]}`.
- The queue is kept in `crawls/jobs.json`, so waiting jobs survive a restart. One crawl runs at a time.
- The desktop app (no key) is unchanged: no pacing, and a second start answers 409.

## Scheduling and the command line

**? Help → Scheduled crawls** creates schedules: a site, hourly / daily / weekly / once, max URLs, and what to do when
the crawl finishes — export the issues pack or everything to a folder, compare with the previous crawl, audit the top
N pages with Lighthouse, fetch Search Console + GA4; the crawl-complete email follows Config → Email. Schedules run
while the app is open; `python cli.py schedule install-task` adds a Windows Task Scheduler job that runs due
schedules every 15 minutes even when it is closed (a headless server is started for the run). Each run's result
(URLs, issue types, export path, compare summary) is kept in the schedule's history.

`cli.py` drives everything from a terminal or CI and talks to the running app (or starts a headless one):

```
.venv\Scripts\python.exe cli.py crawl https://example.com --max-urls 500 --export issues --out C:\reports --fail-on-high
.venv\Scripts\python.exe cli.py list | open NAME | issues | export | report NAME | sitemap | compare [A] [B] | mcp
.venv\Scripts\python.exe cli.py schedule list | add --url … --frequency daily --time 03:00 | run --id … | run-due | install-task
```

Exit code 2 with `--fail-on-high` when the crawl has High-priority issues, so a pipeline can fail on regressions.
MCP: `schedules_list`, `schedule_add`, `schedule_run`, `schedule_remove`.

## MCP server (drive it from Claude Desktop, Claude Code or Cursor)

The whole app is available as 53 MCP tools: start/stop crawls and wait for them, list issues and filters with counts,
query any filter, inspect a URL (inlinks, outlinks, headers, issues), run reports, bulk-export CSV/XLSX/zip files,
and generate, review, approve and push AI fixes. Local only. Two transports share one tool set:

- **HTTP** (preferred while the app is open): `http://127.0.0.1:7743/mcp`. The app binds a fixed port (Config → MCP
  → App port, default 7743) so clients always find it. `claude mcp add --transport http content-pilot-spider http://127.0.0.1:7743/mcp`
- **stdio**: `.venv\Scripts\python.exe mcp_server.py`. Attaches to the running app, or starts a headless copy of the
  server (the desktop app then attaches to that one when launched). `mcp_server.py --tools` prints the tool list.

**Config → MCP** shows the live endpoint, an on/off switch, copy-paste snippets for Claude Code, Claude Desktop and
Cursor, and **View tools** (the tool list as Markdown). Every tool is a thin wrapper over the HTTP API in
`spider/server.py`, so MCP clients can do exactly what the UI can and nothing more; pushes to a LIVE connector still
require the target name as confirmation. `tests\check_mcp.py` exercises every tool over both transports.



## Tests

`tests\run_all.py` runs every suite (the `inspect_*.py` scripts are ad hoc data explorers, not tests) (`--quick` skips the ones that crawl live sites or run Lighthouse). Each
`check_*.py` is self-contained: it starts its own server on a free port, uses scratch settings so your real
`settings.json` is never touched, and cleans up what it writes. Suites that run a live crawl set `SPIDER_CRAWLS_DIR` to a scratch folder under
`%TEMP%\claude`, so test crawls never appear in your crawl list. `check_windowless.py` is the Windows regression
test for the way the app is actually launched (pythonw, no console): it fails if any console window appears during a
Lighthouse audit, and it fails if an emoji or pictograph creeps into the interface files.

## UI design system

Tokens (colour for light/dark, type scale, spacing, radius, shadow) live in the `:root` block at the top of
`ui/styles.css`. Icons are an inline SVG stroke set in `ui/icons.js` (`<i data-ic="name">` in markup, `ic("name")` in
scripts); the interface uses no emoji or pictographic characters anywhere. Loading is designed, not spun: a splash on boot, skeleton rows/pairs/lists while data loads,
`busy` buttons, an indeterminate progress bar while analysing, empty states that name the next action, and toasts
instead of `alert()`.

## Layout

```
run.py              desktop launcher (uvicorn thread + pywebview window)
spider/config.py    CrawlConfig (all settings + thresholds)
spider/crawler.py   async crawl loop, robots, sitemaps, redirects, scope rules
spider/fetch.py     httpx fetching (no auto-redirects: every hop is a record)
spider/parse.py     HTML extraction (titles, metas, headings, links, canonicals, hreflang, resources, validation)
spider/analysis.py  graph-wide analysis (inlinks, duplicates, chains, hreflang, link score, sitemaps)
spider/issues.py    tab/filter registry (SQL predicate per filter) + grid columns
spider/exports.py   CSV/XLSX + reports
spider/server.py    FastAPI API (+ /mcp Streamable HTTP endpoint)
spider/mcp_server.py MCP tool set (thin wrapper over the API); mcp_server.py in the root = stdio launcher
spider/instance.py  fixed port + .port file so launchers find a running server
ui/                 index.html, app.js, styles.css (Tabulator grid)
tests/              smoke_crawl.py URL [max]  - headless crawl + filter counts; check_mcp.py - MCP over HTTP + stdio
```

## Notes

- The crawl total counts every discovered URL: pages, images (every `srcset` size), JS/CSS and external links,
  like Screaming Frog. The progress bar shows the breakdown. Social share-intent links (facebook/twitter/linkedin
  share URLs) are recorded but not fetched by default (Config > Crawl).
- Robots.txt is checked for external hosts too (same default as Screaming Frog), so external URLs on sites that
  disallow bots show as "Blocked by robots.txt". Turn off in Config > Advanced if you only want status codes.
- Analysis runs on the server thread; on very large crawls (50k+ URLs) the UI may pause for a few seconds every 15s.
- Screaming Frog is a trademark of Screaming Frog Ltd. This project is not affiliated with it.
