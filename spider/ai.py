"""AI providers for fix suggestions. The user brings their own key: Anthropic (official SDK), OpenAI, Gemini,
Ollama or any OpenAI-compatible endpoint. Every provider exposes one call: complete(system, user) -> text."""
from __future__ import annotations

import json
import re
from typing import Any

import httpx

from .config import CrawlConfig

DEFAULT_MODELS = {
    "anthropic": "claude-opus-5-5",
    "openai": "gpt-4.1",
    "gemini": "gemini-2.5-flash",
    "ollama": "llama3.1",
    "custom": "",
}
PROVIDER_NAMES = {"anthropic": "Anthropic (Claude)", "openai": "OpenAI", "gemini": "Google Gemini", "ollama": "Ollama (local)", "custom": "Custom OpenAI-compatible endpoint"}


class AIError(Exception):
    pass


class Provider:
    def __init__(self, cfg: CrawlConfig):
        self.cfg = cfg
        self.provider = (cfg.ai_provider or "anthropic").lower()
        self.model = cfg.ai_model or DEFAULT_MODELS.get(self.provider, "")
        self.key = (cfg.ai_api_key or "").strip()
        self.base_url = (cfg.ai_base_url or "").strip().rstrip("/")
        if self.provider == "ollama" and not self.base_url:
            self.base_url = "http://localhost:11434/v1"
        if self.provider in ("anthropic", "openai", "gemini") and not self.key:
            raise AIError(f"No API key set for {PROVIDER_NAMES.get(self.provider, self.provider)}. Add it in Config > AI.")
        if self.provider == "custom" and not self.base_url:
            raise AIError("Custom provider needs a base URL (OpenAI-compatible, e.g. https://api.groq.com/openai/v1).")
        if not self.model:
            raise AIError("Set a model name in Config > AI.")
        # usage accounting (read by spider/usage.py after a job)
        self.requests = 0
        self.input_tokens = 0
        self.output_tokens = 0

    def _count(self, input_tokens: Any, output_tokens: Any) -> None:
        self.requests += 1
        try:
            self.input_tokens += int(input_tokens or 0)
            self.output_tokens += int(output_tokens or 0)
        except (TypeError, ValueError):
            pass

    @property
    def label(self) -> str:
        return f"{PROVIDER_NAMES.get(self.provider, self.provider)} · {self.model}"

    async def complete(self, system: str, user: str, max_tokens: int = 4000) -> str:
        if self.provider == "anthropic":
            return await self._anthropic(system, user, max_tokens)
        if self.provider == "gemini":
            return await self._gemini(system, user, max_tokens)
        return await self._openai_compatible(system, user, max_tokens)

    # --- Anthropic (official SDK) ------------------------------------------------
    async def _anthropic(self, system: str, user: str, max_tokens: int) -> str:
        import anthropic
        client = anthropic.AsyncAnthropic(api_key=self.key, max_retries=3, timeout=120.0)
        try:
            kwargs: dict[str, Any] = dict(model=self.model, max_tokens=max_tokens, system=system,
                                          messages=[{"role": "user", "content": user}],
                                          output_config={"effort": "low"})
            if self.model.startswith(("claude-opus-5", "claude-sonnet-5-5", "claude-fable-5")):
                # server-side refusal fallback: the API re-runs a declined request on a fallback model
                resp = await client.beta.messages.create(betas=["server-side-fallback-2026-07-01"], fallbacks="default", **kwargs)
            else:
                kwargs.pop("output_config", None)
                resp = await client.messages.create(**kwargs)
        except anthropic.AuthenticationError:
            raise AIError("Anthropic rejected the API key (401). Check Config > AI.")
        except anthropic.RateLimitError:
            raise AIError("Anthropic rate limit hit (429). Lower AI concurrency in Config > AI and retry.")
        except anthropic.BadRequestError as exc:
            raise AIError(f"Anthropic bad request: {exc.message}")
        except anthropic.APIStatusError as exc:
            raise AIError(f"Anthropic API error {exc.status_code}: {exc.message}")
        except anthropic.APIConnectionError as exc:
            raise AIError(f"Could not reach the Anthropic API: {exc}")
        finally:
            await client.close()
        u = getattr(resp, "usage", None)
        self._count((getattr(u, "input_tokens", 0) or 0) + (getattr(u, "cache_read_input_tokens", 0) or 0) + (getattr(u, "cache_creation_input_tokens", 0) or 0),
                    getattr(u, "output_tokens", 0))
        if resp.stop_reason == "refusal":
            cat = getattr(getattr(resp, "stop_details", None), "category", None)
            raise AIError(f"The model declined this request{f' ({cat})' if cat else ''}.")
        return "".join(b.text for b in resp.content if b.type == "text")

    # --- OpenAI / Ollama / custom (chat completions) -----------------------------
    async def _openai_compatible(self, system: str, user: str, max_tokens: int) -> str:
        base = self.base_url or "https://api.openai.com/v1"
        headers = {"Content-Type": "application/json"}
        if self.key:
            headers["Authorization"] = f"Bearer {self.key}"
        body = {"model": self.model, "messages": [{"role": "system", "content": system}, {"role": "user", "content": user}],
                "max_tokens": max_tokens, "temperature": 0.3}
        async with httpx.AsyncClient(timeout=180.0) as client:
            try:
                r = await client.post(f"{base}/chat/completions", headers=headers, json=body)
            except httpx.HTTPError as exc:
                raise AIError(f"Could not reach {base}: {exc}")
        if r.status_code == 401:
            raise AIError("The provider rejected the API key (401). Check Config > AI.")
        if r.status_code == 429:
            raise AIError("Provider rate limit hit (429). Lower AI concurrency and retry.")
        if r.status_code >= 400:
            raise AIError(f"Provider error {r.status_code}: {r.text[:300]}")
        data = r.json()
        u = data.get("usage") or {}
        self._count(u.get("prompt_tokens"), u.get("completion_tokens"))
        try:
            return data["choices"][0]["message"]["content"] or ""
        except (KeyError, IndexError, TypeError):
            raise AIError(f"Unexpected response from provider: {str(data)[:300]}")

    # --- Gemini ------------------------------------------------------------------
    async def _gemini(self, system: str, user: str, max_tokens: int) -> str:
        base = self.base_url or "https://generativelanguage.googleapis.com/v1beta"
        url = f"{base}/models/{self.model}:generateContent"
        body = {"systemInstruction": {"parts": [{"text": system}]}, "contents": [{"role": "user", "parts": [{"text": user}]}],
                "generationConfig": {"maxOutputTokens": max_tokens, "temperature": 0.3}}
        async with httpx.AsyncClient(timeout=180.0) as client:
            try:
                r = await client.post(url, headers={"x-goog-api-key": self.key, "Content-Type": "application/json"}, json=body)
            except httpx.HTTPError as exc:
                raise AIError(f"Could not reach Gemini: {exc}")
        if r.status_code in (401, 403):
            raise AIError("Gemini rejected the API key. Check Config > AI.")
        if r.status_code == 429:
            raise AIError("Gemini rate limit hit (429). Lower AI concurrency and retry.")
        if r.status_code >= 400:
            raise AIError(f"Gemini error {r.status_code}: {r.text[:300]}")
        data = r.json()
        u = data.get("usageMetadata") or {}
        self._count(u.get("promptTokenCount"), u.get("candidatesTokenCount"))
        try:
            return "".join(p.get("text", "") for p in data["candidates"][0]["content"]["parts"])
        except (KeyError, IndexError, TypeError):
            raise AIError(f"Unexpected response from Gemini: {str(data)[:300]}")


