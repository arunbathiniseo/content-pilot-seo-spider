"""Tab / filter registry. One entry per Screaming Frog filter, expressed as a SQL predicate over `urls`.

kind: 'issue' | 'warning' | 'opportunity' | '' (plain filter, not an issue)
Scope placeholders are expanded with the crawl config (thresholds, ignore-non-indexable).
"""
from __future__ import annotations

import sqlite3
from typing import Any

from .config import CrawlConfig

TABS: list[tuple[str, str]] = [
    ("url", "All URLs"), ("internal", "Internal"), ("external", "External"), ("security", "Security"), ("response_codes", "Response Codes"),
    ("page_titles", "Page Titles"), ("meta_description", "Meta Description"), ("meta_keywords", "Meta Keywords"),
    ("h1", "H1"), ("h2", "H2"), ("content", "Content"), ("images", "Images"), ("canonicals", "Canonicals"),
    ("pagination", "Pagination"), ("directives", "Directives"), ("hreflang", "Hreflang"), ("structured_data", "Structured Data"), ("amp", "AMP"), ("pagespeed", "PageSpeed"), ("accessibility", "Accessibility"), ("search_console", "Search Console"), ("analytics", "Analytics"), ("link_metrics", "Link Metrics"), ("javascript", "JavaScript"), ("links", "Links"),
    ("validation", "Validation"), ("sitemaps", "Sitemaps"), ("custom_search", "Custom Search"), ("custom_extraction", "Custom Extraction"),
]

VISIBLE = "crawled IN (1,3)"
INT = f"{VISIBLE} AND is_internal=1"
EXT = f"{VISIBLE} AND is_internal=0"
HTML = "crawled=1 AND is_internal=1 AND type='HTML' AND status_code BETWEEN 200 AND 299"
HTML_IDX = HTML + "{IDX}"          # {IDX} -> " AND indexable=1" when ignore_non_indexable_for_issues
DIRECTIVES = "(coalesce(meta_robots,'') || ',' || coalesce(x_robots,''))"


def _tok(token: str) -> str:
    return f"{DIRECTIVES} REGEXP '(^|[,\\s;:])\\s*{token}\\s*($|[,\\s;:])'"


F: list[dict[str, Any]] = []


def add(tab: str, key: str, name: str, where: str, kind: str = "", priority: str = "", desc: str = "", fix: str = "", issue_only: bool = False):
    F.append({"tab": tab, "key": f"{tab}:{key}", "short": key, "name": name, "where": where, "kind": kind,
              "priority": priority, "desc": desc, "fix": fix, "issue_only": issue_only})


# --- Internal / External ----------------------------------------------------
for tab, scope in (("internal", INT), ("external", EXT)):
    add(tab, "all", "All", scope)
    for t in ("HTML", "JavaScript", "CSS", "Images", "Media", "Fonts", "XML", "PDF", "Flash", "Other", "Unknown"):
        add(tab, t.lower(), t, f"{scope} AND type='{t}'")

# --- Security ---------------------------------------------------------------
SEC = f"crawled=1 AND (is_internal=1 OR type<>'HTML')"
add("security", "http_urls", "HTTP URLs", f"{SEC} AND url LIKE 'http://%'", "issue", "High",
    "URLs served over insecure HTTP.", "Migrate to HTTPS and 301 redirect HTTP to HTTPS.")
add("security", "https_urls", "HTTPS URLs", f"{SEC} AND url LIKE 'https://%'")
add("security", "mixed_content", "Mixed Content", f"{SEC} AND mixed_content=1", "issue", "High",
    "HTTPS pages loading resources over HTTP.", "Update resource references to HTTPS.")
add("security", "form_insecure", "Form URL Insecure", f"{SEC} AND form_insecure=1", "issue", "High",
    "HTTPS pages with a form posting to an HTTP URL.", "Point form actions to HTTPS URLs.")
add("security", "form_on_http", "Form on HTTP URL", f"{SEC} AND form_on_http=1 AND type='HTML'", "issue", "High",
    "Forms on HTTP pages.", "Serve pages with forms over HTTPS.")
add("security", "unsafe_cross_origin", "Unsafe Cross-Origin Links", f"{SEC} AND unsafe_cross_origin=1", "warning", "Low",
    "Links with target=_blank and no rel=noopener/noreferrer.", "Add rel=\"noopener\" to target=\"_blank\" links.")
add("security", "protocol_relative", "Protocol-Relative Resource Links", f"{SEC} AND protocol_relative=1", "warning", "Low",
    "Resources referenced with // instead of https://.", "Use absolute https:// URLs for resources.")
add("security", "missing_hsts", "Missing HSTS Header", f"{SEC} AND url LIKE 'https://%' AND headers NOT LIKE '%\"strict-transport-security\"%'", "warning", "Low",
    "HTTPS URLs without a Strict-Transport-Security header.", "Set Strict-Transport-Security on all HTTPS responses.")
add("security", "missing_csp", "Missing Content-Security-Policy Header", f"{SEC} AND headers NOT LIKE '%\"content-security-policy\"%'", "warning", "Low",
    "URLs without a Content-Security-Policy header.", "Set a strict Content-Security-Policy header.")
add("security", "missing_xcto", "Missing X-Content-Type-Options Header", f"{SEC} AND headers NOT LIKE '%\"x-content-type-options\"%'", "warning", "Low",
    "URLs without X-Content-Type-Options: nosniff.", "Set X-Content-Type-Options: nosniff.")
add("security", "missing_xfo", "Missing X-Frame-Options Header", f"{SEC} AND type='HTML' AND headers NOT LIKE '%\"x-frame-options\"%' AND headers NOT LIKE '%frame-ancestors%'", "warning", "Low",
    "HTML URLs without X-Frame-Options (or a CSP frame-ancestors directive).", "Set X-Frame-Options: SAMEORIGIN or CSP frame-ancestors.")
add("security", "missing_referrer_policy", "Missing Secure Referrer-Policy Header",
    f"{SEC} AND NOT (headers LIKE '%\"referrer-policy\": \"no-referrer%' OR headers LIKE '%\"referrer-policy\": \"strict-origin%')", "warning", "Low",
    "URLs missing a secure Referrer-Policy (no-referrer, no-referrer-when-downgrade, strict-origin, strict-origin-when-cross-origin).",
    "Set Referrer-Policy: strict-origin-when-cross-origin.")
add("security", "bad_content_type", "Bad Content Type", f"{SEC} AND bad_content_type=1", "warning", "Low",
    "The Content-Type header does not match the URL's file type.", "Serve the correct Content-Type.")

# --- Structured Data --------------------------------------------------------
add("structured_data", "contains", "Contains Structured Data", f"{HTML} AND sd_count>0")
add("structured_data", "missing", "Missing", f"{HTML_IDX} AND coalesce(sd_count,0)=0 AND coalesce(sd_parse_errors,0)=0", "opportunity", "Low",
    "Indexable pages with no JSON-LD, Microdata or RDFa.", "Add schema.org markup (Organization, Article, Product, BreadcrumbList…) so search engines understand the page and can show rich results.")
add("structured_data", "validation_errors", "Validation Errors", f"{HTML} AND sd_errors>0", "issue", "Medium",
    "Structured data with errors: unknown schema.org types or properties, or Google rich-result required properties missing.", "Open the Structured Data pane for the exact messages and fix the markup.")
add("structured_data", "validation_warnings", "Validation Warnings", f"{HTML} AND sd_warnings>0", "warning", "Low",
    "Structured data missing Google's recommended properties, or using properties not expected on that type.", "Add the recommended properties to qualify for richer results.")
add("structured_data", "parse_errors", "Parse Errors", f"{HTML} AND sd_parse_errors>0", "issue", "Medium",
    "JSON-LD that is not valid JSON (search engines ignore it entirely).", "Validate the JSON (trailing commas, unescaped quotes, truncated output).")
add("structured_data", "jsonld", "JSON-LD URLs", f"{HTML} AND sd_jsonld>0")
add("structured_data", "microdata", "Microdata URLs", f"{HTML} AND sd_microdata>0")
add("structured_data", "rdfa", "RDFa URLs", f"{HTML} AND sd_rdfa>0")

# --- AMP --------------------------------------------------------------------
AMP_SCOPE = f"{VISIBLE} AND is_internal=1 AND (is_amp=1 OR (amp_url IS NOT NULL AND amp_url<>''))"
add("amp", "non_200", "Non-200 AMP Response", f"{INT} AND amp_non200=1", "issue", "High",
    "The URL in the page's amphtml link does not return 200.", "Fix or remove the amphtml link so Google is not sent to a broken AMP page.")
add("amp", "missing_return", "Missing Non-AMP Return Link", f"{INT} AND amp_missing_return=1", "issue", "Medium",
    "AMP page whose canonical does not point to a non-AMP page (missing, self-referencing or another AMP URL).", "Add <link rel=\"canonical\"> to the non-AMP version on every AMP page.")
add("amp", "missing_canonical", "Missing Canonical to Non-AMP", f"{INT} AND is_amp=1 AND amp_missing_canonical=1", "issue", "Medium",
    "AMP page with no canonical link at all.", "Add <link rel=\"canonical\"> pointing at the non-AMP page.")
add("amp", "nonindexable_canonical", "Non-Indexable Canonical", f"{INT} AND amp_canonical_nonindexable=1", "issue", "Medium",
    "The AMP page's canonical target is non-indexable or not 200.", "Point the canonical at an indexable 200 page.")
add("amp", "indexable_amp", "Indexable AMP URL", f"{INT} AND amp_indexable=1", "warning", "Low",
    "AMP page that is itself indexable (no canonical to a non-AMP page).", "Canonicalise AMP pages to the non-AMP version unless the site is AMP-only.")
