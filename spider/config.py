"""Crawl configuration. Mirrors the Screaming Frog Config > Spider groups."""
from __future__ import annotations

from dataclasses import dataclass, field, asdict
from typing import Any

DEFAULT_UA = (
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) "
    "Chrome/130.0.0.0 Safari/537.36 LocalSEOSpider/0.1"
)

USER_AGENTS = {
    "Local SEO Spider": DEFAULT_UA,
    "Googlebot (Desktop)": "Mozilla/5.0 (compatible; Googlebot/2.1; +http://www.google.com/bot.html)",
    "Googlebot (Smartphone)": (
        "Mozilla/5.0 (Linux; Android 6.0.1; Nexus 5X Build/MMB29P) AppleWebKit/537.36 (KHTML, like Gecko) "
        "Chrome/130.0.0.0 Mobile Safari/537.36 (compatible; Googlebot/2.1; +http://www.google.com/bot.html)"
    ),
    "Bingbot": "Mozilla/5.0 (compatible; bingbot/2.0; +http://www.bing.com/bingbot.htm)",
    "Chrome (Desktop)": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/130.0.0.0 Safari/537.36",
    "Screaming Frog": "Screaming Frog SEO Spider/22.0",
}

NON_DESCRIPTIVE_ANCHORS = [
    "click here", "here", "read more", "learn more", "more", "link", "this", "this page",
    "continue", "continue reading", "view", "view more", "see more", "details", "go", "find out more",
]

INTERNAL_SEARCH_PARAMS = ["q", "s", "search", "query", "keyword", "keywords", "term", "search_query"]


# settings that never go into a crawl file (a crawl .db is something people share)
CRAWL_FILE_SECRETS = frozenset({"ai_api_key", "targets", "auth_pass", "custom_headers", "smtp_pass", "google_client_secret",
                                "google_refresh_token", "ahrefs_token", "moz_secret", "majestic_key", "psi_api_key"})


