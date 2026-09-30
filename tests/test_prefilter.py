from src.evaluation.prefilter import visa_relocation_prefilter


def test_prefilter_skips_obvious_non_sponsorship_role(monkeypatch):
    monkeypatch.setattr("config.settings.settings.claude_prefilter_enabled", True)
    job = {"title": "Senior React Engineer", "company": "Acme", "location": "Paris", "raw_description": "Build React dashboards and APIs.", "is_remote": False}
    should_call, signals = visa_relocation_prefilter(job)
    assert should_call is False
    assert signals == []


def test_prefilter_allows_relocation_signal(monkeypatch):
    monkeypatch.setattr("config.settings.settings.claude_prefilter_enabled", True)
    job = {"title": "Backend Engineer", "company": "Acme", "location": "Berlin", "raw_description": "We provide relocation support for international candidates.", "is_remote": False}
    should_call, signals = visa_relocation_prefilter(job)
    assert should_call is True
    assert "relocation support" in signals
