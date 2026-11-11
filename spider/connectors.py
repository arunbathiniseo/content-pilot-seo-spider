"""Site connectors: push an approved fix to a CMS. Plug-and-play - add a class, register it in CONNECTORS.

Target config (Config > Connectors): {name, type, env: staging|live, base_url, username, secret, extra}
"""
from __future__ import annotations

import base64
import hashlib
import hmac
import json
import re
import xmlrpc.client
from dataclasses import dataclass
from typing import Any
from urllib.parse import urlsplit

import httpx


@dataclass
class PushResult:
    ok: bool
    message: str
    detail: str = ""


class Connector:
    type_name = "base"
    label = "Base"
    supports = {"title", "meta_description", "h1", "alt_text", "canonical", "redirect"}
    fields = [("base_url", "Site URL"), ("username", "Username"), ("secret", "Password / token")]

    def __init__(self, target: dict[str, Any]):
        self.t = target
        self.base = (target.get("base_url") or "").rstrip("/")

    async def test(self) -> PushResult:
        raise NotImplementedError

    async def apply(self, fix: dict[str, Any]) -> PushResult:
        raise NotImplementedError


# ----------------------------------------------------------------------------- WordPress REST
class WordPressREST(Connector):
    """WordPress 5.6+ REST API with an Application Password (Users > Profile > Application Passwords).
    Titles go to the post title; meta descriptions / canonicals go to Yoast or Rank Math meta fields when the
    site exposes them; alt text to the media library; redirects need the Redirection plugin."""
    type_name = "wordpress"
    label = "WordPress (REST API + application password)"
    fields = [("base_url", "Site URL, e.g. https://staging.example.com"), ("username", "WordPress username"), ("secret", "Application password")]

    def _client(self) -> httpx.AsyncClient:
        token = base64.b64encode(f"{self.t.get('username', '')}:{self.t.get('secret', '')}".encode()).decode()
        return httpx.AsyncClient(timeout=30.0, headers={"Authorization": f"Basic {token}", "User-Agent": "Mozilla/5.0 ContentPilotSpider/0.1", "Accept": "application/json"}, follow_redirects=True)

    async def test(self) -> PushResult:
        async with self._client() as c:
            try:
                r = await c.get(f"{self.base}/wp-json/wp/v2/users/me", params={"context": "edit"})
            except httpx.HTTPError as exc:
                return PushResult(False, f"Cannot reach {self.base}: {exc}")
        if r.status_code == 200:
            u = r.json()
            return PushResult(True, f"Connected as {u.get('name') or u.get('slug')} (roles: {', '.join(u.get('roles', []))})")
        if r.status_code in (401, 403):
            return PushResult(False, "Authentication failed: check the username and application password (REST may also be blocked by a security plugin or Cloudflare).", r.text[:300])
        return PushResult(False, f"Unexpected response {r.status_code}", r.text[:300])

    async def _types(self, c: httpx.AsyncClient) -> dict[str, str]:
        r = await c.get(f"{self.base}/wp-json/wp/v2/types", params={"context": "edit"})
        if r.status_code != 200:
            return {"post": "posts", "page": "pages"}
        return {k: v.get("rest_base", k) for k, v in r.json().items()}

    async def _find(self, c: httpx.AsyncClient, url: str) -> tuple[str, int] | None:
        """Resolve a public URL to (rest_base, id)."""
        path = urlsplit(url).path.rstrip("/")
        slug = path.split("/")[-1] if path else ""
        types = await self._types(c)
        if not slug:   # homepage: front page setting
            r = await c.get(f"{self.base}/wp-json/wp/v2/settings")
            if r.status_code == 200 and r.json().get("page_on_front"):
                return ("pages", int(r.json()["page_on_front"]))
            return None
        r = await c.get(f"{self.base}/wp-json/wp/v2/search", params={"search": slug, "per_page": 20})
        if r.status_code == 200:
            for item in r.json():
                if (item.get("url") or "").rstrip("/") == url.rstrip("/"):
                    return (types.get(item.get("subtype"), item.get("subtype") + "s"), int(item["id"]))
        for base in ("pages", "posts"):
            r = await c.get(f"{self.base}/wp-json/wp/v2/{base}", params={"slug": slug, "status": "any", "per_page": 5})
            if r.status_code == 200:
                for item in r.json():
                    if (item.get("link") or "").rstrip("/") == url.rstrip("/"):
                        return (base, int(item["id"]))
        return None

    async def apply(self, fix: dict[str, Any]) -> PushResult:
        kind, url, value = fix["kind"], fix["url"], fix.get("proposed") or ""
        async with self._client() as c:
            if kind == "alt_text":
                return await self._alt(c, fix)
            if kind == "redirect":
                return await self._redirect(c, fix)
            found = await self._find(c, url)
            if not found:
                return PushResult(False, "Could not find this URL in WordPress (not a post/page, or slug differs).")
            rest_base, pid = found
            ep = f"{self.base}/wp-json/wp/v2/{rest_base}/{pid}"
            if kind in ("title", "h1"):
                r = await c.post(ep, json={"title": value})
                return self._done(r, "Post title updated" + (" (H1 follows the post title on most themes)" if kind == "h1" else ""))
            if kind in ("meta_description", "canonical"):
                yoast = "_yoast_wpseo_metadesc" if kind == "meta_description" else "_yoast_wpseo_canonical"
                rank = "rank_math_description" if kind == "meta_description" else "rank_math_canonical_url"
                for key in (yoast, rank):
                    r = await c.post(ep, json={"meta": {key: value}})
                    if r.status_code == 200 and (r.json().get("meta") or {}).get(key) == value:
                        return PushResult(True, f"{'Yoast' if key == yoast else 'Rank Math'} field {key} updated")
                return PushResult(False, "The site does not expose its SEO plugin meta fields to the REST API. Yoast: install the 'Yoast REST meta' snippet or use the Webhook connector; Rank Math: enable 'REST API' in its settings.", r.text[:300])
        return PushResult(False, f"Fix kind '{kind}' is exported as a brief, not pushed.")

    async def _alt(self, c: httpx.AsyncClient, fix: dict[str, Any]) -> PushResult:
        img = fix.get("url")
        name = urlsplit(img).path.split("/")[-1]
        stem = re.sub(r"-\d+x\d+(?=\.\w+$)", "", name).rsplit(".", 1)[0]
        r = await c.get(f"{self.base}/wp-json/wp/v2/media", params={"search": stem, "per_page": 20})
        if r.status_code != 200:
            return PushResult(False, f"Media search failed ({r.status_code})", r.text[:200])
        for m in r.json():
            src = (m.get("source_url") or "")
            if src.rsplit("/", 1)[-1].rsplit(".", 1)[0] == stem or src == img:
                r2 = await c.post(f"{self.base}/wp-json/wp/v2/media/{m['id']}", json={"alt_text": fix.get("proposed") or ""})
                return self._done(r2, "Media alt text updated (applies where the theme prints the library alt)")
        return PushResult(False, "Image not found in the media library (external or theme image).")

    async def _redirect(self, c: httpx.AsyncClient, fix: dict[str, Any]) -> PushResult:
        src = urlsplit(fix["url"]).path or "/"
        body = {"url": src, "match_type": "url", "action_type": "url", "action_code": 301, "action_data": {"url": fix.get("proposed") or "/"}, "group_id": 1, "status": "enabled"}
        r = await c.post(f"{self.base}/wp-json/redirection/v1/redirect", json=body)
        if r.status_code == 404:
            return PushResult(False, "The Redirection plugin is not installed; redirect exported as a brief instead.")
        return self._done(r, f"301 redirect created: {src} → {fix.get('proposed')}")

    @staticmethod
    def _done(r: httpx.Response, ok_msg: str) -> PushResult:
        if r.status_code in (200, 201):
            return PushResult(True, ok_msg)
        try:
            msg = r.json().get("message", "")
        except Exception:  # noqa: BLE001
            msg = r.text[:200]
        return PushResult(False, f"WordPress returned {r.status_code}: {msg}")