add("amp", "missing_html_tag", "Missing <html amp> Tag", f"{INT} AND amp_missing_html_tag=1", "issue", "High",
    "Linked as amphtml but the page's <html> element has no amp (or ⚡) attribute, so it is not valid AMP.", "Add the amp attribute to <html> or remove the amphtml link.")
add("amp", "missing_script", "Missing AMP Script", f"{INT} AND amp_missing_script=1", "issue", "High",
    "AMP page without the AMP runtime script.", "Add <script async src=\"https://cdn.ampproject.org/v0.js\"></script> in <head>.")
add("amp", "missing_boilerplate", "Missing Boilerplate", f"{INT} AND amp_missing_boilerplate=1", "issue", "High",
    "AMP page without the required amp-boilerplate style.", "Add the <style amp-boilerplate> block and its <noscript> fallback.")
add("amp", "disallowed_html", "Contains Disallowed HTML", f"{INT} AND amp_disallowed_html>0", "issue", "High",
    "img/video/audio/iframe/embed/object or custom JavaScript on an AMP page.", "Use amp-img, amp-video, amp-audio, amp-iframe… and remove custom scripts.")
add("amp", "other_errors", "Other Validation Errors", f"{INT} AND amp_other_errors>0", "issue", "Medium",
    "Doctype, head/body, charset, viewport or CSS rule violations (see the Issues pane for the list).", "Fix each message listed in the amp_issues field.")


# --- PageSpeed (Lighthouse) ----------------------------------------------------
LH = f"{HTML} AND lh_at IS NOT NULL"
add("pagespeed", "audited", "Audited (has Lighthouse data)", LH)
add("pagespeed", "perf_poor", "Performance Score Poor (0-49)", f"{LH} AND lh_perf < 50", "issue", "High", "Lighthouse performance score below 50.", "Work through the opportunities in the PageSpeed pane, biggest savings first.")
add("pagespeed", "perf_needs_work", "Performance Score Needs Improvement (50-89)", f"{LH} AND lh_perf BETWEEN 50 AND 89", "warning", "Medium", "Lighthouse performance score 50-89.", "Fix the opportunities with the largest estimated savings.")
add("pagespeed", "lcp_slow", "LCP Over 2.5s", f"{LH} AND lh_lcp > 2500", "issue", "High", "Largest Contentful Paint above Google's 2.5 s 'good' threshold (lab).", "Optimise the LCP element: preload it, serve a smaller image, cut render-blocking resources, speed up the server response.")
add("pagespeed", "cls_poor", "CLS Over 0.1", f"{LH} AND lh_cls > 0.1", "issue", "Medium", "Cumulative Layout Shift above 0.1.", "Give images/embeds explicit dimensions, reserve space for ads and dynamic content, avoid inserting content above existing content.")
add("pagespeed", "tbt_high", "Total Blocking Time Over 200ms", f"{LH} AND lh_tbt > 200", "warning", "Medium", "Main thread blocked for more than 200 ms during load (lab proxy for INP).", "Split long tasks, defer non-critical JavaScript, remove unused scripts.")
add("pagespeed", "field_lcp_poor", "Field LCP Over 2.5s (CrUX)", f"{LH} AND lh_field_lcp > 2500", "issue", "High", "Real-user LCP (Chrome UX Report, 75th percentile) above 2.5 s. PageSpeed Insights API only.", "Same as lab LCP, but this is what Google measures for Core Web Vitals.")
add("pagespeed", "field_inp_poor", "Field INP Over 200ms (CrUX)", f"{LH} AND lh_field_inp > 200", "issue", "High", "Real-user Interaction to Next Paint above 200 ms. PageSpeed Insights API only.", "Reduce JavaScript work on interactions; break up long tasks.")
for _aid, _name in (
    ("render-blocking-resources", "Eliminate Render-Blocking Resources"), ("uses-responsive-images", "Properly Size Images"), ("offscreen-images", "Defer Offscreen Images"),
    ("unminified-css", "Minify CSS"), ("unminified-javascript", "Minify JavaScript"), ("unused-css-rules", "Reduce Unused CSS"), ("unused-javascript", "Reduce Unused JavaScript"),
    ("uses-optimized-images", "Efficiently Encode Images"), ("modern-image-formats", "Serve Images in Next-Gen Formats"), ("uses-text-compression", "Enable Text Compression"),
    ("uses-rel-preconnect", "Preconnect to Required Origins"), ("server-response-time", "Reduce Initial Server Response Time"), ("redirects", "Avoid Multiple Page Redirects"),
    ("uses-rel-preload", "Preload Key Requests"), ("efficient-animated-content", "Use Video Formats for Animated Content"), ("dom-size", "Avoid Excessive DOM Size"),
    ("bootup-time", "Reduce JavaScript Execution Time"), ("mainthread-work-breakdown", "Minimize Main-Thread Work"), ("uses-long-cache-ttl", "Serve Static Assets with an Efficient Cache Policy"),
    ("total-byte-weight", "Avoid Enormous Network Payloads"), ("layout-shifts", "Avoid Large Layout Shifts"), ("unsized-images", "Image Elements Do Not Have Explicit Width & Height"),
    ("long-tasks", "Avoid Long Main-Thread Tasks"), ("third-party-summary", "Reduce the Impact of Third-Party Code"), ("legacy-javascript", "Avoid Serving Legacy JavaScript"),
    ("font-display", "Ensure Text Remains Visible During Webfont Load"), ("lcp-lazy-loaded", "LCP Image Was Lazily Loaded"),
):
    add("pagespeed", _aid, _name, f"{LH} AND lh_failed LIKE '%,{_aid},%'", "opportunity", "Low", f"Lighthouse audit '{_name}' did not pass.", "See the PageSpeed pane for the affected resources and estimated savings.")

# --- Accessibility (Lighthouse / axe-core) -------------------------------------
add("accessibility", "audited", "Audited (has Lighthouse data)", LH)
add("accessibility", "score_poor", "Accessibility Score Poor (0-49)", f"{LH} AND lh_a11y < 50", "issue", "High", "Lighthouse accessibility score below 50.", "Fix the failing axe checks in the PageSpeed pane.")
add("accessibility", "score_needs_work", "Accessibility Score Needs Improvement (50-89)", f"{LH} AND lh_a11y BETWEEN 50 AND 89", "warning", "Medium", "Lighthouse accessibility score 50-89.", "Fix the failing axe checks in the PageSpeed pane.")
for _aid, _name in (
    ("color-contrast", "Insufficient Colour Contrast"), ("image-alt", "Images Missing Alt Text"), ("link-name", "Links Without Discernible Name"), ("button-name", "Buttons Without Accessible Name"),
    ("html-has-lang", "<html> Missing lang Attribute"), ("html-lang-valid", "<html> lang Not Valid"), ("document-title", "Missing <title>"), ("meta-viewport", "Viewport Disables Zooming"),
    ("heading-order", "Heading Levels Skipped"), ("label", "Form Elements Without Labels"), ("input-image-alt", "Image Buttons Missing Alt"), ("frame-title", "Frames Without Title"),
    ("list", "Lists Contain Non-List Items"), ("listitem", "List Items Outside Lists"), ("tabindex", "tabindex Greater Than 0"), ("duplicate-id-aria", "Duplicate ARIA ids"),
    ("aria-allowed-attr", "ARIA Attributes Not Allowed"), ("aria-required-attr", "ARIA Required Attributes Missing"), ("aria-valid-attr-value", "Invalid ARIA Attribute Values"),
    ("aria-roles", "Invalid ARIA Roles"), ("aria-hidden-focus", "aria-hidden Elements Contain Focusable Content"), ("link-in-text-block", "Links Not Distinguishable Without Colour"),
    ("target-size", "Touch Targets Too Small"), ("label-content-name-mismatch", "Label and Visible Text Mismatch"), ("select-name", "Select Elements Without Name"),
    ("video-caption", "Video Without Captions"), ("skip-link", "Skip Link Not Focusable"), ("bypass", "No Way to Bypass Repeated Content"),
):
    add("accessibility", _aid, _name, f"{LH} AND lh_failed LIKE '%,{_aid},%'", "issue", "Medium", f"axe-core check '{_name}' failed (WCAG).", "Open the PageSpeed pane for the elements involved.")


# --- Search Console / Analytics ----------------------------------------------
GSC_SCOPE = f"is_internal=1 AND (crawled IN (1,3) OR crawled=5)"
add("search_console", "clicks", "Clicks Above 0", f"{GSC_SCOPE} AND gsc_clicks > 0")
add("search_console", "impressions", "Impressions Above 0", f"{GSC_SCOPE} AND gsc_impressions > 0")
add("search_console", "no_data", "No Search Console Data", f"{HTML_IDX} AND gsc_clicks IS NULL AND EXISTS (SELECT 1 FROM meta WHERE key='gsc')", "opportunity", "Low",
    "Indexable pages with no impressions in the date range.", "Check they are indexed (URL Inspection) and targeted at queries people search for; strengthen internal links.")
add("search_console", "nonindexable_traffic", "Non-Indexable with Search Console Data", f"crawled=1 AND is_internal=1 AND type='HTML' AND indexable=0 AND gsc_impressions > 0", "issue", "Medium",
    "Pages that get impressions or clicks but are noindex / canonicalised / redirected: that traffic will disappear.", "Make the page indexable, or redirect it to the page that should rank.")
add("search_console", "orphans", "Orphan URLs", f"crawled=5 AND gsc_orphan=1", "warning", "Medium",
    "URLs with Search Console data that the crawl never reached: not linked internally.", "Link them from relevant pages, or redirect / remove them if they should not exist.")
