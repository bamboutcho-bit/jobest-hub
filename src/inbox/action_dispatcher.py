"""Human-safe action dispatcher with outbound message threading and follow-up caps."""
import logging
import smtplib
from datetime import datetime, timedelta, timezone
from email.mime.text import MIMEText
from email.utils import make_msgid

from sqlalchemy import select

from config.settings import settings
from src.negotiation.offer_assistant import draft_counter_offer
from src.scheduling.ics_scheduler import build_reply_mime, draft_scheduling_reply
from src.storage.db import get_session
from src.storage.quota import reserve_outbound_send
from src.storage.outbound import claim_outbound, mark_sent, mark_unknown, mark_failed
from src.outreach.safety import validate_message
from src.storage.models import EmailEvent, JobPosting, PipelineStage

logger = logging.getLogger(__name__)


def _extract_body_text(msg, default_body: str = "") -> str:
    if default_body:
        return default_body
    if hasattr(msg, "is_multipart") and msg.is_multipart():
        parts = []
        for part in msg.walk():
            if part.get_content_type() == "text/plain":
                payload = part.get_payload(decode=True)
                if payload:
                    charset = part.get_content_charset() or "utf-8"
                    parts.append(payload.decode(charset, errors="replace"))
        if parts:
            return "\n".join(parts)
    payload = msg.get_payload(decode=True) if hasattr(msg, "get_payload") else None
    if payload:
        charset = msg.get_content_charset() or "utf-8"
        return payload.decode(charset, errors="replace")
    raw = msg.get_payload() if hasattr(msg, "get_payload") else ""
    return raw if isinstance(raw, str) else ""


def _send_mime(msg, to_addr: str, session, *, email_type: str, job_id: int, idempotency_key: str, body_text: str = "") -> bool:
    if not settings.outbound_send_enabled:
        logger.warning("Outbound sending globally disabled; message kept as draft.")
        return False
    if not all([settings.sender_email, settings.sender_smtp_host, settings.sender_smtp_password]):
        logger.warning("Sender identity not configured — cannot send.")
        return False

    body = _extract_body_text(msg, body_text)
    safety = validate_message(recipient=to_addr, subject=msg.get("Subject", ""), body=body)
    if not safety.allowed:
        logger.warning("Outbound email blocked by safety policy: %s", safety.reason)
        return False
    try:
        ledger = claim_outbound(
            session, idempotency_key=idempotency_key, job_id=job_id, email_type=email_type,
            recipient=to_addr, subject=msg.get("Subject", ""), message_id=msg.get("Message-ID", ""),
        )
        try:
            reserve_outbound_send(session, email_type=email_type)
        except RuntimeError as exc:
            mark_failed(session, ledger, str(exc))
            raise
    except RuntimeError as exc:
        logger.warning("Outbound send safety/quota blocked: %s", exc)
        return False
    try:
        with smtplib.SMTP(settings.sender_smtp_host, settings.sender_smtp_port, timeout=20) as server:
            server.starttls()
            server.login(settings.sender_email, settings.sender_smtp_password)
            server.sendmail(settings.sender_email, [to_addr], msg.as_string())
        mark_sent(session, ledger)
        return True
    except Exception as exc:
        mark_unknown(session, ledger, str(exc))
        logger.error("Outbound delivery state unknown for %s: %s", to_addr, exc)
        return False


def _latest_inbound(job: JobPosting) -> EmailEvent | None:
    events = [e for e in job.email_events if e.direction == "inbound"]
    return max(events, key=lambda e: e.created_at) if events else None


def _record_outbound(session, job: JobPosting, msg, body: str, email_type: str) -> None:
    session.add(EmailEvent(
        job_id=job.id,
        direction="outbound",
        message_id=msg.get("Message-ID"),
        in_reply_to=msg.get("In-Reply-To"),
        references=msg.get("References"),
        sender_email=settings.sender_email,
        recipient_email=job.application_email,
        subject=msg.get("Subject"),
        body=body,
        email_type=email_type,
    ))