# ----------------------------------------------------------------------------- WordPress XML-RPC
class WordPressXMLRPC(Connector):
    """Older/locked-down sites where REST is blocked but xmlrpc.php works. Titles + SEO custom fields only."""
    type_name = "wordpress_xmlrpc"
    label = "WordPress (XML-RPC)"
    supports = {"title", "h1", "meta_description", "canonical"}
    fields = [("base_url", "Site URL"), ("username", "WordPress username"), ("secret", "Password")]

    def _proxy(self):
        class UA(xmlrpc.client.Transport):
            user_agent = "Mozilla/5.0 ContentPilotSpider/0.1"
        class UAS(xmlrpc.client.SafeTransport):
            user_agent = "Mozilla/5.0 ContentPilotSpider/0.1"
        url = f"{self.base}/xmlrpc.php"
        return xmlrpc.client.ServerProxy(url, transport=UAS() if url.startswith("https") else UA(), allow_none=True)

    async def test(self) -> PushResult:
        import asyncio
        def go():
            return self._proxy().wp.getUsersBlogs(self.t.get("username", ""), self.t.get("secret", ""))
        try:
            blogs = await asyncio.to_thread(go)
            return PushResult(True, f"Connected: {', '.join(b.get('blogName', '') for b in blogs)}")
        except Exception as exc:  # noqa: BLE001
            return PushResult(False, f"XML-RPC login failed: {str(exc)[:200]}")

    async def apply(self, fix: dict[str, Any]) -> PushResult:
        import asyncio
        user, pw = self.t.get("username", ""), self.t.get("secret", "")
        kind, value = fix["kind"], fix.get("proposed") or ""
        if kind not in self.supports:
            return PushResult(False, f"XML-RPC connector cannot push '{kind}'.")

        def go():
            p = self._proxy()
            target = fix["url"].rstrip("/")
            post = None
            for ptype in ("page", "post"):
                offset = 0
                while offset < 3000:
                    batch = p.wp.getPosts(0, user, pw, {"post_type": ptype, "number": 100, "offset": offset, "post_status": "publish"}, ["post_id", "link", "post_title"])
                    if not batch:
                        break
                    for item in batch:
                        if item.get("link", "").rstrip("/") == target:
                            post = item
                            break
                    if post or len(batch) < 100:
                        break
                    offset += 100
                if post:
                    break
            if not post:
                return PushResult(False, "Could not find this URL among published posts/pages.")
            if kind in ("title", "h1"):
                p.wp.editPost(0, user, pw, post["post_id"], {"post_title": value})
                return PushResult(True, "Post title updated")
            key = "_yoast_wpseo_metadesc" if kind == "meta_description" else "_yoast_wpseo_canonical"
            full = p.wp.getPost(0, user, pw, post["post_id"], ["post_id", "custom_fields"])
            existing = next((f for f in full.get("custom_fields", []) if f.get("key") == key), None)
            cf = {"key": key, "value": value}
            if existing:
                cf["id"] = existing["id"]
            p.wp.editPost(0, user, pw, post["post_id"], {"custom_fields": [cf]})
            return PushResult(True, f"Yoast field {key} updated via XML-RPC")

        try:
            return await asyncio.to_thread(go)
        except Exception as exc:  # noqa: BLE001
            return PushResult(False, f"XML-RPC error: {str(exc)[:200]}")