add("search_console", "position_low", "Average Position Above 10", f"{GSC_SCOPE} AND gsc_impressions > 0 AND gsc_position > 10", "opportunity", "Low", "Ranking on page 2+ for its queries.", "Improve content and internal links for the main query; see which queries in Search Console.")
add("search_console", "ctr_low", "Low CTR (Under 1%, 100+ Impressions)", f"{GSC_SCOPE} AND gsc_impressions >= 100 AND gsc_ctr < 1", "opportunity", "Low", "Seen often but rarely clicked.", "Rewrite the title and meta description to match the intent of the queries.")
add("search_console", "inspected", "URL Inspected", f"crawled=1 AND is_internal=1 AND inspect_at IS NOT NULL")
add("search_console", "not_on_google", "URL Is Not on Google", f"crawled=1 AND is_internal=1 AND inspect_verdict IN ('FAIL','NEUTRAL') AND inspect_coverage NOT LIKE '%Submitted and indexed%'", "issue", "High",
    "Google's URL Inspection says this page is not indexed.", "Read the coverage state: crawled-not-indexed usually means thin or duplicate content; discovered-not-indexed means weak linking.")
add("search_console", "indexable_not_indexed", "Indexable URL Not Indexed", f"crawled=1 AND is_internal=1 AND indexable=1 AND inspect_verdict IN ('FAIL','NEUTRAL')", "issue", "High",
    "The page is indexable but Google has not indexed it.", "Improve content quality and internal links; request indexing after fixing.")
add("search_console", "canonical_mismatch", "User-Declared Canonical Not Selected", f"crawled=1 AND is_internal=1 AND inspect_google_canonical<>'' AND inspect_user_canonical<>'' AND inspect_google_canonical<>inspect_user_canonical", "warning", "Medium",
    "Google chose a different canonical than the page declares.", "Make the declared canonical the stronger page (links, content), or accept Google's choice and redirect.")

GA_SCOPE = f"is_internal=1 AND (crawled IN (1,3) OR crawled=5)"
add("analytics", "sessions", "Sessions Above 0", f"{GA_SCOPE} AND ga_sessions > 0")
add("analytics", "bounce_high", "Bounce Rate Above 70%", f"{GA_SCOPE} AND ga_sessions >= 10 AND ga_bounce_rate > 70", "opportunity", "Low", "Most visits leave without engaging.", "Check intent match, speed and the first screen; add clear next steps.")
add("analytics", "no_data", "No GA4 Data", f"{HTML_IDX} AND ga_sessions IS NULL AND EXISTS (SELECT 1 FROM meta WHERE key='ga4')", "opportunity", "Low", "Indexable pages with no sessions in the date range.", "Either nobody visits them or the GA4 tag is missing: check both.")
add("analytics", "nonindexable_traffic", "Non-Indexable with GA4 Data", f"crawled=1 AND is_internal=1 AND type='HTML' AND indexable=0 AND ga_sessions > 0", "warning", "Medium", "Pages with visits that are not indexable.", "Fine for logged-in or campaign pages; otherwise make them indexable.")
add("analytics", "orphans", "Orphan URLs", f"crawled=5 AND ga_orphan=1", "warning", "Medium", "Paths with GA4 sessions the crawl never reached.", "Link them internally, or redirect / remove.")
add("analytics", "key_events", "Key Events Above 0", f"{GA_SCOPE} AND ga_key_events > 0")


# --- Link Metrics (Ahrefs / Moz / Majestic) -----------------------------------
LMS = f"{HTML} AND lm_at IS NOT NULL"
add("link_metrics", "has", "Has Link Metrics", LMS)
add("link_metrics", "no_backlinks", "No Backlinks", f"{LMS} AND coalesce(lm_ahrefs_backlinks, lm_moz_backlinks, lm_majestic_backlinks, 0) = 0", "opportunity", "Low",
    "No external backlinks reported by any connected provider.", "Earn or build links to pages that matter; consolidate pages nobody links to.")
add("link_metrics", "low_authority", "Low Page Authority", f"{LMS} AND ((lm_ahrefs_ur IS NOT NULL AND lm_ahrefs_ur < 10) OR (lm_moz_pa IS NOT NULL AND lm_moz_pa < 20) OR (lm_majestic_tf IS NOT NULL AND lm_majestic_tf < 10))", "opportunity", "Low",
    "Ahrefs UR under 10, Moz PA under 20 or Majestic TF under 10.", "Point internal links from strong pages at these, and earn external links.")
add("link_metrics", "high_authority", "High Page Authority", f"{LMS} AND ((lm_ahrefs_ur >= 30) OR (lm_moz_pa >= 40) OR (lm_majestic_tf >= 30))")
add("link_metrics", "spam", "Moz Spam Score 30%+", f"{LMS} AND lm_moz_spam >= 30", "warning", "Medium", "Moz flags the page's link profile as spammy.", "Review the backlinks; disavow if they are manipulative.")
add("link_metrics", "backlinks_low_inlinks", "Backlinks but Few Internal Links", f"{LMS} AND coalesce(lm_ahrefs_backlinks, lm_moz_backlinks, lm_majestic_backlinks, 0) > 0 AND inlinks <= 2", "opportunity", "Medium",
    "Externally linked pages that the site itself barely links to; their equity is not flowing onward.", "Link to them from the navigation or related content, and link out from them.")
add("link_metrics", "backlinks_nonindexable", "Backlinks to Non-Indexable Page", f"crawled=1 AND is_internal=1 AND type='HTML' AND lm_at IS NOT NULL AND indexable=0 AND coalesce(lm_ahrefs_backlinks, lm_moz_backlinks, lm_majestic_backlinks, 0) > 0", "issue", "Medium",
    "Pages with backlinks that are noindex / canonicalised / redirected.", "Make them indexable or redirect them so the links count.")

# --- Response Codes ---------------------------------------------------------
add("response_codes", "blocked_robots", "Blocked by Robots.txt", f"{VISIBLE} AND (crawled=3 OR blocked_robots=1)")
add("response_codes", "blocked_resource", "Blocked Resource", f"{VISIBLE} AND (crawled=3 OR blocked_robots=1) AND type IN ('JavaScript','CSS','Images')")
add("response_codes", "no_response", "No Response", f"crawled=1 AND status_code IS NULL")
add("response_codes", "2xx", "Success (2xx)", f"crawled=1 AND status_code BETWEEN 200 AND 299")
add("response_codes", "3xx", "Redirection (3xx)", f"crawled=1 AND status_code BETWEEN 300 AND 399")
add("response_codes", "js_redirect", "Redirection (JavaScript)", f"crawled=1 AND redirect_type='JavaScript Redirect'")
add("response_codes", "meta_refresh", "Redirection (Meta Refresh)", f"crawled=1 AND redirect_type='Meta Refresh Redirect'")
add("response_codes", "http_refresh", "Redirection (HTTP Refresh)", f"crawled=1 AND redirect_type='HTTP Refresh Redirect'")
add("response_codes", "redirect_chain", "Redirect Chain", f"crawled=1 AND redirect_chain_len>=2")
add("response_codes", "redirect_loop", "Redirect Loop", f"crawled=1 AND redirect_loop=1")
add("response_codes", "4xx", "Client Error (4xx)", f"crawled=1 AND status_code BETWEEN 400 AND 499")
add("response_codes", "5xx", "Server Error (5xx)", f"crawled=1 AND status_code>=500")
# issue-only internal / external splits (shown in the Issues sidebar, as in Screaming Frog)
add("response_codes", "int_no_response", "Internal No Response", f"crawled=1 AND is_internal=1 AND status_code IS NULL", "issue", "High", "Internal URLs that returned no response (timeout, DNS, connection refused).", "Check server availability and timeouts.", True)
add("response_codes", "int_4xx", "Internal Client Error (4xx)", f"crawled=1 AND is_internal=1 AND status_code BETWEEN 400 AND 499", "issue", "High", "Internal URLs returning 4xx (broken links).", "Fix or redirect broken URLs and update the links pointing to them.", True)
add("response_codes", "int_5xx", "Internal Server Error (5xx)", f"crawled=1 AND is_internal=1 AND status_code>=500", "issue", "High", "Internal URLs returning 5xx.", "Investigate server errors.", True)
add("response_codes", "int_redirect_loop", "Internal Redirect Loop", f"crawled=1 AND is_internal=1 AND redirect_loop=1", "issue", "High", "Redirects that loop back on themselves.", "Fix the redirect rules.", True)
add("response_codes", "int_blocked_robots", "Internal Blocked by Robots.txt", f"{INT} AND (crawled=3 OR blocked_robots=1)", "warning", "High", "Internal URLs disallowed by robots.txt.", "Review robots.txt disallow rules.", True)
add("response_codes", "int_redirect_chain", "Internal Redirect Chain", f"crawled=1 AND is_internal=1 AND redirect_chain_len>=2", "warning", "Medium", "Internal URLs that redirect more than once.", "Redirect directly to the final URL.", True)
add("response_codes", "int_3xx", "Internal Redirection (3xx)", f"crawled=1 AND is_internal=1 AND status_code BETWEEN 300 AND 399", "warning", "Low", "Internal URLs that redirect.", "Link to the final URL directly.", True)
add("response_codes", "int_meta_refresh", "Internal Redirection (Meta Refresh)", f"crawled=1 AND is_internal=1 AND redirect_type='Meta Refresh Redirect'", "warning", "Low", "Internal pages redirecting via meta refresh.", "Use a server-side 301 instead.", True)
add("response_codes", "int_http_refresh", "Internal Redirection (HTTP Refresh)", f"crawled=1 AND is_internal=1 AND redirect_type='HTTP Refresh Redirect'", "warning", "Low", "Internal pages redirecting via a Refresh header.", "Use a server-side 301 instead.", True)
add("response_codes", "ext_no_response", "External No Response", f"crawled=1 AND is_internal=0 AND status_code IS NULL", "warning", "Low", "External URLs that returned no response.", "Check the linked site; remove or update dead links.", True)
add("response_codes", "ext_4xx", "External Client Error (4xx)", f"crawled=1 AND is_internal=0 AND status_code BETWEEN 400 AND 499", "warning", "Low", "External links returning 4xx.", "Update or remove the broken external links.", True)
add("response_codes", "ext_5xx", "External Server Error (5xx)", f"crawled=1 AND is_internal=0 AND status_code>=500", "warning", "Low", "External links returning 5xx.", "Re-check later; remove if persistent.", True)
add("response_codes", "ext_blocked_robots", "External Blocked by Robots.txt", f"{EXT} AND (crawled=3 OR blocked_robots=1)", "", "", "", "", True)

