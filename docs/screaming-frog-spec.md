# Screaming Frog SEO Spider v24.3 — feature inventory (build spec)

Compiled 14 Oct 2026 from the official user guide (Tabs, Issues, Configuration, General pages).
This is the checklist the local spider is built against. Status column is maintained by hand.

Status key: `[x]` done · `[~]` partial · `[ ]` not started · `P2`/`P3` planned phase.

## 1. Tabs and filters (top window)

- **Internal / External** — filter by content type: HTML, JavaScript, CSS, Images, PDF, Flash, Other, Unknown.
- **Security** — HTTP URLs, HTTPS URLs, Mixed Content, Form URL Insecure, Form on HTTP URL, Unsafe Cross-Origin Links, Protocol-Relative Resource Links, Missing HSTS Header, Missing Content-Security-Policy Header, Missing X-Content-Type-Options Header, Missing X-Frame-Options Header, Missing Secure Referrer-Policy Header, Bad Content Type.
- **Response Codes** — Blocked by Robots.txt, Blocked Resource, No Response, Success (2XX), Redirection (3XX), Redirection (JavaScript), Redirection (Meta Refresh), Redirection (HTTP Refresh), Redirect Chain, Redirect Loop, Client Error (4xx), Server Error (5XX).
- **URL** — Non ASCII Characters, Underscores, Uppercase, Multiple Slashes, Repetitive Path, Contains A Space, Internal Search, Parameters, Broken Bookmark, GA Tracking Parameters, Over 115 Characters.
- **Page Titles** — Missing, Duplicate, Over 60 Characters, Below 30 Characters, Over 561 Pixels, Below 200 Pixels, Same as H1, Multiple, Outside `<head>`.
- **Meta Description** — Missing, Duplicate, Over 155 Characters, Below 70 Characters, Over 985 Pixels, Below 400 Pixels, Multiple, Outside `<head>`.
- **Meta Keywords** — Missing, Duplicate, Multiple.
- **H1** — Missing, Duplicate, Over 70 Characters, Multiple, Alt Text in h1, Non-sequential.
- **H2** — Missing, Duplicate, Over 70 Characters, Multiple, Non-sequential.
- **Content** — Exact Duplicates, Near Duplicates, Low Content Pages, Soft 404 Pages, Spelling Errors, Grammar Errors, Readability Difficult, Readability Very Difficult, Lorem Ipsum Placeholder, Semantically Similar, Low Relevance Content.
- **Images** — Over 100kb, Missing Alt Text, Missing Alt Attribute, Alt Text Over 100 Characters, Background Images, Missing Size Attributes, Incorrectly Sized Images.
- **Canonicals** — Contains Canonical, Self Referencing, Canonicalised, Missing, Multiple, Multiple Conflicting, Non-Indexable Canonical, Canonical Is Relative, Unlinked, Invalid Attribute In Annotation, Contains Fragment URL, Outside `<head>`.
- **Pagination** — Contains Pagination, First Page, Paginated 2+ Pages, Pagination URL Not In Anchor Tag, Non-200 Pagination URL, Unlinked Pagination URL, Non-Indexable, Multiple Pagination URLs, Pagination Loop, Sequence Error.
- **Directives** — Index, Noindex, Follow, Nofollow, None, NoArchive, NoSnippet, Max-Snippet, Max-Image-Preview, Max-Video-Preview, NoODP, NoYDIR, NoImageIndex, NoTranslate, Unavailable_After, Refresh, Outside `<head>`.
- **Hreflang** — Contains Hreflang, Non-200 Hreflang URLs, Unlinked Hreflang URLs, Missing Return Links, Inconsistent Language & Region Return Links, Non-Canonical Return Links, Noindex Return Links, Incorrect Language & Region Codes, Multiple Entries, Missing Self Reference, Not Using Canonical, Missing X-Default, Missing, Outside `<head>`.
- **JavaScript** (P2) — Pages with Blocked Resources, Contains JavaScript Links, Contains JavaScript Content, Noindex Only in Original HTML, Nofollow Only in Original HTML, Canonical Only in Rendered HTML, Canonical Mismatch, Page Title Only in Rendered HTML, Page Title Updated by JavaScript, Meta Description Only in Rendered HTML, Meta Description Updated by JavaScript, H1 Only in Rendered HTML, H1 Updated by JavaScript, Uses Old AJAX Crawling Scheme URLs, Uses Old AJAX Crawling Scheme Meta Fragment Tag, Pages with JavaScript Errors.
- **Links** — Pages With High Crawl Depth, Pages Without Internal Outlinks, Internal Nofollow Outlinks, Internal Outlinks With No Anchor Text, Non-Descriptive Anchor Text In Internal Outlinks, Pages With High External Outlinks, Pages With High Internal Outlinks, Follow & Nofollow Internal Inlinks To Page, Internal Nofollow Inlinks Only, Outlinks To Localhost, Non-Indexable Page Inlinks Only, Pages With Uncrawlable Internal Outlinks.
- **AMP** (P2) — Non-200 Response, Missing Non-AMP Return Link, Missing Canonical to Non-AMP, Non-Indexable Canonical, Indexable, Non-Indexable; validation: Missing `<html amp>` Tag, Missing/Invalid Doctype, Missing Head Tag, Missing Body Tag, Missing Canonical, Missing/Invalid Meta Charset, Missing/Invalid Meta Viewport, Missing/Invalid AMP Script, Missing/Invalid AMP Boilerplate, Contains Disallowed HTML, Other Validation Errors.
- **Structured Data** (P2) — Contains Structured Data, Missing Structured Data, Validation Errors, Validation Warnings, Parse Errors, Microdata URLs, JSON-LD URLs, RDFa URLs, Rich Result Validation Errors / Warnings.
- **Sitemaps** — URLs In Sitemap, URLs Not In Sitemap, Orphan URLs, Non-Indexable URLs in Sitemap, URLs In Multiple Sitemaps, XML Sitemap With Over 50k URLs, XML Sitemap With Over 50mb.
- **PageSpeed** (P3, Lighthouse) — Document Request Latency, LCP Request Discovery, Render Blocking Requests, Network Dependency Tree, Use Efficient Cache Lifetimes, Layout Shift Culprits, Improve Image Delivery, Forced Reflow, Legacy JavaScript, Duplicated JavaScript, Avoid Enormous Network Payloads, Minify CSS, Minify JavaScript, Reduce Unused CSS, Reduce Unused JavaScript, Reduce JavaScript Execution Time, Minimize Main-Thread Work, Optimize DOM Size, Font Display.
- **Mobile** (P3) — Viewport Not Set, Target Size, Content Not Sized Correctly, Illegible Font Size, Unsupported Plugins, Mobile Alternate Link.
- **Accessibility** (P3, axe-core via Lighthouse) — score bands + ~92 rules across Best Practice, WCAG 2.0 A/AA/AAA, 2.1 AA, 2.2 AA.
- **Custom Search / Custom Extraction / Custom JavaScript / AI** — one dynamic filter per configured item.
- **Analytics** (P3, GA4) — Sessions Above 0, Bounce Rate Above 70%, No GA Data, Non-Indexable with GA Data, Orphan URLs.
- **Search Console** (P3) — Clicks Above 0, No Search Analytics Data, Non-Indexable with Search Analytics Data, Orphan URLs, URL Is Not on Google, Indexable URL Not Indexed, URL is on Google But Has Issues, User-Declared Canonical Not Selected, Page Is Not Mobile Friendly, AMP URL Is Invalid, Rich Result Invalid.
- **Validation** — Invalid HTML Elements In `<head>`, `<body>` Element Preceding `<html>`, `<head>` Not First In `<html>`, Missing `<head>`, Multiple `<head>`, Missing `<body>`, Multiple `<body>`, HTML Document Over 2MB, Resource Over 2MB, High Carbon Rating.
- **Link Metrics** (P3) — Majestic / Ahrefs / Moz columns.
- **Change Detection** (P3, Compare mode) — Indexability, Page Titles, Meta Description, H1, Word Count, Crawl Depth, Inlinks, Unique Inlinks, Internal Outlinks, Unique Internal Outlinks, External Outlinks, Unique External Outlinks, Structured Data Unique Types, Content.