# ----------------------------------------------------------------------------- Webhook (any custom site)
class Webhook(Connector):
    """POST approved fixes as JSON to your own endpoint (Wix/Shopify/custom CMS: your receiver applies them).
    Body: {"fixes": [{url, kind, field, current, proposed, issue}]}. Header X-CPS-Signature = HMAC-SHA256(secret, body)."""
    type_name = "webhook"
    label = "Webhook (custom site / any CMS)"
    fields = [("base_url", "Endpoint URL that receives the JSON"), ("secret", "Shared secret for the HMAC signature (optional)")]

    async def test(self) -> PushResult:
        body = json.dumps({"ping": True})
        async with httpx.AsyncClient(timeout=20.0) as c:
            try:
                r = await c.post(self.base, content=body, headers=self._headers(body))
            except httpx.HTTPError as exc:
                return PushResult(False, f"Cannot reach endpoint: {exc}")
        return PushResult(r.status_code < 400, f"Endpoint answered {r.status_code}", r.text[:200])

    def _headers(self, body: str) -> dict[str, str]:
        h = {"Content-Type": "application/json", "User-Agent": "ContentPilotSpider/0.1"}
        if self.t.get("secret"):
            h["X-CPS-Signature"] = hmac.new(self.t["secret"].encode(), body.encode(), hashlib.sha256).hexdigest()
        return h

    async def apply(self, fix: dict[str, Any]) -> PushResult:
        payload = {"fixes": [{k: fix.get(k) for k in ("url", "kind", "field", "current", "proposed", "issue_name", "rationale")}]}
        body = json.dumps(payload, ensure_ascii=False)
        async with httpx.AsyncClient(timeout=30.0) as c:
            try:
                r = await c.post(self.base, content=body, headers=self._headers(body))
            except httpx.HTTPError as exc:
                return PushResult(False, f"Cannot reach endpoint: {exc}")
        return PushResult(r.status_code < 400, f"Endpoint answered {r.status_code}", r.text[:300])


CONNECTORS: dict[str, type[Connector]] = {c.type_name: c for c in (WordPressREST, WordPressXMLRPC, Webhook)}


def make_connector(target: dict[str, Any]) -> Connector:
    cls = CONNECTORS.get((target.get("type") or "").lower())
    if cls is None:
        raise ValueError(f"Unknown connector type '{target.get('type')}'. Available: {', '.join(CONNECTORS)}")
    return cls(target)


def connector_catalog() -> list[dict[str, Any]]:
    return [{"type": c.type_name, "label": c.label, "fields": c.fields, "supports": sorted(c.supports), "doc": (c.__doc__ or "").strip()} for c in CONNECTORS.values()]