# --- URL --------------------------------------------------------------------
U = f"{INT} AND type='HTML'"
add("url", "non_ascii", "Non ASCII Characters", f"{U} AND url_non_ascii=1", "warning", "Low", "URLs with non-ASCII characters.", "Use ASCII characters in URLs.")
add("url", "underscores", "Underscores", f"{U} AND url_underscores=1", "opportunity", "Low", "URLs containing underscores.", "Prefer hyphens as word separators.")
add("url", "uppercase", "Uppercase", f"{U} AND url_uppercase=1", "warning", "Low", "URLs with uppercase characters.", "Use lowercase URLs and redirect variants.")
add("url", "multiple_slashes", "Multiple Slashes", f"{U} AND url_multiple_slashes=1", "issue", "Low", "URLs with // in the path.", "Fix the links and redirect the double-slash URLs.")
add("url", "repetitive_path", "Repetitive Path", f"{U} AND url_repetitive_path=1", "warning", "Low", "URLs with a repeated path segment.", "Check for relative-link loops.")
add("url", "space", "Contains A Space", f"{U} AND url_space=1", "issue", "Low", "URLs containing spaces.", "Replace spaces with hyphens.")
add("url", "internal_search", "Internal Search", f"{U} AND url_internal_search=1", "warning", "Low", "Internal search result URLs.", "Block internal search pages from crawling/indexing.")
add("url", "parameters", "Parameters", f"{U} AND url_parameters=1", "warning", "Low", "URLs with query parameters.", "Canonicalise or avoid linking to parameterised URLs.")
add("url", "broken_bookmark", "Broken Bookmark", f"{U} AND url_broken_bookmark=1", "issue", "Low", "Pages with #fragment links to IDs that do not exist.", "Fix the fragment or add the anchor ID.")
add("url", "ga_params", "GA Tracking Parameters", f"{U} AND url_ga_params=1", "warning", "Low", "Internal URLs with utm_/gclid tracking parameters.", "Do not use tracking parameters on internal links.")
add("url", "over_length", "Over {url_max_chars} Characters", f"{U} AND url_over_length=1", "opportunity", "Low", "Long URLs.", "Keep URLs concise.")

# --- Page Titles ------------------------------------------------------------
add("page_titles", "missing", "Missing", f"{HTML_IDX} AND (title IS NULL OR title='')", "issue", "High", "Pages without a <title>.", "Add a unique, descriptive title.")
add("page_titles", "duplicate", "Duplicate", f"{HTML_IDX} AND title_dup>0", "opportunity", "Medium", "Pages sharing the same title.", "Write a unique title per page.")
add("page_titles", "over_chars", "Over {title_max_chars} Characters", f"{HTML_IDX} AND title_len>{{title_max_chars}}", "opportunity", "Medium", "Titles likely to be truncated in SERPs.", "Shorten the title.")
add("page_titles", "below_chars", "Below {title_min_chars} Characters", f"{HTML_IDX} AND title_len>0 AND title_len<{{title_min_chars}}", "opportunity", "Medium", "Short titles.", "Use the available space for a descriptive title.")
add("page_titles", "over_px", "Over {title_max_px} Pixels", f"{HTML_IDX} AND title_px>{{title_max_px}}", "opportunity", "Medium", "Titles wider than Google's pixel limit.", "Shorten the title.")
add("page_titles", "below_px", "Below {title_min_px} Pixels", f"{HTML_IDX} AND title_px>0 AND title_px<{{title_min_px}}", "opportunity", "Medium", "Narrow titles.", "Use the available space.")
add("page_titles", "same_as_h1", "Same as H1", f"{HTML_IDX} AND title<>'' AND lower(title)=lower(h1)", "opportunity", "Low", "Title identical to the H1.", "Vary the title and H1 where useful.")
add("page_titles", "multiple", "Multiple", f"{HTML} AND title_count>1", "issue", "High", "Pages with more than one <title>.", "Keep a single title element.")
add("page_titles", "outside_head", "Outside <head>", f"{HTML} AND title_outside_head=1", "issue", "High", "<title> found outside <head>.", "Move the title into <head> and fix invalid head markup.")

# --- Meta Description -------------------------------------------------------
add("meta_description", "missing", "Missing", f"{HTML_IDX} AND (meta_desc IS NULL OR meta_desc='')", "opportunity", "Low", "Pages without a meta description.", "Write a unique meta description.")
add("meta_description", "duplicate", "Duplicate", f"{HTML_IDX} AND md_dup>0", "opportunity", "Low", "Pages sharing a meta description.", "Write unique descriptions.")
add("meta_description", "over_chars", "Over {desc_max_chars} Characters", f"{HTML_IDX} AND md_len>{{desc_max_chars}}", "opportunity", "Low", "Long meta descriptions.", "Shorten to avoid truncation.")
add("meta_description", "below_chars", "Below {desc_min_chars} Characters", f"{HTML_IDX} AND md_len>0 AND md_len<{{desc_min_chars}}", "opportunity", "Low", "Short meta descriptions.", "Use the available space.")
add("meta_description", "over_px", "Over {desc_max_px} Pixels", f"{HTML_IDX} AND md_px>{{desc_max_px}}", "opportunity", "Low", "Descriptions wider than Google's pixel limit.", "Shorten.")
add("meta_description", "below_px", "Below {desc_min_px} Pixels", f"{HTML_IDX} AND md_px>0 AND md_px<{{desc_min_px}}", "opportunity", "Low", "Narrow descriptions.", "Use the available space.")
add("meta_description", "multiple", "Multiple", f"{HTML} AND md_count>1", "issue", "Medium", "Pages with multiple meta descriptions.", "Keep one.")
add("meta_description", "outside_head", "Outside <head>", f"{HTML} AND md_outside_head=1", "issue", "Medium", "Meta description outside <head>.", "Move into <head>.")

# --- Meta Keywords ----------------------------------------------------------
add("meta_keywords", "missing", "Missing", f"{HTML_IDX} AND (meta_kw IS NULL OR meta_kw='')")
add("meta_keywords", "duplicate", "Duplicate", f"{HTML_IDX} AND kw_dup>0")
add("meta_keywords", "multiple", "Multiple", f"{HTML} AND kw_count>1")

# --- H1 / H2 ----------------------------------------------------------------
add("h1", "missing", "Missing", f"{HTML_IDX} AND h1_count=0", "issue", "Medium", "Pages without an H1.", "Add a single descriptive H1.")
add("h1", "duplicate", "Duplicate", f"{HTML_IDX} AND h1_dup>0", "opportunity", "Low", "Pages sharing an H1.", "Use unique H1s.")
add("h1", "over_chars", "Over {h1_max_chars} Characters", f"{HTML_IDX} AND h1_len>{{h1_max_chars}}", "opportunity", "Low", "Long H1s.", "Keep H1s concise.")
add("h1", "multiple", "Multiple", f"{HTML_IDX} AND h1_count>1", "warning", "Medium", "Pages with more than one H1.", "Use one H1 per page.")
add("h1", "alt_text", "Alt Text in h1", f"{HTML_IDX} AND h1_has_alt=1", "warning", "Low", "H1 text comes from an image alt attribute.", "Use real text in the H1.")
add("h1", "nonseq", "Non-Sequential", f"{HTML_IDX} AND h1_nonseq=1", "warning", "Low", "The H1 is not the first heading on the page.", "Put the H1 before other headings.")
add("h2", "missing", "Missing", f"{HTML_IDX} AND h2_count=0", "warning", "Low", "Pages without an H2.", "Structure content with H2s.")
add("h2", "duplicate", "Duplicate", f"{HTML_IDX} AND h2_dup>0", "opportunity", "Low", "Pages sharing the same first H2.", "Use descriptive, unique H2s.")
add("h2", "over_chars", "Over {h2_max_chars} Characters", f"{HTML_IDX} AND h2_len>{{h2_max_chars}}", "opportunity", "Low", "Long H2s.", "Keep H2s concise.")
add("h2", "multiple", "Multiple", f"{HTML_IDX} AND h2_count>1", "warning", "Low", "Pages with multiple H2s (review hierarchy).", "Check heading hierarchy.")
add("h2", "nonseq", "Non-Sequential", f"{HTML_IDX} AND h2_nonseq=1", "warning", "Low", "An H2 appears before the H1.", "Fix heading order.")

# --- Content ----------------------------------------------------------------
add("content", "exact_duplicates", "Exact Duplicates", f"{HTML_IDX} AND exact_dup>0", "issue", "High", "Pages with identical HTML (hash).", "Canonicalise or consolidate duplicates.")
add("content", "spelling_errors", "Spelling Errors", f"{HTML_IDX} AND spelling_errors>0", "warning", "Low",
    "Pages with words the dictionary does not know (site vocabulary learnt from repeated words is excluded; enable in Config → Spelling & grammar).",
    "Fix the typo, or add the word to the dictionary in Config → Spelling & grammar if it is a product or brand term.")
add("content", "grammar_errors", "Grammar Errors", f"{HTML_IDX} AND grammar_errors>0", "warning", "Low",
    "Pages with grammar findings (repeated words, a/an, sentence case, punctuation spacing, common confusions; or LanguageTool when configured).",
    "Review the findings in the Spelling & Grammar pane and correct the copy.")
