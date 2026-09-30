"""Central AI service: Multi-provider (Ollama, Gemini, Claude) with persistent cooldowns and quota."""
from __future__ import annotations

import logging
import time
from datetime import datetime, timedelta, timezone
from typing import Any, Callable

from sqlalchemy import select

from config.settings import settings
from src.ai.providers import AIProviderError, ClaudeProvider, GeminiProvider, OllamaProvider
from src.storage.db import get_session
from src.storage.models import AIProviderState
from src.storage.quota import reserve_ai_call

logger = logging.getLogger(__name__)


class AIQuotaExceeded(RuntimeError):
    pass


class AIProviderUnavailable(RuntimeError):
    pass


class AIService:
    """Multi-provider AI orchestrator with cooldowns, retries, and quota management."""

    def __init__(self, *, session=None):
        self.session = session
        self.providers: dict[str, Any] = {
            "ollama": OllamaProvider(
                settings.ollama_base_url,
                settings.ollama_model,
                settings.ai_request_timeout_seconds,
            ),
            "gemini": GeminiProvider(
                settings.gemini_api_key,
                settings.gemini_model,
                settings.ai_request_timeout_seconds,
            ),
        }
        if getattr(settings, "claude_api_key", None):
            self.providers["claude"] = ClaudeProvider(
                settings.claude_api_key,
                settings.claude_model,
            )

    @staticmethod
    def _provider_order() -> list[str]:
        primary = (settings.ai_provider or "ollama").strip().lower()
        fallbacks = [p.strip().lower() for p in settings.ai_fallback_providers.split(",") if p.strip()]
        order: list[str] = []
        for p in [primary] + fallbacks:
            if p and p not in order:
                order.append(p)
        return order

    @staticmethod
    def _is_configured(provider_name: str) -> bool:
        if provider_name == "ollama":
            return bool(settings.ollama_base_url and settings.ollama_model)
        if provider_name == "gemini":
            return bool(settings.gemini_api_key)
        if provider_name == "claude":
            return bool(settings.claude_api_key)
        if provider_name == "heuristic":
            return True
        return False

    @staticmethod
    def _cooldown_seconds(failure_count: int) -> int:
        base = max(1, settings.ai_cooldown_base_seconds)
        return min(settings.ai_cooldown_max_seconds, base * (2 ** max(0, failure_count - 1)))

    def _state(self, session, provider_name: str) -> AIProviderState:
        state = session.scalar(
            select(AIProviderState).where(AIProviderState.provider == provider_name).with_for_update()
        )
        if state is None:
            state = AIProviderState(provider=provider_name, failure_count=0)
            session.add(state)
            session.flush()
        return state

    def _success(self, session, state: AIProviderState) -> None:
        state.failure_count = 0
        state.cooldown_until = None
        state.last_error = None
        state.updated_at = datetime.now(timezone.utc)
        session.flush()

    def _failure(self, session, state: AIProviderState, exc: Exception) -> None:
        state.failure_count += 1
        retry_after = getattr(exc, "retry_after", None)
        delay = retry_after if retry_after is not None else self._cooldown_seconds(state.failure_count)
        state.cooldown_until = datetime.now(timezone.utc) + timedelta(seconds=max(1, int(delay)))
        state.last_error = str(exc)[:1000]
        state.updated_at = datetime.now(timezone.utc)
        session.flush()

    def _generate_in_session(self, session, *, system_prompt: str, user_prompt: str,
                             max_tokens: int, parse: Callable[[str], object] | None):
        errors: list[str] = []
        order = self._provider_order()

        for provider_name in order:
            if provider_name == "heuristic":
                continue  # Handled by caller if all AI providers fail

            if not self._is_configured(provider_name):
                continue

            if provider_name not in self.providers:
                continue

            state = self._state(session, provider_name)
            now = datetime.now(timezone.utc)
            cooldown_until = state.cooldown_until
            if cooldown_until and cooldown_until.tzinfo is None:
                cooldown_until = cooldown_until.replace(tzinfo=timezone.utc)
            if cooldown_until and cooldown_until > now:
                errors.append(f"{provider_name} cooling down until {cooldown_until.isoformat()}")
                continue

            attempts = 0
            max_retries = max(0, int(settings.ai_transient_retry_count))
            provider_success = False

            while attempts <= max_retries:
                attempts += 1
                try:
                    reserve_ai_call(session)
                except RuntimeError as exc:
                    raise AIQuotaExceeded(str(exc)) from exc

                try:
                    text = self.providers[provider_name].generate(
                        system_prompt=system_prompt,
                        user_prompt=user_prompt,
                        max_tokens=max_tokens,
                    )
                    result = parse(text) if parse else text
                    self._success(session, state)
                    return result
                except AIProviderError as exc:
                    errors.append(f"{provider_name}: {exc}")
                    transient = bool(getattr(exc, "transient", False))
                    if transient and attempts <= max_retries:
                        logger.warning("Transient %s failure; retry %s/%s", provider_name, attempts, max_retries)
                        time.sleep(max(0, settings.ai_transient_retry_delay_seconds))
                        continue
                    self._failure(session, state, exc)
                    break
                except Exception as exc:
                    self._failure(session, state, exc)
                    errors.append(f"{provider_name} unusable output: {exc}")
                    break

        raise AIProviderUnavailable(f"All configured AI providers failed: {'; '.join(errors) if errors else 'none configured'}")

    def generate(self, *, system_prompt: str, user_prompt: str, max_tokens: int, parse=None):
        if self.session is not None:
            return self._generate_in_session(
                self.session, system_prompt=system_prompt, user_prompt=user_prompt,
                max_tokens=max_tokens, parse=parse,
            )
        with get_session() as session:
            return self._generate_in_session(
                session, system_prompt=system_prompt, user_prompt=user_prompt,
                max_tokens=max_tokens, parse=parse,
            )