async def list_models(cfg: CrawlConfig) -> list[str]:
    """Model ids the configured provider offers right now (its own model-list endpoint). Raises AIError when the
    provider cannot be asked (no key, unreachable, endpoint unsupported)."""
    provider = (cfg.ai_provider or "anthropic").lower()
    key = (cfg.ai_api_key or "").strip()
    base = (cfg.ai_base_url or "").strip().rstrip("/")
    if provider in ("anthropic", "openai", "gemini") and not key:
        raise AIError("Enter the API key first.")
    async with httpx.AsyncClient(timeout=20.0) as client:
        try:
            if provider == "anthropic":
                out, after = [], None
                for _ in range(10):
                    params: dict[str, Any] = {"limit": 100}
                    if after:
                        params["after_id"] = after
                    r = await client.get("https://api.anthropic.com/v1/models", params=params,
                                         headers={"x-api-key": key, "anthropic-version": "2023-06-01"})
                    _raise_for(r, "Anthropic")
                    data = r.json()
                    out += [m["id"] for m in data.get("data", [])]
                    if not data.get("has_more") or not data.get("last_id"):
                        break
                    after = data["last_id"]
                return out
            if provider == "gemini":
                b = base or "https://generativelanguage.googleapis.com/v1beta"
                out, token = [], None
                for _ in range(10):
                    params = {"pageSize": 1000}
                    if token:
                        params["pageToken"] = token
                    r = await client.get(f"{b}/models", params=params, headers={"x-goog-api-key": key})
                    _raise_for(r, "Gemini")
                    data = r.json()
                    out += [m["name"].split("/", 1)[-1] for m in data.get("models", []) if "generateContent" in m.get("supportedGenerationMethods", ["generateContent"])]
                    token = data.get("nextPageToken")
                    if not token:
                        break
                return out
            if provider == "ollama":
                root = (base or "http://localhost:11434/v1")
                root = root[:-3] if root.endswith("/v1") else root
                r = await client.get(f"{root}/api/tags")
                _raise_for(r, "Ollama")
                return [m["name"] for m in r.json().get("models", [])]
            # OpenAI and OpenAI-compatible endpoints
            b = base or "https://api.openai.com/v1"
            headers = {"Authorization": f"Bearer {key}"} if key else {}
            r = await client.get(f"{b}/models", headers=headers)
            _raise_for(r, PROVIDER_NAMES.get(provider, provider))
            data = r.json()
            rows = data.get("data", data) if isinstance(data, dict) else data
            return [m["id"] if isinstance(m, dict) else str(m) for m in rows]
        except httpx.HTTPError as exc:
            raise AIError(f"Could not reach the provider to list models: {exc}")
        except (KeyError, TypeError, ValueError) as exc:
            raise AIError(f"Unexpected model-list response: {exc}")