add("content", "near_duplicates", "Near Duplicates", f"{HTML_IDX} AND near_dup_count>0", "warning", "Medium",
    "Pages whose text is at least {near_duplicate_threshold}% similar to another page (MinHash over 3-word shingles).", "Consolidate into one page with a canonical/redirect, or differentiate the content.")
add("content", "low_content", "Low Content Pages", f"{HTML_IDX} AND word_count<{{low_content_words}}", "opportunity", "Medium", "Pages with fewer than {low_content_words} words.", "Add useful content or noindex thin pages.")
add("content", "soft_404", "Soft 404 Pages", f"{HTML_IDX} AND soft_404=1", "warning", "High", "200 pages that look like 'not found' pages.", "Return a real 404/410 status.")
add("content", "spelling", "Spelling Errors", f"{HTML_IDX} AND 0", "issue", "Medium", "Pages with spelling errors (phase 2).", "")
add("content", "grammar", "Grammar Errors", f"{HTML_IDX} AND 0", "issue", "Medium", "Pages with grammar errors (phase 2).", "")
add("content", "readability_difficult", "Readability Difficult", f"{HTML_IDX} AND readability='Difficult'", "opportunity", "Low", "Flesch reading ease 30-50.", "Shorten sentences and simplify words.")
add("content", "readability_very_difficult", "Readability Very Difficult", f"{HTML_IDX} AND readability='Very Difficult'", "opportunity", "Low", "Flesch reading ease below 30.", "Simplify the copy.")
add("content", "lorem_ipsum", "Lorem Ipsum Placeholder", f"{HTML_IDX} AND lorem_ipsum=1", "warning", "High", "Placeholder text left on the page.", "Replace placeholder copy.")

# --- Images -----------------------------------------------------------------
IMG = f"{VISIBLE} AND type='Images'"
add("images", "over_size", "Over {image_max_kb} KB", f"{IMG} AND crawled=1 AND size>{{image_max_kb}}*1024", "opportunity", "Medium", "Large images.", "Compress and resize images.")
add("images", "missing_alt_text", "Missing Alt Text", f"{IMG} AND img_alt_missing=1", "issue", "Low", "Images with an empty alt attribute.", "Add descriptive alt text (or leave empty only for decorative images).")
add("images", "missing_alt_attr", "Missing Alt Attribute", f"{IMG} AND img_alt_attr_missing=1", "issue", "Low", "Images with no alt attribute at all.", "Add an alt attribute.")
add("images", "alt_over", "Alt Text Over {alt_max_chars} Characters", f"{IMG} AND img_alt_over=1", "opportunity", "Low", "Very long alt text.", "Keep alt text concise.")
add("images", "background", "Background Images", f"{IMG} AND img_background=1", "warning", "Low", "Images loaded via CSS background.", "Use <img> for content images.")
add("images", "missing_size", "Missing Size Attributes", f"{IMG} AND img_size_attr_missing=1", "opportunity", "Low", "Images without width/height attributes (CLS).", "Add width and height attributes.")
add("images", "incorrectly_sized", "Incorrectly Sized Images", f"{IMG} AND 0", "opportunity", "Low", "Rendered size differs from natural size (needs JS rendering, phase 2).", "")

# --- Canonicals -------------------------------------------------------------
add("canonicals", "contains", "Contains Canonical", f"{HTML} AND canonical<>''")
add("canonicals", "self_referencing", "Self Referencing", f"{HTML} AND canonical<>'' AND canonical=url")
add("canonicals", "canonicalised", "Canonicalised", f"{HTML} AND canonical<>'' AND canonical<>url", "warning", "High", "Pages canonicalised to another URL.", "Make sure this is intended; link to the canonical URL.")
add("canonicals", "missing", "Missing", f"{HTML} AND (canonical IS NULL OR canonical='') AND indexable=1", "warning", "Medium", "Indexable pages without a canonical.", "Add a self-referencing canonical.")
add("canonicals", "multiple", "Multiple", f"{HTML} AND canonical_count>1", "warning", "Low", "Pages with more than one canonical.", "Keep one canonical.")
add("canonicals", "multiple_conflicting", "Multiple Conflicting", f"{HTML} AND canonical_conflict=1", "issue", "High", "Canonicals pointing to different URLs.", "Keep a single canonical.")
add("canonicals", "non_indexable_canonical", "Non-Indexable Canonical", f"{HTML} AND canonical_target_nonindexable=1", "issue", "High", "The canonical URL is itself non-indexable.", "Point canonicals at indexable 200 URLs.")
add("canonicals", "relative", "Canonical Is Relative", f"{HTML} AND canonical_relative=1", "warning", "Low", "Relative canonical hrefs.", "Use absolute URLs.")
add("canonicals", "unlinked", "Unlinked", f"{HTML} AND canonical_unlinked=1", "warning", "Medium", "Canonical URLs not linked anywhere in the crawl.", "Link to canonical URLs internally.")
add("canonicals", "invalid_attr", "Invalid Attribute In Annotation", f"{HTML} AND canonical_invalid_attr=1", "issue", "High", "Canonical link with a missing or malformed href.", "Fix the markup.")
add("canonicals", "fragment", "Contains Fragment URL", f"{HTML} AND canonical_fragment=1", "issue", "High", "Canonical URL contains a #fragment.", "Remove the fragment.")
add("canonicals", "outside_head", "Outside <head>", f"{HTML} AND canonical_outside_head=1", "issue", "High", "Canonical found outside <head>.", "Move into <head>.")

# --- Pagination -------------------------------------------------------------
PAG = f"{HTML} AND pagination_count>0"
add("pagination", "contains", "Contains Pagination", PAG)
add("pagination", "first_page", "First Page", f"{PAG} AND rel_next<>'' AND (rel_prev IS NULL OR rel_prev='')")
add("pagination", "paginated_2plus", "Paginated 2+ Pages", f"{PAG} AND rel_prev<>''")
add("pagination", "not_in_anchor", "Pagination URL Not In Anchor Tag", f"{PAG} AND pagination_not_anchor=1", "issue", "High", "rel=next/prev URLs that are not also linked with <a>.", "Add real links to paginated pages.")
add("pagination", "non_200", "Non-200 Pagination URL", f"{PAG} AND pag_non200=1", "issue", "High", "Pagination URLs that do not return 200.", "Fix the pagination URLs.")
add("pagination", "unlinked", "Unlinked Pagination URL", f"{PAG} AND pag_unlinked=1", "issue", "Medium", "Pagination URLs not linked from anywhere.", "Link paginated pages.")
add("pagination", "non_indexable", "Non-Indexable", f"{PAG} AND indexable=0", "warning", "High", "Paginated pages that are non-indexable.", "Paginated pages should normally be indexable and self-canonical.")
add("pagination", "multiple", "Multiple Pagination URLs", f"{PAG} AND pag_multiple=1", "issue", "Low", "More than one rel=next or rel=prev.", "Keep one of each.")
add("pagination", "loop", "Pagination Loop", f"{PAG} AND pag_loop=1", "issue", "Low", "rel=next chain loops back.", "Fix the pagination sequence.")
add("pagination", "sequence_error", "Sequence Error", f"{PAG} AND pag_sequence_error=1", "issue", "Low", "next/prev do not point back to each other.", "Fix the pagination sequence.")

# --- Directives -------------------------------------------------------------
D = f"{HTML}"
add("directives", "index", "Index", f"{D} AND {_tok('index')}")
add("directives", "noindex", "Noindex", f"{D} AND {_tok('noindex')}", "warning", "High", "Pages with a noindex directive.", "Confirm these should be excluded from the index.")
add("directives", "follow", "Follow", f"{D} AND {_tok('follow')}")
add("directives", "nofollow", "Nofollow", f"{D} AND {_tok('nofollow')}", "warning", "High", "Pages with a nofollow directive.", "Confirm links should not be followed.")
add("directives", "none", "None", f"{D} AND {_tok('none')}", "warning", "High", "Pages with the 'none' directive (noindex, nofollow).", "Confirm this is intended.")
add("directives", "noarchive", "NoArchive", f"{D} AND {_tok('noarchive')}")
add("directives", "nosnippet", "NoSnippet", f"{D} AND {_tok('nosnippet')}", "warning", "Low", "Pages blocking snippets.", "Confirm this is intended.")
add("directives", "max_snippet", "Max-Snippet", f"{D} AND {_tok('max-snippet')}")
add("directives", "max_image_preview", "Max-Image-Preview", f"{D} AND {_tok('max-image-preview')}")
add("directives", "max_video_preview", "Max-Video-Preview", f"{D} AND {_tok('max-video-preview')}")
add("directives", "noodp", "NoODP", f"{D} AND {_tok('noodp')}", "warning", "Low", "Obsolete directive.", "Remove.")
add("directives", "noydir", "NoYDIR", f"{D} AND {_tok('noydir')}", "warning", "Low", "Obsolete directive.", "Remove.")
add("directives", "noimageindex", "NoImageIndex", f"{D} AND {_tok('noimageindex')}", "issue", "Low", "Images on the page will not be indexed.", "Confirm this is intended.")
add("directives", "notranslate", "NoTranslate", f"{D} AND {_tok('notranslate')}", "warning", "Low", "Translation disabled.", "Confirm this is intended.")
add("directives", "unavailable_after", "Unavailable_After", f"{D} AND {_tok('unavailable_after')}", "warning", "Medium", "Pages with an expiry directive.", "Check the date.")
add("directives", "refresh", "Refresh", f"{D} AND (meta_refresh<>'' OR http_refresh<>'')")
add("directives", "outside_head", "Outside <head>", f"{D} AND robots_outside_head=1", "issue", "High", "Robots meta tag outside <head>.", "Move into <head>.")

