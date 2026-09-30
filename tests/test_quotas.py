from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker

from config.settings import settings
from src.storage.models import Base
from src.storage.quota import quota_snapshot, reserve_ai_call, reserve_outbound_send


def make_session():
    engine = create_engine("sqlite:///:memory:")
    Base.metadata.create_all(engine)
    return sessionmaker(bind=engine)()


def test_daily_ai_quota_is_persistent_and_enforced(monkeypatch):
    monkeypatch.setattr(settings, "max_ai_calls_per_day", 2)
    s = make_session()
    reserve_ai_call(s)
    reserve_ai_call(s)
    s.commit()

    assert quota_snapshot(s)["ai_calls_today"] == 2
    assert quota_snapshot(s)["ai_calls_remaining"] == 0
    try:
        reserve_ai_call(s)
        assert False, "quota should be exhausted"
    except RuntimeError as exc:
        assert "AI quota" in str(exc)


def test_outbound_and_application_quotas_are_separate(monkeypatch):
    monkeypatch.setattr(settings, "max_outbound_emails_per_day", 3)
    monkeypatch.setattr(settings, "max_applications_per_day", 2)
    s = make_session()
    reserve_outbound_send(s, email_type="application")
    reserve_outbound_send(s, email_type="application")
    reserve_outbound_send(s, email_type="follow_up")
    s.commit()

    snapshot = quota_snapshot(s)
    assert snapshot["applications_sent_today"] == 2
    assert snapshot["applications_remaining"] == 0
    assert snapshot["outbound_sends_remaining"] == 0


def test_failed_outbound_claim_can_be_retried_but_unknown_cannot(monkeypatch):
    from src.storage.models import OutboundMessage
    from src.storage.outbound import claim_outbound, mark_failed, mark_unknown
    monkeypatch.setattr(settings, "outbound_send_kill_switch", False)
    monkeypatch.setattr(settings, "max_outbound_emails_per_hour", 100)
    monkeypatch.setattr(settings, "min_seconds_between_external_emails", 0)
    s = make_session()
    row = claim_outbound(s, idempotency_key="retry-1", job_id=1, email_type="application", recipient="hr@acme.example", subject="Application", message_id="<1>")
    mark_failed(s, row, "quota")
    row2 = claim_outbound(s, idempotency_key="retry-1", job_id=1, email_type="application", recipient="hr@acme.example", subject="Application", message_id="<2>")
    assert row2.status == "sending"
    mark_unknown(s, row2, "SMTP timeout after DATA")
    try:
        claim_outbound(s, idempotency_key="retry-1", job_id=1, email_type="application", recipient="hr@acme.example", subject="Application", message_id="<3>")
        assert False, "unknown delivery must not be retried automatically"
    except RuntimeError as exc:
        assert "automatic retry blocked" in str(exc)
