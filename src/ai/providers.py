"""Provider implementations behind one small AI abstraction."""
from __future__ import annotations

import logging
from abc import ABC, abstractmethod
from typing import Any

import requests
try:
    import anthropic
except ImportError:  # Claude is optional when Ollama/Gemini are used.
    anthropic = None

logger = logging.getLogger(__name__)


class AIProviderError(RuntimeError):
    def __init__(self, message: str, *, retry_after: int | None = None, transient: bool = False):
        super().__init__(message)
        self.retry_after = retry_after
        self.transient = transient


class RateLimitError(AIProviderError):
    pass


class AIProvider(ABC):
    name: str

    @abstractmethod
    def generate(self, *, system_prompt: str, user_prompt: str, max_tokens: int) -> str:
        raise NotImplementedError


class OllamaProvider(AIProvider):
    name = "ollama"

    def __init__(self, base_url: str, model: str, timeout: int = 90):
        self.base_url = base_url.rstrip("/")
        self.model = model
        self.timeout = timeout

    def generate(self, *, system_prompt: str, user_prompt: str, max_tokens: int) -> str:
        try:
            response = requests.post(
                f"{self.base_url}/api/chat",
                json={
                    "model": self.model,
                    "stream": False,
                    "format": "json",
                    "messages": [
                        {"role": "system", "content": system_prompt},
                        {"role": "user", "content": user_prompt},
                    ],
                    "options": {"num_predict": max_tokens, "temperature": 0.25},
                    "keep_alive": "10m",
                },
                timeout=self.timeout,
            )
        except requests.RequestException as exc:
            raise AIProviderError(f"Ollama request failed: {exc}", transient=True) from exc
        if response.status_code == 429:
            raise RateLimitError("Ollama returned HTTP 429", transient=True)
        if response.status_code >= 400:
            raise AIProviderError(f"Ollama returned HTTP {response.status_code}: {response.text[:300]}", transient=response.status_code >= 500)
        try:
            data = response.json()
            return str(data["message"]["content"])
        except (ValueError, KeyError, TypeError) as exc:
            raise AIProviderError("Ollama returned an invalid response payload") from exc


class GeminiProvider(AIProvider):
    name = "gemini"

    def __init__(self, api_key: str, model: str, timeout: int = 90):
        self.api_key = api_key
        self.model = model
        self.timeout = timeout

    def generate(self, *, system_prompt: str, user_prompt: str, max_tokens: int) -> str:
        if not self.api_key:
            raise AIProviderError("Gemini API key is not configured")
        url = (
            f"https://generativelanguage.googleapis.com/v1beta/models/"
            f"{self.model}:generateContent?key={self.api_key}"
        )
        payload: dict[str, Any] = {
            "systemInstruction": {"parts": [{"text": system_prompt}]},
            "contents": [{"role": "user", "parts": [{"text": user_prompt}]}],
            "generationConfig": {"maxOutputTokens": max_tokens},
        }
        try:
            response = requests.post(url, json=payload, timeout=self.timeout)
        except requests.RequestException as exc:
            raise AIProviderError(f"Gemini request failed: {exc}", transient=True) from exc
        if response.status_code == 429:
            retry_after = response.headers.get("Retry-After")
            raise RateLimitError(
                "Gemini returned HTTP 429",
                retry_after=int(retry_after) if retry_after and retry_after.isdigit() else None,
                transient=True,
            )
        if response.status_code >= 400:
            raise AIProviderError(f"Gemini returned HTTP {response.status_code}: {response.text[:300]}", transient=response.status_code >= 500)
        try:
            data = response.json()
            parts = data["candidates"][0]["content"]["parts"]
            return "\n".join(str(p.get("text", "")) for p in parts if p.get("text"))
        except (ValueError, KeyError, IndexError, TypeError) as exc:
            raise AIProviderError("Gemini returned an invalid response payload") from exc


class ClaudeProvider(AIProvider):
    name = "claude"

    def __init__(self, api_key: str, model: str):
        self.api_key = api_key
        self.model = model

    def generate(self, *, system_prompt: str, user_prompt: str, max_tokens: int) -> str:
        if not self.api_key:
            raise AIProviderError("Claude API key is not configured")
        if anthropic is None:
            raise AIProviderError("Claude provider dependency is not installed")
        try:
            client = anthropic.Anthropic(api_key=self.api_key)
            response = client.messages.create(
                model=self.model,
                max_tokens=max_tokens,
                system=system_prompt,
                messages=[{"role": "user", "content": user_prompt}],
            )
        except anthropic.RateLimitError as exc:
            retry_after = None
            headers = getattr(exc, "response", None)
            if headers is not None:
                raw = getattr(headers, "headers", {}).get("retry-after")
                if raw and str(raw).isdigit():
                    retry_after = int(raw)
            raise RateLimitError("Claude returned a rate-limit error", retry_after=retry_after) from exc
        except anthropic.APIError as exc:
            raise AIProviderError(f"Claude API error: {exc}", transient=True) from exc
        try:
            return "\n".join(b.text for b in response.content if b.type == "text")
        except (AttributeError, TypeError) as exc:
            raise AIProviderError("Claude returned an invalid response payload") from exc