# --- Hreflang ---------------------------------------------------------------
HL = f"{HTML} AND hreflang_count>0"
add("hreflang", "contains", "Contains Hreflang", HL)
add("hreflang", "non_200", "Non-200 Hreflang URLs", f"{HL} AND hl_non200=1", "issue", "High", "Hreflang URLs that do not return 200.", "Point hreflang at 200 URLs.")
add("hreflang", "unlinked", "Unlinked Hreflang URLs", f"{HL} AND hl_unlinked=1", "issue", "Medium", "Hreflang URLs not linked anywhere in the crawl.", "Link alternate pages.")
add("hreflang", "missing_return", "Missing Return Links", f"{HL} AND hl_missing_return=1", "issue", "High", "Alternate pages do not link back.", "Add reciprocal hreflang.")
add("hreflang", "inconsistent", "Inconsistent Language & Region Return Links", f"{HL} AND hl_inconsistent=1", "issue", "High", "Return links use a different language code.", "Align codes across pages.")
add("hreflang", "noncanonical_return", "Non-Canonical Return Links", f"{HL} AND hl_noncanonical_return=1", "issue", "High", "Hreflang points at canonicalised URLs.", "Use canonical URLs in hreflang.")
add("hreflang", "noindex_return", "Noindex Return Links", f"{HL} AND hl_noindex_return=1", "issue", "High", "Hreflang points at noindex URLs.", "Use indexable URLs.")
add("hreflang", "bad_codes", "Incorrect Language & Region Codes", f"{HL} AND hl_bad_codes=1", "issue", "High", "Invalid ISO language/region codes.", "Use ISO 639-1 + optional ISO 3166-1 Alpha 2.")
add("hreflang", "multiple", "Multiple Entries", f"{HL} AND hl_multiple=1", "issue", "High", "Same language declared with different URLs.", "One URL per language/region.")
add("hreflang", "missing_self", "Missing Self Reference", f"{HL} AND hl_missing_self=1", "warning", "Low", "Page does not reference itself.", "Add a self-referencing hreflang.")
add("hreflang", "not_canonical", "Not Using Canonical", f"{HL} AND hl_not_canonical=1", "issue", "High", "Hreflang on a canonicalised page.", "Only canonical URLs should carry hreflang.")
add("hreflang", "missing_xdefault", "Missing X-Default", f"{HL} AND hl_missing_xdefault=1", "warning", "Low", "No x-default entry.", "Add x-default.")
add("hreflang", "missing", "Missing", f"{HTML} AND hreflang_count=0 AND EXISTS (SELECT 1 FROM urls h WHERE h.hreflang_count>0 LIMIT 1)")
add("hreflang", "outside_head", "Outside <head>", f"{HL} AND hreflang_outside_head=1", "issue", "High", "Hreflang link outside <head>.", "Move into <head>.")

# --- JavaScript (populated only when Config > Rendering = JavaScript) --------
JS = f"{HTML} AND rendered=1"
_NOIDX = "REGEXP '(^|[,\\s])noindex($|[,\\s])'"
_NOFOL = "REGEXP '(^|[,\\s])nofollow($|[,\\s])'"
add("javascript", "blocked_resources", "Pages with Blocked Resources", f"{JS} AND js_blocked>0", "warning", "High", "Resources needed to render the page are blocked by robots.txt.", "Allow Googlebot to fetch JS/CSS/API resources.")
add("javascript", "js_links", "Contains JavaScript Links", f"{JS} AND js_links_count>0", "warning", "Medium", "Hyperlinks only present in the rendered HTML.", "Make sure important links are in the raw HTML or rendered reliably.")
add("javascript", "js_content", "Contains JavaScript Content", f"{JS} AND js_content=1", "warning", "Medium", "Text content only present after rendering.", "Server-render important content where possible.")
add("javascript", "noindex_orig_only", "Noindex Only in Original HTML", f"{JS} AND coalesce(orig_meta_robots,'') {_NOIDX} AND NOT (coalesce(meta_robots,'') {_NOIDX})", "issue", "High", "JavaScript removes a noindex that is in the raw HTML; Google will see the noindex and never render.", "Decide on one directive and set it server-side.")
add("javascript", "nofollow_orig_only", "Nofollow Only in Original HTML", f"{JS} AND coalesce(orig_meta_robots,'') {_NOFOL} AND NOT (coalesce(meta_robots,'') {_NOFOL})", "issue", "High", "JavaScript removes a nofollow that is in the raw HTML.", "Set directives server-side.")
add("javascript", "canonical_rendered_only", "Canonical Only in Rendered HTML", f"{JS} AND coalesce(orig_canonical,'')='' AND canonical<>''", "warning", "Low", "Canonical only added by JavaScript.", "Put the canonical in the raw HTML.")
add("javascript", "canonical_mismatch", "Canonical Mismatch", f"{JS} AND coalesce(orig_canonical,'')<>'' AND canonical<>'' AND orig_canonical<>canonical", "issue", "High", "Raw and rendered canonicals differ.", "Make them consistent.")
add("javascript", "title_rendered_only", "Page Title Only in Rendered HTML", f"{JS} AND coalesce(orig_title,'')='' AND title<>''", "warning", "Medium", "Title only set by JavaScript.", "Set the title server-side.")
add("javascript", "title_updated", "Page Title Updated by JavaScript", f"{JS} AND coalesce(orig_title,'')<>'' AND title<>'' AND orig_title<>title", "warning", "Medium", "JavaScript changes the title.", "Keep raw and rendered titles identical.")
add("javascript", "md_rendered_only", "Meta Description Only in Rendered HTML", f"{JS} AND coalesce(orig_meta_desc,'')='' AND meta_desc<>''", "warning", "Medium", "", "Set it server-side.")
add("javascript", "md_updated", "Meta Description Updated by JavaScript", f"{JS} AND coalesce(orig_meta_desc,'')<>'' AND meta_desc<>'' AND orig_meta_desc<>meta_desc", "warning", "Medium", "", "Keep them identical.")
add("javascript", "h1_rendered_only", "H1 Only in Rendered HTML", f"{JS} AND coalesce(orig_h1,'')='' AND h1<>''", "warning", "Medium", "", "Set it server-side.")
add("javascript", "h1_updated", "H1 Updated by JavaScript", f"{JS} AND coalesce(orig_h1,'')<>'' AND h1<>'' AND orig_h1<>h1", "warning", "Medium", "", "Keep them identical.")
add("javascript", "old_ajax_urls", "Uses Old AJAX Crawling Scheme URLs", f"{HTML} AND (url LIKE '%#!%' OR url LIKE '%_escaped_fragment_%')", "issue", "Medium", "Deprecated #! AJAX crawling scheme.", "Use normal URLs with server-side or client-side rendering Google supports.")
add("javascript", "old_ajax_meta", "Uses Old AJAX Crawling Scheme Meta Fragment Tag", f"{HTML} AND meta_fragment=1", "issue", "Medium", "<meta name=\"fragment\" content=\"!\"> is deprecated.", "Remove it.")
add("javascript", "js_errors", "Pages with JavaScript Errors", f"{JS} AND js_errors>0", "warning", "Low", "Console errors or uncaught exceptions while rendering.", "Fix the JavaScript errors (see the Console Log pane).")
add("javascript", "render_failed", "Render Failed", f"{HTML} AND render_error IS NOT NULL AND render_error<>''", "warning", "Medium", "Headless Chrome could not render the page.", "Check the Render Error column.")

# --- Links ------------------------------------------------------------------
L = f"{HTML}"
add("links", "high_crawl_depth", "Pages With High Crawl Depth", f"{L} AND depth>{{high_crawl_depth}}", "opportunity", "Medium", "Pages more than {high_crawl_depth} clicks from the start URL.", "Improve internal linking.")
add("links", "no_internal_outlinks", "Pages Without Internal Outlinks", f"{L} AND outlinks=0", "warning", "High", "Dead-end pages.", "Add internal links.")
add("links", "nofollow_outlinks", "Internal Nofollow Outlinks", f"{L} AND outlinks_nofollow>0", "warning", "Low", "Pages with nofollow internal links.", "Remove nofollow from internal links.")
add("links", "no_anchor", "Internal Outlinks With No Anchor Text", f"{L} AND outlinks_no_anchor>0", "opportunity", "Low", "Internal links without anchor text or alt.", "Add descriptive anchor text.")
add("links", "nondescriptive", "Non-Descriptive Anchor Text In Internal Outlinks", f"{L} AND outlinks_nondescriptive>0", "opportunity", "Low", "Links like 'click here'.", "Use descriptive anchors.")
add("links", "high_external", "Pages With High External Outlinks", f"{L} AND ext_outlinks>{{high_external_outlinks}}", "warning", "Low", "More than {high_external_outlinks} external links.", "Review external links.")
add("links", "high_internal", "Pages With High Internal Outlinks", f"{L} AND outlinks>{{high_internal_outlinks}}", "warning", "Low", "More than {high_internal_outlinks} internal links.", "Reduce link bloat.")
add("links", "follow_nofollow_inlinks", "Follow & Nofollow Internal Inlinks To Page", f"{L} AND inlinks_follow>0 AND inlinks_nofollow>0", "warning", "Low", "Mixed follow/nofollow inlinks.", "Be consistent.")
add("links", "nofollow_inlinks_only", "Internal Nofollow Inlinks Only", f"{L} AND inlinks_nofollow>0 AND inlinks_follow=0", "warning", "Low", "Only nofollow inlinks.", "Add followed links.")
add("links", "localhost", "Outlinks To Localhost", f"{L} AND localhost_outlinks>0", "issue", "High", "Links to localhost / 127.0.0.1.", "Fix the links.")
add("links", "nonindexable_inlinks_only", "Non-Indexable Page Inlinks Only", f"{L} AND indexable=1 AND inlinks>0 AND inlinks_from_indexable=0", "warning", "High", "Indexable pages linked only from non-indexable pages.", "Link from indexable pages.")
add("links", "uncrawlable", "Pages With Uncrawlable Internal Outlinks", f"{L} AND uncrawlable_outlinks>0", "warning", "High", "Links with javascript: or malformed hrefs.", "Use crawlable <a href> links.")

