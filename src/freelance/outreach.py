"""Freelance outreach & follow-up scheduler.

Manages the automated communication lifecycle for freelance leads:
send initial pitch, schedule follow-ups, track responses, and advance deal stages.
Respects existing email safety quotas and rate limits.
"""
from __future__ import annotations

import logging
import smtplib
from datetime import datetime, timedelta, timezone
from email.message import EmailMessage
from email.utils import make_msgid
from pathlib import Path

from config.candidate_profile import FREELANCE_PROFILE
from config.settings import settings
from src.freelance.pitch_generator import generate_follow_up, generate_pitch
from src.storage.db import get_session
from src.storage.models import FreelanceLead, FreelanceMessage, FreelanceStage

logger = logging.getLogger(__name__)


def _send_email(*, to: str, subject: str, body: str, lead_id: int, user_id: int | None = None) -> bool:
    """Send an email using the configured SMTP settings.

    Returns True if sent successfully, False otherwise.
    """
    if not settings.outbound_send_enabled:
        logger.info("Outbound sending disabled; pitch for lead %d stored as draft only.", lead_id)
        return False

    user_cfg = {}
    try:
        from src.storage.user_settings import get_user_effective_settings
        user_cfg = get_user_effective_settings(user_id=user_id)
    except Exception:
        pass

    s_email = user_cfg.get("sender_email") or settings.sender_email
    s_name = user_cfg.get("sender_name") or settings.sender_display_name
    s_host = user_cfg.get("smtp_host") or settings.sender_smtp_host
    s_port = user_cfg.get("smtp_port") or settings.sender_smtp_port
    s_pass = user_cfg.get("smtp_password") or settings.sender_smtp_password

    if not all([s_email, s_host, s_pass]):
        logger.warning("SMTP not configured; cannot send pitch for lead %d.", lead_id)
        return False

    try:
        msg = EmailMessage()
        msg["From"] = f"{s_name} <{s_email}>"
        msg["To"] = to
        msg["Subject"] = subject
        msg["Message-ID"] = make_msgid(domain=s_email.split("@")[-1] if "@" in s_email else "localhost")
        msg.set_content(body)

        # Attach resume if available
        from src.candidate.profile_manager import resolve_user_resume_path
        resume_path = resolve_user_resume_path(user_id=user_id, auto_generate=True)
        if resume_path and resume_path.is_file():
            with open(resume_path, "rb") as f:
                filename = f"{s_name.replace(' ', '_')}_Resume.pdf" if not resume_path.name.endswith(".pdf") else resume_path.name
                msg.add_attachment(
                    f.read(),
                    maintype="application",
                    subtype="pdf",
                    filename=filename,
                )

        with smtplib.SMTP(s_host, s_port) as server:
            server.ehlo()
            server.starttls()
            server.login(s_email, s_pass)
            server.send_message(msg)

        logger.info("Freelance pitch sent to %s for lead %d", to, lead_id)
        return True
    except Exception as exc:
        logger.exception("Failed to send freelance pitch for lead %d: %s", lead_id, exc)
        return False


def send_pitch(lead_id: int) -> str:
    """Generate (if needed) and send a pitch for a freelance lead.

    Returns: 'sent', 'draft_only', 'no_email', 'error', 'already_sent'.
    """
    with get_session() as session:
        lead = session.get(FreelanceLead, lead_id)
        if lead is None:
            return "not_found"
        if lead.pitch_status == "sent":
            return "already_sent"

    # Generate pitch if not yet done
    if not _has_pitch(lead_id):
        gen_result = generate_pitch(lead_id)
        if gen_result not in ("generated", "already_done", "already_sent"):
            return f"pitch_gen_{gen_result}"

    with get_session() as session:
        lead = session.get(FreelanceLead, lead_id)
        if lead is None:
            return "not_found"

        if not lead.contact_email:
            logger.info("Lead %d has no contact email; pitch stored as draft.", lead_id)
            return "no_email"

        if not lead.pitch_body:
            return "no_pitch_body"

        sent = _send_email(
            to=lead.contact_email,
            subject=lead.pitch_subject or f"Re: {lead.title}",
            body=lead.pitch_body,
            lead_id=lead_id,
            user_id=lead.user_id,
        )

        if sent:
            lead.pitch_status = "sent"
            lead.stage = FreelanceStage.PITCHED
            lead.last_contact_at = datetime.now(timezone.utc)
            lead.next_follow_up_due = datetime.now(timezone.utc) + timedelta(
                days=settings.freelance_follow_up_1_days
            )
            # Update the message record
            msg = session.query(FreelanceMessage).filter(
                FreelanceMessage.lead_id == lead_id,
                FreelanceMessage.message_type == "pitch",
            ).order_by(FreelanceMessage.created_at.desc()).first()
            if msg:
                msg.sent_at = datetime.now(timezone.utc)
            lead.updated_at = datetime.now(timezone.utc)
            return "sent"
        else:
            lead.pitch_status = "draft"
            lead.stage = FreelanceStage.EVALUATED  # Keep in evaluated, not pitched
            return "draft_only"


