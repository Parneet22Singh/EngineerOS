"""Provider-agnostic AI layer.

Modules call ``ai.complete(...)`` without caring which backend is configured. A
``none`` provider is always available so the platform runs with zero AI credentials —
features that use AI (e.g. narrated report summaries) degrade gracefully instead of
failing. Anthropic / OpenAI / Ollama are wired lazily so their SDKs are optional.
"""
from __future__ import annotations

import logging
from typing import Protocol

import httpx

from app.config import Settings

logger = logging.getLogger("engineeros.ai")


class AIProvider(Protocol):
    name: str
    available: bool

    async def complete(self, prompt: str, *, system: str | None = ..., max_tokens: int = ...) -> str: ...


class NullProvider:
    """Always-available no-op provider. Returns empty text."""

    name = "none"
    available = False

    async def complete(self, prompt: str, *, system: str | None = None, max_tokens: int = 1024) -> str:
        return ""


class AnthropicProvider:
    name = "anthropic"

    def __init__(self, api_key: str, model: str) -> None:
        self._api_key = api_key
        self._model = model
        self.available = bool(api_key)

    async def complete(self, prompt: str, *, system: str | None = None, max_tokens: int = 1024) -> str:
        if not self.available:
            return ""
        body: dict = {
            "model": self._model,
            "max_tokens": max_tokens,
            "messages": [{"role": "user", "content": prompt}],
        }
        if system:
            body["system"] = system
        async with httpx.AsyncClient(timeout=60) as client:
            resp = await client.post(
                "https://api.anthropic.com/v1/messages",
                headers={
                    "x-api-key": self._api_key,
                    "anthropic-version": "2023-06-01",
                    "content-type": "application/json",
                },
                json=body,
            )
            resp.raise_for_status()
            data = resp.json()
            return "".join(block.get("text", "") for block in data.get("content", []))


class OpenAIProvider:
    """OpenAI-compatible chat provider.

    Works against the OpenAI cloud API or any server that speaks the same protocol —
    including a local ``llama.cpp`` / LM Studio server — by pointing ``base_url`` at it.
    """

    name = "openai"

    def __init__(self, api_key: str, model: str, base_url: str = "https://api.openai.com/v1") -> None:
        self._api_key = api_key
        self._model = model
        self._base_url = base_url.rstrip("/")
        self._is_local = "localhost" in base_url or "127.0.0.1" in base_url
        # A local server needs no key; the cloud API does.
        self.available = bool(api_key) or self._is_local

    async def complete(self, prompt: str, *, system: str | None = None, max_tokens: int = 1024) -> str:
        if not self.available:
            return ""
        messages = []
        if system:
            messages.append({"role": "system", "content": system})
        messages.append({"role": "user", "content": prompt})
        # Local CPU inference is slow; give it a generous timeout.
        timeout = 600 if self._is_local else 60
        try:
            async with httpx.AsyncClient(timeout=timeout) as client:
                resp = await client.post(
                    f"{self._base_url}/chat/completions",
                    headers={"authorization": f"Bearer {self._api_key or 'sk-local'}"},
                    json={"model": self._model, "messages": messages, "max_tokens": max_tokens},
                )
                resp.raise_for_status()
                data = resp.json()
                return data["choices"][0]["message"]["content"]
        except Exception as exc:  # noqa: BLE001
            logger.warning("openai-compatible completion failed: %r", exc)
            return ""


class OllamaProvider:
    name = "ollama"

    def __init__(self, base_url: str, model: str) -> None:
        self._base_url = base_url.rstrip("/")
        self._model = model
        self.available = True  # assume a local server; complete() fails soft

    async def complete(self, prompt: str, *, system: str | None = None, max_tokens: int = 1024) -> str:
        try:
            async with httpx.AsyncClient(timeout=120) as client:
                resp = await client.post(
                    f"{self._base_url}/api/generate",
                    json={
                        "model": self._model,
                        "prompt": prompt,
                        "system": system or "",
                        "stream": False,
                        "options": {"num_predict": max_tokens},
                    },
                )
                resp.raise_for_status()
                return resp.json().get("response", "")
        except Exception as exc:  # noqa: BLE001
            logger.warning("ollama completion failed: %r", exc)
            return ""


def build_provider(settings: Settings) -> AIProvider:
    provider = settings.ai_provider.lower()
    if provider == "anthropic":
        return AnthropicProvider(settings.anthropic_api_key, settings.ai_model)
    if provider == "openai":
        return OpenAIProvider(settings.openai_api_key, settings.ai_model, settings.openai_base_url)
    if provider == "ollama":
        return OllamaProvider(settings.ollama_base_url, settings.ai_model)
    return NullProvider()