# --- Validation -------------------------------------------------------------
V = f"{HTML}"
add("validation", "invalid_head", "Invalid HTML Elements In <head>", f"{V} AND invalid_head_elements=1", "warning", "High", "Elements in <head> that end it early (browsers then ignore later head tags).", "Keep only valid head elements.")
add("validation", "body_before_html", "<body> Element Preceding <html>", f"{V} AND body_before_html=1", "warning", "High", "", "Fix document structure.")
add("validation", "head_not_first", "<head> Not First In <html> Element", f"{V} AND head_not_first=1", "warning", "High", "", "Put <head> first inside <html>.")
add("validation", "head_missing", "Missing <head> Tag", f"{V} AND head_missing=1", "issue", "High", "", "Add a <head>.")
add("validation", "head_multiple", "Multiple <head> Tags", f"{V} AND head_multiple=1", "issue", "High", "", "Keep one <head>.")
add("validation", "body_missing", "Missing <body> Tag", f"{V} AND body_missing=1", "issue", "High", "", "Add a <body>.")
add("validation", "body_multiple", "Multiple <body> Tags", f"{V} AND body_multiple=1", "issue", "High", "", "Keep one <body>.")
add("validation", "doc_over_2mb", "HTML Document Over 2MB", f"{V} AND size>2097152", "issue", "High", "Google stops parsing HTML after 2MB.", "Reduce document size.")
add("validation", "resource_over_2mb", "Resource Over 2MB", f"{INT} AND crawled=1 AND type<>'HTML' AND size>2097152", "issue", "High", "", "Reduce resource size.")
add("validation", "high_carbon", "High Carbon Rating", f"{V} AND 0", "opportunity", "Low", "(phase 2)", "")

# --- Sitemaps ---------------------------------------------------------------
add("sitemaps", "in_sitemap", "URLs In Sitemap", f"{INT} AND in_sitemap=1")
add("sitemaps", "not_in_sitemap", "URLs Not In Sitemap", f"{HTML} AND indexable=1 AND in_sitemap=0 AND EXISTS (SELECT 1 FROM sitemaps LIMIT 1)", "issue", "Medium", "Indexable pages missing from the XML sitemap.", "Add them to the sitemap.")
add("sitemaps", "orphan", "Orphan URLs", f"{HTML} AND in_sitemap=1 AND unique_inlinks=0", "issue", "Medium", "Sitemap URLs with no internal inlinks.", "Link to them or remove from the sitemap.")
add("sitemaps", "non_indexable_in_sitemap", "Non-Indexable URLs in Sitemap", f"{INT} AND in_sitemap=1 AND indexable=0", "issue", "Medium", "Sitemap contains non-indexable URLs.", "Only list indexable canonical URLs.")
add("sitemaps", "multiple_sitemaps", "URLs In Multiple Sitemaps", f"{INT} AND sitemap_count>1", "warning", "Low", "", "List each URL once.")
add("sitemaps", "over_50k", "XML Sitemap With Over 50k URLs", f"url IN (SELECT url FROM sitemaps WHERE url_count>50000)", "issue", "High", "", "Split the sitemap.")
add("sitemaps", "over_50mb", "XML Sitemap With Over 50mb", f"url IN (SELECT url FROM sitemaps WHERE size>52428800)", "issue", "High", "", "Split the sitemap.")


# ----------------------------------------------------------------------------- dynamic filters
# "All" filter scope per tab (Internal/External define their own). Shown first, like Screaming Frog.
TAB_ALL: dict[str, str] = {
    "security": SEC, "response_codes": "crawled IN (1,3)", "url": VISIBLE, "page_titles": HTML, "meta_description": HTML, "meta_keywords": HTML,
    "h1": HTML, "h2": HTML, "content": HTML, "images": IMG, "canonicals": HTML, "pagination": HTML, "directives": HTML, "hreflang": HTML,
    "javascript": HTML, "links": HTML, "structured_data": HTML, "amp": AMP_SCOPE, "pagespeed": LH, "accessibility": LH, "search_console": GSC_SCOPE, "analytics": GA_SCOPE, "link_metrics": LMS, "validation": f"{INT} AND crawled=1", "sitemaps": INT, "custom_search": HTML, "custom_extraction": HTML,
}


def all_filters(cfg: CrawlConfig) -> list[dict[str, Any]]:
    out = []
    idx = " AND indexable=1" if cfg.ignore_non_indexable_for_issues else ""
    vals = cfg.to_dict()
    seen_tabs: set[str] = set()
    for f in F:
        if f["tab"] not in seen_tabs:
            seen_tabs.add(f["tab"])
            if f["tab"] in TAB_ALL:
                out.append({"tab": f["tab"], "key": f"{f['tab']}:all", "short": "all", "name": "All", "where": TAB_ALL[f["tab"]],
                            "kind": "", "priority": "", "desc": "", "fix": "", "issue_only": False})
        g = dict(f)
        g["where"] = g["where"].replace("{IDX}", idx).format(**vals)
        g["name"] = g["name"].format(**vals)
        g["desc"] = (g["desc"] or "").format(**vals)
        out.append(g)
    for tab in ("custom_search", "custom_extraction"):
        if (tab == "custom_search" and cfg.custom_search) or (tab == "custom_extraction" and cfg.custom_extraction):
            out.append({"tab": tab, "key": f"{tab}:all", "short": "all", "name": "All", "where": TAB_ALL[tab], "kind": "", "priority": "", "desc": "", "fix": "", "issue_only": False})
    for i, cs in enumerate(cfg.custom_search or []):
        name = cs.get("name") or cs.get("text", f"Search {i+1}")
        out.append({"tab": "custom_search", "key": f"custom_search:{i}", "short": str(i), "name": name,
                    "where": f"{HTML} AND json_extract(custom, '$.{_jkey(name)}') = 1", "kind": "", "priority": "", "desc": "", "fix": "", "issue_only": False})
    for i, ce in enumerate(cfg.custom_extraction or []):
        name = ce.get("name") or ce.get("expr", f"Extraction {i+1}")
        out.append({"tab": "custom_extraction", "key": f"custom_extraction:{i}", "short": str(i), "name": name,
                    "where": f"{HTML} AND json_array_length(json_extract(custom, '$.{_jkey(name)}')) > 0", "kind": "", "priority": "", "desc": "", "fix": "", "issue_only": False})
    return out


def _jkey(name: str) -> str:
    return '"' + name.replace('"', '\\"') + '"'


def filters_by_key(cfg: CrawlConfig) -> dict[str, dict[str, Any]]:
    return {f["key"]: f for f in all_filters(cfg)}


def rebuild_issue_table(con: sqlite3.Connection, cfg: CrawlConfig) -> None:
    con.execute("DELETE FROM url_issues")
    for f in all_filters(cfg):
        try:
            con.execute(f"INSERT OR IGNORE INTO url_issues(url_id, key) SELECT id, ? FROM urls WHERE {f['where']}", (f["key"],))
        except sqlite3.Error as exc:
            print(f"[issues] filter {f['key']} failed: {exc}")


def counts(con: sqlite3.Connection, cfg: CrawlConfig) -> dict[str, int]:
    c = {r[0]: r[1] for r in con.execute("SELECT key, count(*) FROM url_issues GROUP BY key")}
    return {f["key"]: c.get(f["key"], 0) for f in all_filters(cfg)}


