"""Durable outbound email safety ledger and suppression controls."""
from __future__ import annotations

from datetime import datetime, timedelta, timezone

from sqlalchemy import func, or_, select
from sqlalchemy.exc import IntegrityError

from config.settings import settings
from src.outreach.safety import normalize_email
from src.storage.models import JobPosting, OutboundMessage, OutboundSuppression


def _now() -> datetime:
    return datetime.now(timezone.utc)


def _domain(address: str) -> str:
    return normalize_email(address).rsplit("@", 1)[-1]


def _as_aware(value: datetime) -> datetime:
    if value.tzinfo is None:
        return value.replace(tzinfo=timezone.utc)
    return value.astimezone(timezone.utc)


def is_suppressed(session, address: str, user_id: int | None = None) -> bool:
    address = normalize_email(address)
    domain = _domain(address)
    query = select(OutboundSuppression.id).where(
        (OutboundSuppression.recipient_email == address) |
        ((OutboundSuppression.recipient_email.is_(None)) & (OutboundSuppression.domain == domain))
    )
    if user_id is not None:
        query = query.where(or_(OutboundSuppression.user_id == user_id, OutboundSuppression.user_id.is_(None)))
    return bool(session.scalar(query))


import logging

logger = logging.getLogger(__name__)


def suppress_recipient(session, *, address: str | None = None, domain: str | None = None,
                       reason: str, details: str | None = None, user_id: int | None = None) -> None:
    address = normalize_email(address) or None
    domain = (domain or (_domain(address) if address else "")).strip().lower() or None
    if not address and not domain:
        return

    # Check if address or domain is already suppressed across the system or for this user
    if address:
        query = select(OutboundSuppression).where(OutboundSuppression.recipient_email == address)
    else:
        query = select(OutboundSuppression).where(
            (OutboundSuppression.recipient_email.is_(None)) & (OutboundSuppression.domain == domain)
        )
        if user_id is not None:
            query = query.where(or_(OutboundSuppression.user_id == user_id, OutboundSuppression.user_id.is_(None)))

    existing = session.scalar(query)
    if existing:
        existing.reason = reason
        existing.details = details
        if user_id is not None and existing.user_id is None:
            existing.user_id = user_id
        return

    try:
        session.add(OutboundSuppression(
            user_id=user_id, recipient_email=address, domain=domain, reason=reason, details=details, created_at=_now()
        ))
        session.flush()
    except (IntegrityError, Exception) as exc:
        logger.debug("Suppression insert skipped or updated: %s", exc)
        session.rollback()
        if address:
            existing = session.scalar(select(OutboundSuppression).where(OutboundSuppression.recipient_email == address))
            if existing:
                existing.reason = reason
                existing.details = details


def _check_velocity(session, recipient: str) -> None:
    now = _now()
    recent_cutoff = now - timedelta(hours=1)
    recent_count = session.scalar(select(func.count(OutboundMessage.id)).where(
        OutboundMessage.created_at >= recent_cutoff,
        OutboundMessage.status.in_(["pending", "sending", "sent", "unknown"]),
    )) or 0
    if recent_count >= max(0, settings.max_outbound_emails_per_hour):
        raise RuntimeError("Hourly outbound email safety limit reached")

    latest = session.scalar(select(OutboundMessage.created_at).where(
        OutboundMessage.recipient_email == recipient,
        OutboundMessage.status.in_(["pending", "sending", "sent", "unknown"]),
    ).order_by(OutboundMessage.created_at.desc()).limit(1))
    if latest and (now - _as_aware(latest)).total_seconds() < max(0, settings.min_seconds_between_external_emails):
        raise RuntimeError("Minimum delay between external email sends has not elapsed")


def claim_outbound(session, *, idempotency_key: str, job_id: int | None, email_type: str,
                   recipient: str, subject: str, message_id: str, user_id: int | None = None) -> OutboundMessage:
    recipient = normalize_email(recipient)
    if user_id is None and job_id is not None:
        user_id = session.scalar(select(JobPosting.user_id).where(JobPosting.id == job_id))

    if settings.outbound_send_kill_switch:
        raise RuntimeError("Global outbound email kill switch is enabled")
    if is_suppressed(session, recipient, user_id=user_id):
        raise RuntimeError("Recipient is on the outbound suppression list")
    _check_velocity(session, recipient)

    if email_type == "application":
        dedup_query = select(OutboundMessage.id).where(
            OutboundMessage.recipient_email == recipient,
            OutboundMessage.email_type == "application",
            OutboundMessage.status.in_(["pending", "sending", "sent", "unknown"]),
            OutboundMessage.idempotency_key != idempotency_key
        )
        if user_id is not None:
            dedup_query = dedup_query.where(or_(OutboundMessage.user_id == user_id, OutboundMessage.user_id.is_(None)))
        has_previous = session.scalar(dedup_query.limit(1))
        if has_previous:
            raise RuntimeError(f"Global deduplication: {recipient} has already been emailed previously.")

    existing = session.scalar(select(OutboundMessage).where(OutboundMessage.idempotency_key == idempotency_key))
    if existing:
        if existing.status in {"sent", "sending", "unknown"}:
            raise RuntimeError(f"Outbound message already {existing.status}; automatic retry blocked")
        if existing.status in {"pending", "failed"}:
            existing.status = "sending"
            existing.updated_at = _now()
            existing.message_id = message_id
            existing.subject = subject
            existing.failure_reason = None
            if user_id is not None and existing.user_id is None:
                existing.user_id = user_id
            session.flush()
            return existing

    row = OutboundMessage(
        user_id=user_id,
        idempotency_key=idempotency_key,
        job_id=job_id,
        email_type=email_type,
        recipient_email=recipient,
        subject=subject,
        message_id=message_id,
        status="sending",
        created_at=_now(),
        updated_at=_now(),
    )
    try:
        with session.begin_nested():
            session.add(row)
            session.flush()
    except IntegrityError:
        existing = session.scalar(select(OutboundMessage).where(OutboundMessage.idempotency_key == idempotency_key))
        raise RuntimeError(f"Outbound message already claimed with status={existing.status if existing else 'unknown'}")
    return row


def mark_sent(session, row: OutboundMessage) -> None:
    row.status = "sent"
    row.sent_at = _now()
    row.updated_at = _now()
    session.flush()


def mark_unknown(session, row: OutboundMessage, reason: str) -> None:
    row.status = "unknown"
    row.failure_reason = reason[:1000]
    row.updated_at = _now()
    session.flush()


def mark_failed(session, row: OutboundMessage, reason: str) -> None:
    row.status = "failed"
    row.failure_reason = reason[:1000]
    row.updated_at = _now()
    session.flush()