## 2. Issues (type / priority)

Response Codes: Internal No Response, Internal 4XX, Internal 5XX, Internal Redirect Loop — Issue/High; Internal Blocked by Robots.txt, Internal Blocked Resource — Warning/High; Internal Redirect Chain, External Blocked Resource — Warning/Med; Internal 3XX / Meta Refresh / HTTP Refresh / JavaScript, External No Response / 4XX / 5XX — Warning/Low.
Security: HTTP URLs, Mixed Content, Form URL Insecure, Form On HTTP URL — Issue/High; all header/cross-origin/protocol-relative/bad content type — Warning/Low.
URL: Multiple Slashes, Contains A Space, Broken Bookmark — Issue/Low; Non ASCII, Uppercase, Repetitive Path, Internal Search, Parameters, GA Tracking Parameters — Warning/Low; Underscores, Over 115 Characters — Opportunity/Low.
Page Titles: Missing, Multiple, Outside head — Issue/High; Duplicate, Over 60, Below 30, Over 561px, Below 200px — Opportunity/Med; Same as H1 — Opportunity/Low.
Meta Description: Multiple, Outside head — Issue/Med; Missing, Duplicate, Over 155, Below 70, Over 985px, Below 400px — Opportunity/Low.
H1: Missing — Issue/Med; Multiple — Warning/Med; Alt Text in h1, Non-sequential — Warning/Low; Duplicate, Over 70 — Opportunity/Low.
H2: Missing, Multiple, Non-sequential — Warning/Low; Duplicate, Over 70 — Opportunity/Low.
Content: Exact Duplicates — Issue/High; Spelling, Grammar — Issue/Med; Soft 404, Lorem Ipsum — Warning/High; Near Duplicates, Semantically Similar — Warning/Med; Low Relevance — Warning/Low; Low Content — Opportunity/Med; Readability Difficult / Very Difficult — Opportunity/Low.
Images: Missing Alt Text, Missing Alt Attribute — Issue/Low; Background Images — Warning/Low; Over 100kb — Opportunity/Med; Alt > 100, Incorrectly Sized, Missing Size Attributes — Opportunity/Low.
Canonicals: Multiple Conflicting, Non-Indexable Canonical, Invalid Attribute, Contains Fragment, Outside head — Issue/High; Canonicalised — Warning/High; Missing, Unlinked — Warning/Med; Multiple, Relative — Warning/Low.
Pagination: Not In Anchor Tag, Non-200 — Issue/High; Unlinked — Issue/Med; Multiple, Loop, Sequence Error — Issue/Low; Non-Indexable — Warning/High.
Directives: Outside head — Issue/High; NoImageIndex — Issue/Low; Noindex, Nofollow, None — Warning/High; Unavailable_After — Warning/Med; NoSnippet, NoODP, NoYDIR, NoTranslate — Warning/Low.
Hreflang: Non-200, Missing Return Links, Inconsistent Return Links, Non-Canonical Return Links, Noindex Return Links, Incorrect Codes, Multiple Entries, Not Using Canonical, Outside head — Issue/High; Unlinked — Issue/Med; Missing Self Reference, Missing X-Default — Warning/Low.
JavaScript: Noindex Only in Original HTML, Nofollow Only in Original HTML, Canonical Mismatch — Issue/High; Old AJAX scheme (2) — Issue/Med; Blocked Resources — Warning/High; JS links/content, title/meta/H1 only-in-rendered or updated — Warning/Med; Canonical Only in Rendered, JS Errors — Warning/Low.
Links: Outlinks To Localhost — Issue/High; Uncrawlable Internal Outlinks, Without Internal Outlinks, Non-Indexable Page Inlinks Only — Warning/High; Nofollow Outlinks, High External, High Internal, Follow & Nofollow Inlinks, Nofollow Inlinks Only — Warning/Low; High Crawl Depth — Opportunity/Med; No Anchor Text, Non-Descriptive Anchor — Opportunity/Low.
AMP: all 15 validation/SEO — Issue/High; Indexable — Warning/High.
Structured Data: Validation Errors, Rich Result Errors, Parse Errors — Issue/High; Missing, Validation Warnings, Rich Result Warnings — Opportunity/Low.
Sitemaps: Over 50k URLs, Over 50mb — Issue/High; Not In Sitemap, Orphan URLs, Non-Indexable In Sitemap — Issue/Med; In Multiple Sitemaps — Warning/Low.
PageSpeed: all — Opportunity/Med (DOM Size, Font Display — Low).
Mobile: Viewport Not Set, Content Not Sized, Illegible Font, Unsupported Plugins — Issue/High; Target Size — Issue/Med; Mobile Alternate Link — Warning/High.
Validation: Missing/Multiple head, Missing/Multiple body, Document > 2MB, Resource > 2MB — Issue/High; Invalid Elements In head, body Before html, head Not First — Warning/High; High Carbon Rating — Opportunity/Low.