def _has_pitch(lead_id: int) -> bool:
    """Check if a lead already has a generated pitch."""
    with get_session() as session:
        lead = session.get(FreelanceLead, lead_id)
        return bool(lead and lead.pitch_body)


def get_leads_needing_follow_up(session=None, user_id: int | None = None) -> list[int]:
    """Retrieve IDs of leads that are currently due for a follow-up."""
    from sqlalchemy import or_
    now = datetime.now(timezone.utc)
    threshold_3d = now - timedelta(days=settings.freelance_follow_up_1_days)

    def _query(s):
        q = s.query(FreelanceLead.id).filter(
            FreelanceLead.stage == FreelanceStage.PITCHED,
            FreelanceLead.follow_up_count < settings.freelance_max_follow_ups,
            or_(
                FreelanceLead.next_follow_up_due <= now,
                (FreelanceLead.next_follow_up_due.is_(None) & (FreelanceLead.last_contact_at <= threshold_3d)),
            ),
        )
        if user_id is not None:
            q = q.filter(FreelanceLead.user_id == user_id)
        due_leads = q.all()
        return [r[0] for r in due_leads]

    if session is not None:
        return _query(session)
    with get_session() as s:
        return _query(s)


def process_follow_ups(user_id: int | None = None) -> dict:
    """Check all pitched leads and draft/send follow-ups where due.

    Returns a summary dict.
    """
    lead_ids = get_leads_needing_follow_up(user_id=user_id)
    results = {"checked": len(lead_ids), "follow_ups_sent": 0, "drafted": 0, "errors": 0}

    for lead_id in lead_ids:
        with get_session() as session:
            lead = session.get(FreelanceLead, lead_id)
            if lead is None:
                continue
            follow_up_num = lead.follow_up_count + 1

        # Generate follow-up draft
        gen_result = generate_follow_up(lead_id, follow_up_number=follow_up_num)
        if gen_result != "generated":
            results["errors"] += 1
            continue

        results["drafted"] += 1

        # Try to send if direct contact email & outbound enabled
        with get_session() as session:
            lead = session.get(FreelanceLead, lead_id)
            if lead is None or not lead.contact_email or not settings.outbound_send_enabled:
                continue

            # Get the latest follow-up message
            msg = session.query(FreelanceMessage).filter(
                FreelanceMessage.lead_id == lead_id,
                FreelanceMessage.message_type == f"follow_up_{follow_up_num}",
            ).order_by(FreelanceMessage.created_at.desc()).first()

            if not msg:
                continue

            sent = _send_email(
                to=lead.contact_email,
                subject=msg.subject or f"Following up — {lead.title}",
                body=msg.body or "",
                lead_id=lead_id,
                user_id=lead.user_id,
            )

            if sent:
                msg.sent_at = datetime.now(timezone.utc)
                lead.last_contact_at = datetime.now(timezone.utc)
                results["follow_ups_sent"] += 1
                session.commit()

    logger.info("Follow-up processing complete: %s", results)
    return results


def advance_stage(lead_id: int, new_stage: str) -> str:
    """Manually advance a lead to a new deal stage.

    Returns: 'updated', 'not_found', 'invalid_stage'.
    """
    try:
        stage = FreelanceStage(new_stage)
    except ValueError:
        return "invalid_stage"

    with get_session() as session:
        lead = session.get(FreelanceLead, lead_id)
        if lead is None:
            return "not_found"
        lead.stage = stage
        lead.updated_at = datetime.now(timezone.utc)
        return "updated"
