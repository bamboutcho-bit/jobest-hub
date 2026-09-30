import os

def test_compose_uses_service_database_host():
    path = "docker-compose.microservices.yml" if os.path.exists("docker-compose.microservices.yml") else "docker-compose.yml"
    text = open(path, encoding="utf-8").read()
    assert "@postgres:5432/" in text
    assert "read_only: true" in text

def test_outbound_defaults_disabled():
    text = open("config/settings.py", encoding="utf-8").read()
    assert 'outbound_send_enabled: bool = False' in text

def test_dashboard_fails_closed():
    text = open("src/dashboard/app.py", encoding="utf-8").read()
    assert 'Dashboard authentication is not configured' in text


def test_outbound_kill_switch_defaults_off_and_is_enforced(monkeypatch):
    from src.outreach.safety import validate_message
    monkeypatch.setattr("config.settings.settings.outbound_send_kill_switch", True)
    from src.storage.outbound import claim_outbound
    from sqlalchemy import create_engine
    from sqlalchemy.orm import sessionmaker
    from src.storage.models import Base
    engine = create_engine("sqlite:///:memory:")
    Base.metadata.create_all(engine)
    s = sessionmaker(bind=engine)()
    try:
        claim_outbound(s, idempotency_key="k1", job_id=1, email_type="application", recipient="hr@acme.example", subject="Application", message_id="<m1>")
        assert False, "kill switch should block outbound claim"
    except RuntimeError as exc:
        assert "kill switch" in str(exc).lower()


def test_safety_rejects_no_reply_personal_and_shortened_urls():
    from src.outreach.safety import validate_message
    assert not validate_message(recipient="noreply@acme.com", subject="Application", body="A" * 150).allowed
    assert not validate_message(recipient="person@gmail.com", subject="Application", body="A" * 150).allowed
    assert not validate_message(recipient="hr@acme.com", subject="Application", body="A" * 110 + " https://bit.ly/example").allowed
