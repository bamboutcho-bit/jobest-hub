import pytest

from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker

from config.settings import settings
from src.ai.service import AIService, AIProviderUnavailable
from src.ai.providers import AIProviderError
from src.storage.models import Base, AIProviderState
from src.storage.quota import quota_snapshot


class FakeProvider:
    def __init__(self, name, fail=False):
        self.name = name
        self.fail = fail
        self.calls = 0

    def generate(self, **kwargs):
        self.calls += 1
        if self.fail:
            raise AIProviderError("boom")
        return '{"ok": true}'


def test_ollama_failure_records_cooldown(monkeypatch):
    engine = create_engine("sqlite:///:memory:")
    Base.metadata.create_all(engine)
    Session = sessionmaker(bind=engine)
    session = Session()

    monkeypatch.setattr(settings, "ai_provider", "ollama")
    monkeypatch.setattr(settings, "ai_fallback_providers", "")
    monkeypatch.setattr(settings, "max_ai_calls_per_day", 10)

    service = AIService(session=session)
    service.providers["ollama"] = FakeProvider("ollama", fail=True)
    service._is_configured = lambda name: True

    with pytest.raises(AIProviderUnavailable):
        service.generate(system_prompt="s", user_prompt="u", max_tokens=10)
    state = session.query(AIProviderState).filter_by(provider="ollama").one()
    assert state.failure_count == 1
    assert state.cooldown_until is not None

def test_transient_ollama_failure_retries_before_fallback(monkeypatch):
    engine = create_engine("sqlite:///:memory:")
    Base.metadata.create_all(engine)
    Session = sessionmaker(bind=engine)
    session = Session()

    monkeypatch.setattr(settings, "ai_provider", "ollama")
    monkeypatch.setattr(settings, "ai_fallback_providers", "gemini")
    monkeypatch.setattr(settings, "max_ai_calls_per_day", 10)
    monkeypatch.setattr(settings, "ai_transient_retry_count", 1)
    monkeypatch.setattr(settings, "ai_transient_retry_delay_seconds", 0)

    class FlakyProvider(FakeProvider):
        def generate(self, **kwargs):
            self.calls += 1
            if self.calls == 1:
                raise AIProviderError("temporary", transient=True)
            return '{"ok": true}'

    service = AIService(session=session)
    service.providers["ollama"] = FlakyProvider("ollama")
    service.providers["gemini"] = FakeProvider("gemini", fail=False)
    service._is_configured = lambda name: True

    assert service.generate(system_prompt="s", user_prompt="u", max_tokens=10) == '{"ok": true}'
    assert service.providers["ollama"].calls == 2
    assert service.providers["gemini"].calls == 0
    assert quota_snapshot(session)["ai_calls_today"] == 2