@dataclass
class CrawlConfig:
    # --- Mode -------------------------------------------------------------
    start_url: str = ""
    mode: str = "spider"                 # spider | list | sitemap
    list_urls: list[str] = field(default_factory=list)

    # --- Spider > Crawl ---------------------------------------------------
    crawl_images: bool = True
    crawl_css: bool = True
    crawl_js: bool = True
    crawl_media: bool = True
    check_external: bool = True          # fetch external links for status codes
    crawl_canonicals: bool = True
    crawl_pagination: bool = True
    crawl_hreflang: bool = True
    crawl_amp: bool = True
    crawl_meta_refresh: bool = True
    crawl_iframes: bool = True
    crawl_subdomains: bool = False
    crawl_outside_start_folder: bool = True
    follow_internal_nofollow: bool = True
    follow_external_nofollow: bool = True
    crawl_sitemaps: bool = True          # auto-discover via robots.txt
    sitemap_urls: list[str] = field(default_factory=list)
    skip_share_links: bool = True        # record but do not fetch facebook/twitter/linkedin share-intent URLs
    extract_structured_data: bool = True # JSON-LD, Microdata, RDFa -> Structured Data tab
    validate_schema_org: bool = True     # unknown types / properties against the bundled schema.org vocabulary
    validate_google: bool = True         # Google rich-result required / recommended properties

    # --- Spider > Limits --------------------------------------------------
    max_urls: int = 0                    # 0 = unlimited
    max_depth: int = 0                   # 0 = unlimited
    max_redirects: int = 10
    max_url_length: int = 2000
    max_page_size_kb: int = 10240
    max_query_strings: int = 0           # 0 = unlimited

    # --- Spider > Rendering -----------------------------------------------
    rendering: str = "text"              # text | javascript
    render_ajax_timeout: float = 5.0     # wait for network idle after load (seconds)
    render_wait_extra: float = 0.0       # extra fixed wait after network idle
    render_width: int = 1366
    render_height: int = 768
    render_mobile: bool = False
    render_screenshots: bool = True
    render_full_page_screenshot: bool = False
    render_block_images: bool = False    # True = don't download images/fonts/media inside the browser (faster, blank images in screenshots)
    render_concurrency: int = 4

    # --- Spider > Advanced ------------------------------------------------
    respect_noindex: bool = False        # drop noindex pages from the crawl
    respect_canonical: bool = False
    respect_robots: bool = True          # False = ignore robots.txt
    robots_report_only: bool = False     # crawl blocked URLs but report them
    store_html: bool = True
    extract_srcset: bool = True
    ignore_non_indexable_for_issues: bool = True
    ignore_paginated_for_duplicates: bool = True
    timeout: float = 20.0
    retries_5xx: int = 0
    verify_ssl: bool = True

    # --- Speed / request --------------------------------------------------
    max_threads: int = 5
    max_urls_per_second: float = 0       # 0 = unlimited
    user_agent: str = DEFAULT_UA
    custom_headers: dict[str, str] = field(default_factory=dict)
    auth_user: str = ""
    auth_pass: str = ""

    # --- Include / Exclude / rewriting ------------------------------------
    include_regex: list[str] = field(default_factory=list)
    exclude_regex: list[str] = field(default_factory=list)
    remove_params: list[str] = field(default_factory=list)
    cdn_domains: list[str] = field(default_factory=list)

    # --- Preferences (thresholds) ------------------------------------------
    title_max_chars: int = 60
    title_min_chars: int = 30
    title_max_px: int = 561
    title_min_px: int = 200
    desc_max_chars: int = 155
    desc_min_chars: int = 70
    desc_max_px: int = 985
    desc_min_px: int = 400
    url_max_chars: int = 115
    h1_max_chars: int = 70
    h2_max_chars: int = 70
    alt_max_chars: int = 100
    image_max_kb: int = 100
    low_content_words: int = 200
    high_crawl_depth: int = 4
    high_internal_outlinks: int = 1000
    high_external_outlinks: int = 100
    non_descriptive_anchors: list[str] = field(default_factory=lambda: list(NON_DESCRIPTIVE_ANCHORS))
    near_duplicate_threshold: int = 90
    spell_check: bool = False            # dictionary spelling check of page text (end of crawl / Analyse)
    grammar_check: bool = False          # rule-based grammar, or LanguageTool when grammar_lt_url is set
    spell_language: str = "en"           # fallback when the page has no usable <html lang>
    spell_ignore: list[str] = field(default_factory=list)   # words never flagged (brand names, product terms)
    spell_learn_min_pages: int = 5       # an unknown word on at least this many pages ...
    spell_learn_share: int = 5           # ... and this % of pages is treated as site vocabulary, not a typo
    grammar_lt_url: str = ""             # LanguageTool server, e.g. http://localhost:8010 or https://api.languagetool.org

    # --- AI (fix suggestions) ---------------------------------------------
    ai_provider: str = "anthropic"       # anthropic | openai | gemini | ollama | custom (OpenAI-compatible)
    ai_api_key: str = ""
    ai_model: str = ""                   # empty = provider default
    ai_base_url: str = ""                # custom / ollama endpoint, e.g. http://localhost:11434/v1
    ai_concurrency: int = 3
    ai_batch_size: int = 8               # pages per request
    ai_brand: str = ""                   # brand name / title suffix, e.g. "Acme"
    ai_instructions: str = ""            # extra house-style guidance for every prompt
    ai_system_prompts: dict[str, str] = field(default_factory=dict)  # per provider id: base system prompt (persona, constraints, formatting)

    # --- Email (crawl-complete notification) --------------------------------
    email_enabled: bool = False          # send a summary + full issue table when a crawl finishes
    email_to: str = ""                   # comma-separated recipients
    email_from: str = ""                 # defaults to the SMTP user
    email_include_compare: bool = True   # add the change-detection summary vs the previous crawl of the site
    smtp_host: str = ""
    smtp_port: int = 587
    smtp_security: str = "starttls"      # starttls | ssl | none
    smtp_user: str = ""
    smtp_pass: str = ""

    # --- PageSpeed / Lighthouse -------------------------------------------
    lighthouse_enabled: bool = False     # after each crawl, audit the top pages automatically
    lighthouse_max_pages: int = 20       # how many pages the automatic run audits (start page + most-linked indexable pages)
    lighthouse_device: str = "mobile"    # mobile | desktop
    lighthouse_concurrency: int = 2      # parallel headless Chrome instances for local runs
    psi_api_key: str = ""                # PageSpeed Insights API key: use Google's servers + CrUX field data instead of local Lighthouse

    # --- Google Search Console + GA4 ----------------------------------------
    google_client_id: str = ""           # OAuth client (Desktop app) from the person's Google Cloud project
    google_client_secret: str = ""
    google_refresh_token: str = ""       # stored after Connect; secret
    google_account: str = ""             # email shown after Connect
    gsc_property: str = ""               # e.g. sc-domain:example.co.uk or https://example.co.uk/ ; empty = auto-match the crawled host
    ga4_property: str = ""               # e.g. properties/123456789
    gsc_days: int = 28                   # date range (ending 3 days ago, when GSC data is final)
    gsc_auto: bool = False               # fetch Search Console + GA4 after each crawl
    gsc_inspect_max: int = 0             # URL Inspection API calls per run (0 = off; quota 2,000 / property / day)
    google_base_override: str = ""       # tests: point every Google host at a mock server

    # --- Link metrics (Ahrefs / Moz / Majestic) ------------------------------
    ahrefs_token: str = ""               # Ahrefs API v3 token (Enterprise / API add-on)
    ahrefs_country: str = "gb"           # country for organic keywords / traffic
    moz_access_id: str = ""              # Moz Links API v2 access id + secret
    moz_secret: str = ""
    majestic_key: str = ""               # Majestic API key (OpenApps)
    majestic_datasource: str = "fresh"   # fresh | historic
    linkmetrics_max_urls: int = 100      # URLs per run (every URL costs credits)
    linkmetrics_auto: bool = False       # fetch after each crawl for the top pages
    linkmetrics_base_override: str = ""  # tests: mock server for all three APIs

    # --- MCP / app server -------------------------------------------------
    mcp_enabled: bool = True             # serve the MCP tool set at http://127.0.0.1:<app_port>/mcp while the app runs
    app_port: int = 7743                 # fixed port so MCP clients can find the app; falls back to a free port if busy

    # --- Site connectors (push targets) -----------------------------------
    # each: {name, type: wordpress|wordpress_xmlrpc|webhook, env: staging|live, base_url, username, secret, extra}
    targets: list[dict[str, Any]] = field(default_factory=list)

    # --- Custom search / extraction (phase 1: search only) -----------------
    custom_search: list[dict[str, Any]] = field(default_factory=list)    # {name, contains: bool, text, regex: bool}
    custom_extraction: list[dict[str, Any]] = field(default_factory=list)  # {name, type: xpath|css|regex, expr}

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)

    @classmethod
    def from_dict(cls, d: dict[str, Any]) -> "CrawlConfig":
        known = {k: v for k, v in d.items() if k in cls.__dataclass_fields__}
        return cls(**known)