def _raise_for(r: httpx.Response, name: str) -> None:
    if r.status_code in (401, 403):
        raise AIError(f"{name} rejected the API key ({r.status_code}).")
    if r.status_code == 404:
        raise AIError(f"{name} has no model-list endpoint at this URL (404).")
    if r.status_code >= 400:
        raise AIError(f"{name} error {r.status_code} while listing models: {r.text[:200]}")


def parse_json_block(text: str) -> Any:
    """Models wrap JSON in prose or code fences; pull out the first JSON array/object."""
    text = text.strip()
    m = re.search(r"```(?:json)?\s*(.*?)```", text, re.S)
    if m:
        text = m.group(1).strip()
    try:
        return json.loads(text)
    except json.JSONDecodeError:
        pass
    start = min([i for i in (text.find("["), text.find("{")) if i >= 0] or [-1])
    if start < 0:
        raise AIError(f"Model did not return JSON: {text[:200]}")
    depth, end = 0, None
    opener = text[start]
    closer = "]" if opener == "[" else "}"
    for i, ch in enumerate(text[start:], start):
        if ch == opener:
            depth += 1
        elif ch == closer:
            depth -= 1
            if depth == 0:
                end = i + 1
                break
    try:
        return json.loads(text[start:end])
    except json.JSONDecodeError as exc:
        raise AIError(f"Could not parse model JSON: {exc}")