# ----------------------------------------------------------------------------- grid columns
COL_LABELS: dict[str, str] = {
    "url": "Address", "content_type": "Content Type", "status_code": "Status Code", "status": "Status",
    "indexable": "Indexability", "indexability_status": "Indexability Status",
    "title": "Title 1", "title_len": "Title 1 Length", "title_px": "Title 1 Pixel Width",
    "meta_desc": "Meta Description 1", "md_len": "Meta Description 1 Length", "md_px": "Meta Description 1 Pixel Width",
    "meta_kw": "Meta Keywords 1", "kw_len": "Meta Keywords 1 Length",
    "h1": "H1-1", "h1_len": "H1-1 Length", "h1_2": "H1-2", "h2": "H2-1", "h2_len": "H2-1 Length", "h2_2": "H2-2",
    "meta_robots": "Meta Robots 1", "x_robots": "X-Robots-Tag 1", "meta_refresh": "Meta Refresh 1", "http_refresh": "HTTP Refresh",
    "canonical": "Canonical Link Element 1", "canonical_http": "HTTP Canonical", "rel_next": "rel=\"next\" 1", "rel_prev": "rel=\"prev\" 1",
    "amp_url": "amphtml Link Element", "mobile_alt": "Mobile Alternate Link",
    "size": "Size (bytes)", "transferred": "Transferred (bytes)", "word_count": "Word Count", "sentence_count": "Sentence Count",
    "avg_words_sentence": "Average Words Per Sentence", "flesch": "Flesch Reading Ease Score", "readability": "Readability",
    "text_ratio": "Text Ratio", "depth": "Crawl Depth", "folder_depth": "Folder Depth", "link_score": "Link Score",
    "inlinks": "Inlinks", "unique_inlinks": "Unique Inlinks", "outlinks": "Outlinks", "unique_outlinks": "Unique Outlinks",
    "ext_outlinks": "External Outlinks", "unique_ext_outlinks": "Unique External Outlinks",
    "closest_similarity": "Closest Similarity Match", "near_dup_count": "No. Near Duplicates", "closest_match": "Closest Match URL", "hash": "Hash",
    "spelling_errors": "Spelling Errors", "grammar_errors": "Grammar Errors",
    "sd_count": "Structured Data Items", "sd_jsonld": "JSON-LD Items", "sd_microdata": "Microdata Items", "sd_rdfa": "RDFa Items",
    "sd_errors": "Validation Errors", "sd_warnings": "Validation Warnings", "sd_parse_errors": "Parse Errors", "sd_types": "Schema Types",
    "is_amp": "AMP Page", "amp_issue_count": "AMP Issues", "amp_issues": "AMP Validation Messages",
    "gsc_clicks": "Clicks", "gsc_impressions": "Impressions", "gsc_ctr": "CTR %", "gsc_position": "Position", "gsc_orphan": "GSC Orphan",
    "ga_sessions": "GA4 Sessions", "ga_engaged": "Engaged Sessions", "ga_engagement_rate": "Engagement Rate %", "ga_bounce_rate": "Bounce Rate %", "ga_views": "Views", "ga_key_events": "Key Events", "ga_orphan": "GA4 Orphan",
    "inspect_verdict": "Index Verdict", "inspect_coverage": "Coverage", "inspect_indexing_state": "Indexing State", "inspect_last_crawl": "Last Crawl (Google)", "inspect_robots": "Robots.txt State",
    "inspect_fetch": "Page Fetch", "inspect_google_canonical": "Google Canonical", "inspect_user_canonical": "User Canonical", "inspect_mobile": "Mobile Usability", "inspect_at": "Inspected",
    "lm_ahrefs_ur": "Ahrefs URL Rating", "lm_ahrefs_dr": "Ahrefs Domain Rating", "lm_ahrefs_backlinks": "Ahrefs Backlinks", "lm_ahrefs_refdomains": "Ahrefs Ref. Domains",
    "lm_ahrefs_keywords": "Ahrefs Keywords", "lm_ahrefs_traffic": "Ahrefs Traffic", "lm_moz_pa": "Moz Page Authority", "lm_moz_da": "Moz Domain Authority", "lm_moz_spam": "Moz Spam Score",
    "lm_moz_linking_domains": "Moz Linking Domains", "lm_moz_backlinks": "Moz External Links", "lm_majestic_tf": "Majestic Trust Flow", "lm_majestic_cf": "Majestic Citation Flow",
    "lm_majestic_backlinks": "Majestic Backlinks", "lm_majestic_refdomains": "Majestic Ref. Domains", "lm_at": "Link Metrics Fetched", "lm_providers": "Link Metrics Providers",
    "lh_perf": "Performance Score", "lh_a11y": "Accessibility Score", "lh_bp": "Best Practices Score", "lh_seo": "SEO Score", "lh_lcp": "LCP (ms)", "lh_cls": "CLS",
    "lh_tbt": "TBT (ms)", "lh_fcp": "FCP (ms)", "lh_si": "Speed Index (ms)", "lh_tti": "Time to Interactive (ms)", "lh_field_lcp": "Field LCP (ms)", "lh_field_cls": "Field CLS",
    "lh_field_inp": "Field INP (ms)", "lh_perf_fails": "Failed Performance Audits", "lh_a11y_fails": "Failed Accessibility Audits", "lh_at": "Lighthouse Run", "lh_device": "Device", "lh_source": "Source",
    "response_time": "Response Time", "last_modified": "Last Modified", "redirect_url": "Redirect URL", "redirect_type": "Redirect Type",
    "redirect_chain_len": "Redirect Chain Length", "final_url": "Final URL", "final_status": "Final Status Code",
    "lang": "Language", "http_version": "HTTP Version", "crawl_ts": "Crawl Timestamp", "type": "Type",
    "hreflang_count": "Hreflang Count", "hreflang": "Hreflang", "canonical_count": "Canonical Count", "pagination_count": "Pagination Count",
    "in_sitemap": "In Sitemap", "sitemap_count": "Sitemap Count", "exact_dup": "Exact Duplicates", "title_dup": "Title Duplicates",
    "md_dup": "Meta Description Duplicates", "h1_dup": "H1 Duplicates", "h2_dup": "H2 Duplicates", "img_width": "Width", "img_height": "Height",
    "custom": "Custom", "error": "Error", "discovered_from": "Discovered From", "cookies": "Cookies", "soft_404": "Soft 404",
    "rendered": "Rendered", "render_time": "Render Time", "render_error": "Render Error", "orig_title": "HTML Title 1", "orig_meta_desc": "HTML Meta Description 1",
    "orig_h1": "HTML H1-1", "orig_canonical": "HTML Canonical", "orig_meta_robots": "HTML Meta Robots", "orig_word_count": "HTML Word Count",
    "js_word_delta": "Word Count Change", "js_word_pct": "JS Word Count %", "js_links_count": "JS Links", "js_errors": "JS Errors",
    "js_blocked": "Blocked Resources", "js_inlinks": "Unique JS Inlinks", "js_outlinks": "Unique JS Outlinks",
}

BASE = ["url", "content_type", "status_code", "status", "indexable", "indexability_status"]
COLUMNS: dict[str, list[str]] = {
    "internal": BASE + ["title", "title_len", "title_px", "meta_desc", "md_len", "md_px", "meta_kw", "kw_len", "h1", "h1_len", "h2", "h2_len",
                        "meta_robots", "x_robots", "meta_refresh", "canonical", "rel_next", "rel_prev", "amp_url", "size", "transferred",
                        "word_count", "sentence_count", "avg_words_sentence", "flesch", "readability", "text_ratio", "depth", "folder_depth",
                        "link_score", "inlinks", "unique_inlinks", "outlinks", "unique_outlinks", "ext_outlinks", "unique_ext_outlinks",
                        "closest_similarity", "near_dup_count", "hash", "response_time", "last_modified", "redirect_url", "redirect_type",
                        "cookies", "lang", "http_version", "mobile_alt", "rendered", "render_time", "crawl_ts"],
    "external": BASE + ["type", "size", "inlinks", "unique_inlinks", "response_time", "redirect_url", "crawl_ts"],
    "security": BASE + ["type", "inlinks"],
    "response_codes": BASE + ["type", "response_time", "redirect_url", "redirect_type", "redirect_chain_len", "final_url", "final_status", "inlinks", "error"],
    "url": BASE + ["type", "depth", "folder_depth", "inlinks"],
    "page_titles": BASE + ["title", "title_len", "title_px", "h1", "inlinks"],
    "meta_description": BASE + ["meta_desc", "md_len", "md_px", "inlinks"],
    "meta_keywords": BASE + ["meta_kw", "kw_len"],
    "h1": BASE + ["h1", "h1_len", "h1_2", "inlinks"],
    "h2": BASE + ["h2", "h2_len", "h2_2", "inlinks"],
    "amp": BASE + ["is_amp", "amp_url", "canonical", "amp_issue_count", "amp_issues"],
    "link_metrics": BASE + ["lm_ahrefs_ur", "lm_ahrefs_dr", "lm_ahrefs_backlinks", "lm_ahrefs_refdomains", "lm_ahrefs_keywords", "lm_ahrefs_traffic", "lm_moz_pa", "lm_moz_da", "lm_moz_spam", "lm_moz_linking_domains", "lm_moz_backlinks", "lm_majestic_tf", "lm_majestic_cf", "lm_majestic_backlinks", "lm_majestic_refdomains", "inlinks", "lm_providers", "lm_at"],
    "search_console": BASE + ["gsc_clicks", "gsc_impressions", "gsc_ctr", "gsc_position", "inspect_verdict", "inspect_coverage", "inspect_google_canonical", "inspect_last_crawl", "inlinks"],
    "analytics": BASE + ["ga_sessions", "ga_engaged", "ga_engagement_rate", "ga_bounce_rate", "ga_views", "ga_key_events", "gsc_clicks", "inlinks"],
    "pagespeed": BASE + ["lh_perf", "lh_lcp", "lh_cls", "lh_tbt", "lh_fcp", "lh_si", "lh_tti", "lh_field_lcp", "lh_field_cls", "lh_field_inp", "lh_perf_fails", "lh_bp", "lh_seo", "lh_device", "lh_source", "lh_at"],
    "accessibility": BASE + ["lh_a11y", "lh_a11y_fails", "lh_perf", "lh_device", "lh_at"],
    "structured_data": BASE + ["sd_types", "sd_count", "sd_jsonld", "sd_microdata", "sd_rdfa", "sd_errors", "sd_warnings", "sd_parse_errors"],
    "content": BASE + ["word_count", "sentence_count", "avg_words_sentence", "flesch", "readability", "text_ratio", "exact_dup", "near_dup_count", "closest_similarity", "closest_match", "spelling_errors", "grammar_errors", "hash", "soft_404"],
    "images": BASE + ["size", "inlinks", "unique_inlinks", "img_width", "img_height"],
    "canonicals": BASE + ["canonical", "canonical_count", "canonical_http", "meta_robots", "inlinks"],
    "pagination": BASE + ["rel_next", "rel_prev", "pagination_count", "canonical"],
    "directives": BASE + ["meta_robots", "x_robots", "meta_refresh", "http_refresh"],
    "hreflang": BASE + ["hreflang_count", "hreflang", "canonical", "lang"],
    "javascript": BASE + ["orig_title", "title", "orig_meta_desc", "meta_desc", "orig_h1", "h1", "orig_canonical", "canonical", "orig_meta_robots", "meta_robots",
                          "orig_word_count", "word_count", "js_word_delta", "js_word_pct", "js_links_count", "js_inlinks", "js_outlinks", "js_errors", "js_blocked", "render_time", "render_error"],
    "links": BASE + ["depth", "link_score", "inlinks", "unique_inlinks", "outlinks", "unique_outlinks", "ext_outlinks", "unique_ext_outlinks", "js_inlinks", "js_outlinks"],
    "validation": BASE + ["size", "type"],
    "sitemaps": BASE + ["in_sitemap", "sitemap_count", "inlinks", "unique_inlinks"],
    "custom_search": BASE + ["custom"],
    "custom_extraction": BASE + ["custom"],
}
