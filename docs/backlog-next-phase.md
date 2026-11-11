# Next phase backlog (saved 14 Oct 2026)

Source: Screaming Frog v24.x release notes, pasted by Arun. Mapped to what it means for Content Pilot Spider.
Items marked *n/a* are Java/packaging concerns that do not apply to this app.

## 1. AI & automation
- [x] **Built-in MCP server** — done 15 Oct 2026. `spider/mcp_server.py` (32 tools: crawl start/wait/control,
      crawls, issues/filters/query, URL details/source, reports, bulk exports, fixes generate/review/push, config) as a
      thin wrapper over the HTTP API; Streamable HTTP at `/mcp` on the app's fixed port (7743, Config → MCP, on/off
      switch) plus `mcp_server.py` stdio launcher that attaches to the running app or starts a headless one; Config →
      MCP panel with client snippets and "View tools" Markdown (`/api/mcp/tools.md`). Tests: `tests/check_mcp.py`.
      Not done: "auto-start on launch" is implicit (HTTP endpoint is always up while the app runs).
- [x] **System prompt per AI provider** — done 15 Oct 2026: `ai_system_prompts` {provider: prompt} in CrawlConfig,
      "Advanced: system prompt for this provider" box in Config → AI (swaps with the provider dropdown); replaces the
      default persona in `fixes._system_prompt`, task/brand/house-style/JSON rules still appended.
- [x] **Live model validation** — done 15 Oct 2026: `ai.list_models(cfg)` (Anthropic /v1/models paginated, OpenAI
      and compatible /models, Gemini models, Ollama /api/tags), `GET /api/ai/models`, model box has a datalist +
      ✓/✗ status line (debounced on provider/key/URL change, ↻ button), Test connection and Save warn when the name is
      not listed. MCP tools `ai_models`, `ai_test` (34 tools now).

## 2. Reporting & project tracking
- [x] **Auto compare crawls** — done 15 Oct 2026: `spider/compare.py` (pages added/removed/changed with per-field
      before→after, issue counts before/after, new/fixed), `GET /api/compare?a&b` (defaults: open crawl vs previous
      completed crawl of the same site) + `/api/compare/export` xlsx, ⇄ Compare modal (tiles, Issues/Changed/Added/
      Removed tabs, issue rows jump to the filter), auto toast after every finished crawl, MCP `compare_crawls` /
      `export_compare`. Scheduled/CLI runs will reuse `/api/compare` when phase 3 adds them. Test: tests/check_compare.py.
- [x] **Crawl-complete email with full issue table** — done 15 Oct 2026: `spider/notify.py` (text + HTML, issue
      table with fix hints, compare summary vs previous crawl), Config → Email (SMTP host/port/security/user/pass,
      recipients, test button, validation), sent from `_after_crawl` when a crawl task ends, `/api/email/test`,
      `/api/email/last`, MCP `email_test`. Test: tests/check_email.py with tests/mock_smtp.py.
- [x] **Skip empty reports** (already the behaviour: bundle, Everything workbook and per-filter sheets drop empty ones; single-report download keeps its header row)
- [x] **Usage stats** — done 15 Oct 2026: `spider/usage.py` (usage.json; crawls recorded in `_after_crawl`, AI
      tokens per Provider request recorded per fix job / test call, price table with Anthropic defaults + user
      overrides), `GET /api/usage`, `POST /api/usage/prices`, Help → Usage & costs modal (Cumulative / Per run /
      Prices tabs), MCP `usage_stats`. Test: tests/check_usage.py. Section 2 complete.

## 3. Integrations & data
- [x] **Ahrefs / Moz / Majestic link metrics** — done 16 Oct 2026 (`spider/linkmetrics.py`, Link Metrics tab + 7 filters, ☁ Data menu, Config → Link metrics with credit cap + auto option, MCP `linkmetrics_run`; tests/check_linkmetrics.py with a mock of all three APIs). Ahrefs country option included; volume mode not applicable to the metrics endpoints used. **Phase 3 complete.**
- [ ] **Interactive content cluster graph** — when embeddings/clusters land (phase 2 near-duplicates), legend
      items toggle cluster visibility.
- [x] **Rich results validation**: skip Google FAQ (deprecated); keep the rest of the rich-result set. (done with structured data, 15 Oct 2026)
- [x] **Accessibility**: axe-core via Lighthouse 12.8 (done with the Lighthouse integration, 16 Oct 2026).
- [x] **GSC read timeout**: 120 s on Search Analytics / GA4 report calls (done with the integration).