## 3. Lower window tabs
URL Details, Inlinks, Outlinks, Image Details, Duplicate Details, Resources, SERP Snippet, Rendered Page, View Source, HTTP Headers, Cookies, Structured Data Details, Lighthouse Details, Accessibility Details, Spelling & Grammar Details, N-grams.
Right sidebar: Overview, Issues, Site Structure, Segments, Response Times, API, Spelling & Grammar, Semantic Search.

## 4. Configuration groups
Spider > Crawl (store/crawl toggles for images, media, CSS, JS, SWF, internal/external links, canonicals, pagination, hreflang, AMP, meta refresh, iframes, mobile alternate; crawl outside start folder; all subdomains; follow nofollow; crawl linked sitemaps).
Spider > Extraction (page details, URL details, directives, structured data, store HTML / rendered HTML, PDF).
Spider > Limits (total, depth, URLs per depth, folder depth, query strings, per subdomain, max redirects, URL length, links per URL, page size, by path).
Spider > Rendering (text only / JavaScript; screenshots; JS error reporting; flatten shadow DOM / iframes; archive; AJAX timeout; window size).
Spider > Advanced (cookies; ignore non-indexable for issues; ignore paginated for duplicates; always follow redirects/canonicals; respect noindex/canonical/next-prev/HSTS/self-ref meta refresh; srcset; fragments; HTML validation; carbon; assume HTML; timeout; 5XX retries).
Spider > Preferences (title/meta char & pixel limits; links thresholds; non-descriptive anchor list; URL/H1/H2/alt length; max image size; low content words).
Content (area include/exclude; duplicates threshold; spelling & grammar; embeddings). Robots.txt. URL Rewriting. CDNs. Include/Exclude. Speed. User-Agent. HTTP Header. Custom Search/Extraction/Link Positions/JavaScript. API Access (GA4, GSC, PSI, Majestic, Ahrefs, Moz, OpenAI/Gemini/Ollama/Anthropic). Authentication. Segments. Crawl Analysis. System settings (theme, language, proxy, storage mode, memory, retention, certificates, notifications, MCP server). Config profiles.

