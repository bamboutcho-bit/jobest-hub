"""Transactional daily quotas stored in the database."""
from __future__ import annotations

from datetime import datetime, timezone

from sqlalchemy import select, text

from config.settings import settings
from src.storage.models import DailyQuota


def _utc_today() -> str:
    return datetime.now(timezone.utc).date().isoformat()


def get_daily_quota(session, for_update: bool = False) -> DailyQuota:
    today = _utc_today()
    stmt = select(DailyQuota).where(DailyQuota.usage_date == today)
    if for_update:
        stmt = stmt.with_for_update()
    row = session.scalar(stmt)
    if row is not None:
        return row

    session.execute(
        text(
            "INSERT INTO daily_quotas (usage_date, ai_calls, outbound_sends, application_sends, updated_at) "
            "VALUES (:d, 0, 0, 0, :now) ON CONFLICT (usage_date) DO NOTHING"
        ),
        {"d": today, "now": datetime.now(timezone.utc)},
    )
    session.flush()
    row = session.scalar(stmt)
    if row is None:
        raise RuntimeError("Could not initialize daily quota row")
    return row


def reserve_ai_call(session) -> DailyQuota:
    row = get_daily_quota(session, for_update=True)
    if row.ai_calls >= max(0, settings.max_ai_calls_per_day):
        raise RuntimeError("Daily AI quota exhausted")
    row.ai_calls += 1
    row.updated_at = datetime.now(timezone.utc)
    session.commit()
    return row


def reserve_outbound_send(session, *, email_type: str = "other") -> DailyQuota:
    row = get_daily_quota(session, for_update=True)
    if row.outbound_sends >= max(0, settings.max_outbound_emails_per_day):
        raise RuntimeError("Daily outbound email quota exhausted")
    if email_type == "application" and row.application_sends >= max(0, settings.max_applications_per_day):
        raise RuntimeError("Daily application quota exhausted")
    row.outbound_sends += 1
    if email_type == "application":
        row.application_sends += 1
    row.updated_at = datetime.now(timezone.utc)
    session.commit()
    return row


def reserve_application_attempt(session) -> DailyQuota:
    """Reserve an application slot for a public ATS submission."""
    row = get_daily_quota(session, for_update=True)
    if row.application_sends >= max(0, settings.max_applications_per_day):
        raise RuntimeError("Daily application quota exhausted")
    row.application_sends += 1
    row.updated_at = datetime.now(timezone.utc)
    session.commit()
    return row


def quota_snapshot(session) -> dict[str, int | str]:
    row = get_daily_quota(session, for_update=False)
    return {
        "usage_date": row.usage_date,
        "ai_calls_today": int(row.ai_calls),
        "ai_calls_remaining": max(0, int(settings.max_ai_calls_per_day) - int(row.ai_calls)),
        "outbound_sends_today": int(row.outbound_sends),
        "outbound_sends_remaining": max(0, int(settings.max_outbound_emails_per_day) - int(row.outbound_sends)),
        "applications_sent_today": int(row.application_sends),
        "applications_remaining": max(0, int(settings.max_applications_per_day) - int(row.application_sends)),
    }