## 4. Architecture
- *n/a* Arm64 Linux packages, JVM/RDP fixes, Java 21 — not applicable (Python app). Equivalent for us: a
  one-file installer / portable zip for Windows, and a Linux/macOS launch script.

## Still open from earlier phases
- Phase 2: ~~near-duplicates (MinHash)~~ done 15 Oct 2026 (`spider/neardup.py`, `near_dups` table, Content → Near
  Duplicates filter with threshold, Duplicates lower pane, closest match column, tests/check_neardup.py), ~~spelling & grammar~~
  done 15 Oct 2026 (`spider/spelling.py`: pyspellchecker + UK/inflection-aware `known()`, learnt site vocabulary, rule
  grammar or LanguageTool; `spelling` table, Content filters, pane with Add to dictionary, report, `/api/spelling/run|ignore`,
  MCP `spelling_run`; text extraction now keeps block newlines; tests/check_spelling.py), ~~structured data + Google rich results~~ done 15 Oct 2026
  (`spider/structured.py`: own JSON-LD parsing (parse errors) + extruct for Microdata/RDFa, bundled schema.org vocabulary
  in spider/data/, Google required/recommended table (FAQ skipped), `structured_data` table + sd_* columns, Structured
  Data tab/filters/pane/report, Config → Crawl toggles; tests/check_structured.py), ~~AMP~~ done 15-16 Oct 2026 (`spider/amp.py`, AMP tab with 11 filters, amp_* columns; tests/check_amp.py), ~~XML sitemap generation~~ done 16 Oct 2026 (`spider/sitemapgen.py`, Export → XML sitemap… modal with preview, `/api/sitemap/preview|generate`, MCP `export_sitemap`; tests/check_sitemap.py), ~~crawl visualisations~~ done 16 Oct 2026 (`spider/viz.py` + D3/d3-cloud vendored, ◉ Visualise modal with 6 views, MCP `crawl_tree` / `word_frequencies`; tests/check_viz.py). **Phase 2 complete.**
  (`@adobe/structured-data-validator`), AMP, XML sitemap generation, crawl visualisations.
- Phase 3: ~~Lighthouse (PageSpeed / Mobile / Accessibility)~~ done 16 Oct 2026 (`spider/lighthouse.py`, vendored lighthouse 12.8 CLI + Playwright Chromium or PSI API, PageSpeed + Accessibility tabs, pane, auto-run option, MCP `lighthouse_run`; tests/check_lighthouse.py), ~~Search Console + GA4~~ done 16 Oct 2026 (`spider/google.py`: OAuth desktop loopback flow, Search Analytics + GA4 Data API + URL Inspection, orphan rows crawled=5, Search Console / Analytics tabs, G Google menu, Config panel with property pickers, MCP `google_fetch`; tests/check_google.py + tests/mock_google.py), ~~Compare mode~~ (done), ~~scheduling, CLI~~ done 16 Oct 2026 (`spider/schedule.py` + `cli.py`: schedules.json, in-app scheduler loop, Windows task via schtasks, Help → Scheduled crawls modal, MCP schedule tools; tests/check_schedule.py).
- Phase 4: self-hosted multi-user mode (auth, per-user crawls, Docker) for hosting on a domain.
- Repo: `git init` + first commit / GitHub push — Arun has not yet said which.

## Fix engine (Arun, 16 Oct 2026: "Fix with AI returned the same solution for every URL on protocol-relative links")
- [x] Evidence-based fixes: `spider/fixkinds.py` — rule kinds code / site / url / redirect_link / image with exact
      snippets from stored HTML, headers and the link graph; template detection (shared markup → one fix); site-level
      header / HTTPS fixes with server snippets; AI briefs now receive per-page `evidence`. Tests: tests/check_fixkinds.py.

## Site Audit dashboard + Content Pilot launcher (Arun, 16 Oct 2026)
- [x] `spider/audit.py` health formula, themes, crawled-pages breakdown, trend (audit_summary cached per crawl file), Markdown report; 📊 Audit modal; `/api/audit*`; MCP `site_audit`.
- [x] Content Pilot: `src/components/dashboard/SpiderLauncher.tsx` on /dashboard/audit (NEXT_PUBLIC_SPIDER_URL, default http://127.0.0.1:7743); spider CORS/PNA for contentpilot.cloud.
      Arun will delete Content Pilot's old audit crawler himself. Hosted spider = phase 4, same button, different URL.