## 5. Modes, bulk export, reports
Modes: Spider, List, SERP, Compare, APIs.
Bulk Export: Queued URLs; Links (all inlinks/outlinks/anchor text, external, nofollow, no anchor, non-descriptive, localhost…); Web (screenshots, archive, page source, page text, PDFs, request/response headers, cookies); Path Type; per-tab "inlinks to URLs in filter"; URL Inspection; Accessibility; Issues; AI. Multi-Export presets.
Reports: Crawl Overview; Redirects (all, chains, redirect & canonical chains, redirects to errors); Canonicals (chains, non-indexable); Pagination; Hreflang (7); Insecure Content; SERP Summary; Orphan Pages; Structured Data (4); PageSpeed (4); Mobile; Accessibility Summary; HTTP Header Summary; Cookie Summary; Crawl Path Report.
Formats: CSV, XLS, XLSX, Google Sheets; Looker Studio overview.

## 6. Visualisations
Force-Directed Crawl Diagram (2D/3D), Crawl Tree Graph, Force-Directed Directory Tree (2D/3D), Directory Tree Graph, Inlink Anchor Text Word Cloud, Body Text Word Cloud, Content Cluster Diagram.

## 7. Other
Scheduling; database storage mode + crawl manager; crawl comparison; XML / image sitemap generation (options: noindex, canonicalised, paginated, PDFs, lastmod, priority, changefreq, images); spelling & grammar; near duplicates (minhash) + exact (hash); AI prompts + semantic search + MCP server; accessibility via axe-core; CLI flags (--crawl, --crawl-list, --headless, --save-crawl, --output-folder, --export-tabs, --bulk-export, --save-report, --create-sitemap, --export-format…); UI (light/dark, column config, focus mode, global search); Log File Analyser (separate product).