def _handle_interview_requests(session) -> int:
    jobs = session.scalars(select(JobPosting).where(JobPosting.pipeline_stage == PipelineStage.INTERVIEW_REQUESTED)).all()
    handled = 0
    for job in jobs:
        body, ics_bytes = draft_scheduling_reply(job.title, job.company, [])
        latest = _latest_inbound(job)
        parent_id = latest.message_id if latest else None
        refs = " ".join(x for x in [latest.references if latest else "", parent_id or ""] if x)

        if settings.auto_reply_mode == "send" and job.application_email:
            msg = build_reply_mime(job.application_email, job.thread_subject or job.title, body, ics_bytes, parent_id, refs)
            if _send_mime(msg, job.application_email, session, email_type="reply", job_id=job.id, idempotency_key=f"interview_reply:{job.id}:{parent_id or 'none'}", body_text=body):
                _record_outbound(session, job, msg, body, "reply")
                job.pipeline_stage = PipelineStage.INTERVIEW_SCHEDULED
                job.last_contact_at = datetime.now(timezone.utc)
                handled += 1
                logger.info("Sent scheduling reply for job %s, preserving thread headers.", job.id)
                try:
                    from src.alerting.telegram_bot import send_telegram_interview_alert
                    send_telegram_interview_alert({
                        "company": job.company,
                        "title": job.title,
                        "summary": f"Interview scheduled with {job.company}! ICS calendar invitation & reply dispatched.",
                        "url": "http://localhost:8080/app#inbox",
                    })
                    from src.notifications.publisher import publish_notification
                    publish_notification(
                        user_id=job.user_id,
                        event_type="interview_scheduled",
                        title=f"🗓️ Interview Confirmed: {job.company}",
                        message=f"Calendar invitation sent for {job.title} at {job.company}.",
                        link="#inbox",
                        data={"job_id": job.id, "company": job.company, "title": job.title, "stage": "interview_scheduled"}
                    )
                except Exception as notif_err:
                    logger.debug("Interview scheduled notification error: %s", notif_err)
            else:
                if "[DRAFT REPLY - review before sending]" not in (job.interview_notes or ""):
                    job.interview_notes = (job.interview_notes or "") + f"\n\n[DRAFT REPLY - review before sending]\n{body}"
                job.pipeline_stage = PipelineStage.RESPONSE_RECEIVED
                handled += 1
        else:
            if "[DRAFT REPLY - review before sending]" not in (job.interview_notes or ""):
                job.interview_notes = (job.interview_notes or "") + f"\n\n[DRAFT REPLY - review before sending]\n{body}"
            job.pipeline_stage = PipelineStage.RESPONSE_RECEIVED
            handled += 1
    return handled


def _handle_offers(session) -> int:
    jobs = session.scalars(select(JobPosting).where(JobPosting.pipeline_stage == PipelineStage.OFFER_RECEIVED)).all()
    handled = 0
    for job in jobs:
        result = draft_counter_offer(job.title, job.company, job.offer_details or "")
        job.offer_counter_draft = result.get("counter_offer_draft", "")
        job.pipeline_stage = PipelineStage.OFFER_NEGOTIATING
        handled += 1
        logger.info("Offer received for job %s (%s @ %s) — human-review draft ready.", job.id, job.title, job.company)
    return handled


def _handle_stale_follow_ups(session) -> int:
    cutoff = datetime.now(timezone.utc) - timedelta(days=settings.follow_up_after_days)
    jobs = session.scalars(select(JobPosting).where(
        JobPosting.pipeline_stage == PipelineStage.APPLIED,
        JobPosting.applied_at.isnot(None),
        JobPosting.applied_at < cutoff,
        JobPosting.follow_up_count < settings.max_follow_ups,
    )).all()

    handled = 0
    for job in jobs:
        follow_up_body = (
            f"Hi, I wanted to follow up on my application for the {job.title} role at {job.company} "
            f"sent on {job.applied_at.strftime('%d %B') if job.applied_at else 'earlier'}. "
            f"I remain very interested and happy to provide any further information.\n\n"
            f"Best regards,\n{settings.sender_display_name}"
        )
        if settings.auto_reply_mode == "send" and job.application_email:
            latest = _latest_inbound(job)
            parent_id = latest.message_id if latest else None
            msg = MIMEText(follow_up_body, "plain", "utf-8")
            msg["Message-ID"] = make_msgid()
            msg["Subject"] = f"Re: {job.thread_subject or job.title}"
            msg["From"] = f"{settings.sender_display_name} <{settings.sender_email}>"
            msg["To"] = job.application_email
            if parent_id:
                msg["In-Reply-To"] = parent_id
                msg["References"] = " ".join(x for x in [latest.references if latest else "", parent_id] if x)
            if _send_mime(msg, job.application_email, session, email_type="follow_up", job_id=job.id, idempotency_key=f"follow_up:{job.id}:{job.follow_up_count + 1}", body_text=follow_up_body):
                _record_outbound(session, job, msg, follow_up_body, "follow_up")
                job.last_contact_at = datetime.now(timezone.utc)
                job.follow_up_count += 1
                handled += 1
            else:
                if job.follow_up_count < settings.max_follow_ups:
                    job.interview_notes = (job.interview_notes or "") + f"\n\n[FOLLOW-UP DRAFT {job.follow_up_count + 1} - review before sending]\n{follow_up_body}"
                    job.follow_up_count += 1
                    handled += 1
        else:
            if job.follow_up_count < settings.max_follow_ups:
                job.interview_notes = (job.interview_notes or "") + f"\n\n[FOLLOW-UP DRAFT {job.follow_up_count + 1} - review before sending]\n{follow_up_body}"
                job.follow_up_count += 1
                handled += 1
    return handled


def run_dispatch_cycle() -> dict:
    with get_session() as session:
        summary = {
            "interview_replies": _handle_interview_requests(session),
            "offer_drafts": _handle_offers(session),
            "follow_ups": _handle_stale_follow_ups(session),
        }
    logger.info("Action dispatch summary: %s", summary)
    return summary
