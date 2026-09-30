"""End-to-end freelance pipeline orchestrator.

Coordinates the full lifecycle: discover leads → deduplicate → evaluate →
generate pitches → send outreach → process follow-ups. Designed to be
called on a schedule alongside the main job pipeline.
"""
from __future__ import annotations

import logging
from datetime import datetime, timezone

from config.settings import settings
from src.freelance.discovery import run_freelance_discovery
from src.freelance.evaluation import evaluate_pending_leads
from src.freelance.outreach import process_follow_ups, send_pitch
from src.freelance.pitch_generator import generate_pitch
from src.storage.db import get_session, init_db
from src.storage.models import FreelanceLead, FreelanceStage

logger = logging.getLogger(__name__)


def _persist_new_leads(raw_leads: list[dict], user_id: int | None = None) -> list[int]:
    """Insert new leads into the database, deduplicating by hash scoped by user.

    Returns the IDs of newly inserted leads.
    """
    new_ids: list[int] = []
    with get_session() as session:
        query = session.query(FreelanceLead.dedup_hash)
        if user_id is not None:
            query = query.filter(FreelanceLead.user_id == user_id)
        existing_hashes = {h for (h,) in query.all()}
        for raw in raw_leads:
            dedup_hash = raw.get("dedup_hash")
            if not dedup_hash or dedup_hash in existing_hashes:
                continue
            existing_hashes.add(dedup_hash)
            lead = FreelanceLead(
                user_id=user_id,
                dedup_hash=dedup_hash,
                title=raw.get("title"),
                client_name=raw.get("client_name"),
                client_type=raw.get("client_type"),
                contact_email=raw.get("contact_email"),
                contact_url=raw.get("contact_url"),
                source_platform=raw.get("source_platform"),
                source_url=raw.get("source_url"),
                raw_description=raw.get("raw_description"),
            )
            session.add(lead)
            session.flush()
            new_ids.append(lead.id)
    return new_ids


def _auto_pitch_eligible(user_id: int | None = None) -> list[int]:
    """Find leads that scored high enough for auto-pitching."""
    if not settings.freelance_auto_pitch:
        return []
    with get_session() as session:
        q = session.query(FreelanceLead).filter(
            FreelanceLead.stage == FreelanceStage.EVALUATED,
            FreelanceLead.is_spam.is_(False),
            FreelanceLead.match_score >= settings.freelance_auto_pitch_min_score,
            FreelanceLead.pitch_status == "not_generated",
        )
        if user_id is not None:
            q = q.filter(FreelanceLead.user_id == user_id)
        leads = q.order_by(FreelanceLead.match_score.desc()).limit(
            settings.freelance_max_pitches_per_day
        ).all()
        return [lead.id for lead in leads]


def run_freelance_pipeline(user_id: int | None = None) -> dict:
    """Execute the full freelance acquisition pipeline.

    Returns a summary dict with counts for each stage.
    """
    if not settings.freelance_enabled:
        logger.info("Freelance engine disabled; skipping pipeline run.")
        return {"status": "disabled"}

    init_db()
    if user_id is None:
        from src.candidate.profile_manager import get_active_profile
        active_p = get_active_profile()
        if active_p and active_p.get("user_id"):
            user_id = active_p.get("user_id")
        else:
            with get_session() as session:
                from sqlalchemy import text
                user_id = session.execute(text("SELECT id FROM users WHERE role = 'admin' ORDER BY id ASC LIMIT 1")).scalar()
                if not user_id:
                    user_id = session.execute(text("SELECT id FROM users ORDER BY id ASC LIMIT 1")).scalar()

    summary = {
        "status": "running",
        "started_at": datetime.now(timezone.utc).isoformat(),
        "discovered": 0,
        "new_leads": 0,
        "evaluated": 0,
        "spam_filtered": 0,
        "pitches_generated": 0,
        "pitches_sent": 0,
        "follow_ups": 0,
        "errors": [],
    }

    # Stage 1: Discovery
    logger.info("=== Freelance Pipeline: Stage 1 — Discovery ===")
    try:
        raw_leads = run_freelance_discovery()
        summary["discovered"] = len(raw_leads)
    except Exception as exc:
        logger.exception("Freelance discovery failed")
        summary["errors"].append(f"Discovery: {exc}")
        raw_leads = []

    # Stage 2: Deduplication & Persistence
    logger.info("=== Freelance Pipeline: Stage 2 — Dedup & Persist ===")
    try:
        new_ids = _persist_new_leads(raw_leads, user_id=user_id)
        summary["new_leads"] = len(new_ids)
        logger.info("Persisted %d new freelance leads (from %d discovered)", len(new_ids), len(raw_leads))
    except Exception as exc:
        logger.exception("Freelance lead persistence failed")
        summary["errors"].append(f"Persistence: {exc}")

    # Stage 3: Evaluation
    logger.info("=== Freelance Pipeline: Stage 3 — AI Evaluation ===")
    try:
        eval_results = evaluate_pending_leads(limit=settings.max_ai_calls_per_run, user_id=user_id)
        summary["evaluated"] = eval_results.get("evaluated", 0)
        summary["spam_filtered"] = eval_results.get("spam", 0)
        if eval_results.get("quota_hit"):
            summary["errors"].append("AI quota reached during evaluation")
    except Exception as exc:
        logger.exception("Freelance evaluation failed")
        summary["errors"].append(f"Evaluation: {exc}")

    # Stage 4: Pitch Generation (for high-scoring leads)
    logger.info("=== Freelance Pipeline: Stage 4 — Pitch Generation ===")
    try:
        eligible_ids = _auto_pitch_eligible(user_id=user_id)
        for lead_id in eligible_ids:
            result = generate_pitch(lead_id)
            if result == "generated":
                summary["pitches_generated"] += 1
    except Exception as exc:
        logger.exception("Freelance pitch generation failed")
        summary["errors"].append(f"Pitch generation: {exc}")

    # Stage 5: Auto-send pitches (if enabled)
    if settings.freelance_auto_pitch and settings.outbound_send_enabled:
        logger.info("=== Freelance Pipeline: Stage 5 — Auto-send Pitches ===")
        try:
            with get_session() as session:
                q = session.query(FreelanceLead).filter(
                    FreelanceLead.pitch_status == "draft",
                    FreelanceLead.contact_email.isnot(None),
                    FreelanceLead.stage == FreelanceStage.EVALUATED,
                    FreelanceLead.is_spam.is_(False),
                    FreelanceLead.match_score >= settings.freelance_auto_pitch_min_score,
                )
                if user_id is not None:
                    q = q.filter(FreelanceLead.user_id == user_id)
                ready = q.limit(settings.freelance_max_pitches_per_day).all()
                send_ids = [lead.id for lead in ready]

            for lead_id in send_ids:
                result = send_pitch(lead_id)
                if result == "sent":
                    summary["pitches_sent"] += 1
        except Exception as exc:
            logger.exception("Freelance auto-send failed")
            summary["errors"].append(f"Auto-send: {exc}")

    # Stage 6: Follow-up processing
    logger.info("=== Freelance Pipeline: Stage 6 — Follow-ups ===")
    try:
        followup_results = process_follow_ups(user_id=user_id)
        summary["follow_ups"] = followup_results.get("follow_ups_sent", 0)
    except Exception as exc:
        logger.exception("Freelance follow-up processing failed")
        summary["errors"].append(f"Follow-ups: {exc}")

    summary["status"] = "completed"
    summary["finished_at"] = datetime.now(timezone.utc).isoformat()
    logger.info("=== Freelance Pipeline Complete: %s ===", summary)
    return summary
