"""Mock of the Google endpoints the app uses (OAuth token, userinfo, Search Console sites / searchAnalytics /
urlInspection, GA4 accountSummaries / runReport) so the integration can be tested without credentials.

    python tests/mock_google.py <port> <crawled_url_1> <crawled_url_2> ...
The URLs given become pages with Search Analytics / GA4 data; one extra orphan URL is always added.
"""
from __future__ import annotations

import json
import sys
from http.server import BaseHTTPRequestHandler, HTTPServer
from urllib.parse import urlsplit

PORT = int(sys.argv[1]) if len(sys.argv) > 1 else 8870
PAGES = sys.argv[2:] or ["https://example.test/"]
ORIGIN = f"{urlsplit(PAGES[0]).scheme}://{urlsplit(PAGES[0]).netloc}"
HOST = urlsplit(PAGES[0]).hostname or "example.test"
CALLS: list[str] = []


class H(BaseHTTPRequestHandler):
    def log_message(self, *a):
        pass

    def _json(self, code, obj):
        body = json.dumps(obj).encode()
        self.send_response(code); self.send_header("Content-Type", "application/json"); self.send_header("Content-Length", str(len(body))); self.end_headers(); self.wfile.write(body)

    def do_GET(self):
        CALLS.append("GET " + self.path)
        if self.path.startswith("/o/oauth2/v2/auth"):
            # the consent screen: tell the test the redirect it would make
            return self._json(200, {"consent": "ok", "query": self.path.split("?", 1)[1]})
        if self.path.startswith("/v1/userinfo"):
            return self._json(200, {"email": "arun@example.test"})
        if self.path.startswith("/webmasters/v3/sites"):
            return self._json(200, {"siteEntry": [{"siteUrl": f"sc-domain:{HOST}", "permissionLevel": "siteOwner"}, {"siteUrl": f"https://other.test/", "permissionLevel": "siteFullUser"}]})
        if self.path.startswith("/v1beta/accountSummaries"):
            return self._json(200, {"accountSummaries": [{"displayName": "Example Ltd", "propertySummaries": [{"property": "properties/123", "displayName": "Example web"}]}]})
        if self.path == "/calls":
            return self._json(200, CALLS)
        self._json(404, {"error": "unknown " + self.path})

    def do_POST(self):
        CALLS.append("POST " + self.path)
        raw = self.rfile.read(int(self.headers.get("Content-Length", 0)) or 0)
        if self.path == "/token":
            form = dict(x.split("=", 1) for x in raw.decode().split("&") if "=" in x)
            if form.get("grant_type") == "authorization_code":
                return self._json(200, {"access_token": "at-1", "refresh_token": "rt-1", "expires_in": 3600})
            return self._json(200, {"access_token": "at-2", "expires_in": 3600})
        if "/searchAnalytics/query" in self.path:
            rows = [{"keys": [u], "clicks": 100 - i * 10, "impressions": 2000 - i * 100, "ctr": 0.05 - i * 0.005, "position": 3.2 + i} for i, u in enumerate(PAGES)]
            rows.append({"keys": [ORIGIN + "/old-landing-page/"], "clicks": 7, "impressions": 900, "ctr": 0.0078, "position": 14.0})
            return self._json(200, {"rows": rows})
        if self.path.startswith("/v1/urlInspection/index:inspect"):
            body = json.loads(raw); u = body["inspectionUrl"]
            indexed = not u.endswith("/pricing/")
            return self._json(200, {"inspectionResult": {"indexStatusResult": {"verdict": "PASS" if indexed else "NEUTRAL", "coverageState": "Submitted and indexed" if indexed else "Crawled - currently not indexed",
                                                                                  "indexingState": "INDEXING_ALLOWED", "lastCrawlTime": "2026-10-13T04:12:00Z", "robotsTxtState": "ALLOWED", "pageFetchState": "SUCCESSFUL",
                                                                                  "googleCanonical": u if indexed else ORIGIN + "/", "userCanonical": u}, "mobileUsabilityResult": {"verdict": "PASS"}}})
        if ":runReport" in self.path:
            rows = [{"dimensionValues": [{"value": urlsplit(u).path or "/"}], "metricValues": [{"value": str(500 - i * 50)}, {"value": str(300 - i * 30)}, {"value": str(0.6)}, {"value": str(0.4 + i * 0.1)}, {"value": str(800 - i * 80)}, {"value": str(5 - i)}]} for i, u in enumerate(PAGES)]
            rows.append({"dimensionValues": [{"value": "/campaign/spring/"}], "metricValues": [{"value": "40"}, {"value": "10"}, {"value": "0.25"}, {"value": "0.75"}, {"value": "60"}, {"value": "0"}]})
            return self._json(200, {"rows": rows})
        self._json(404, {"error": "unknown " + self.path})


print(f"mock google on http://127.0.0.1:{PORT}", flush=True)
HTTPServer(("127.0.0.1", PORT), H).serve_forever()
