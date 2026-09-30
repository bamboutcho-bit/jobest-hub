"""Authenticated live admin/control dashboard for the job-search system."""
from __future__ import annotations

import html
import json
import logging
import os
from pathlib import Path
import secrets
import threading
import csv
import io
import base64
from datetime import datetime, timedelta, timezone

from typing import Any, Dict, List, Optional

from fastapi import Depends, FastAPI, HTTPException, Query, status, Request, Response, WebSocket, WebSocketDisconnect
from fastapi.responses import HTMLResponse, JSONResponse, StreamingResponse, FileResponse
from fastapi.security import HTTPBasic, HTTPBasicCredentials
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel
from sqlalchemy import and_, case, desc, func, or_, select

from config.settings import settings
from src.dashboard.log_capture import system_log_buffer
from src.notifications.hub import notification_hub
from src.notifications.consumer import start_notification_consumer
from src.notifications.publisher import publish_notification
from src.storage.db import get_session, init_db
from src.storage.models import (
    AIProviderState,
    CandidateProfile,
    EmailEvent,
    FreelanceLead,
    FreelanceMessage,
    FreelanceStage,
    InboxMessage,
    JobPosting,
    Notification,
    OutboundMessage,
    OutboundSuppression,
    PipelineRun,
    PipelineRunEvent,
    PipelineStage,
    SupportMessage,
    User,
)
from src.storage.quota import quota_snapshot

logger = logging.getLogger("autohunt.dashboard")

app = FastAPI(title="Job Search Automation Admin", version="3.0")
app.mount("/static", StaticFiles(directory=Path(__file__).parent / "static"), name="static")
security = HTTPBasic()

try:
    from slowapi import Limiter, _rate_limit_exceeded_handler
    from slowapi.util import get_remote_address
    from slowapi.errors import RateLimitExceeded
    limiter = Limiter(key_func=get_remote_address)
    app.state.limiter = limiter
    app.add_exception_handler(RateLimitExceeded, _rate_limit_exceeded_handler)
    RATE_LIMIT_ENABLED = True
except ImportError:
    import logging
    logging.warning("slowapi is not installed. Rate limiting will be disabled.")
    RATE_LIMIT_ENABLED = False
    
    # Dummy limiter for decorator syntax compatibility
    class DummyLimiter:
        def limit(self, limit_string):
            def decorator(func):
                return func
            return decorator
    limiter = DummyLimiter()


def _auth(request: Request):
    from src.auth.service import get_current_user_optional
    user = get_current_user_optional(request)
    if user:
        return True
    if not (settings.dashboard_username and settings.dashboard_password):
        raise HTTPException(status_code=500, detail="Dashboard authentication is not configured")
    auth_header = request.headers.get("Authorization", "")
    if auth_header.startswith("Basic "):
        try:
            raw = base64.b64decode(auth_header[6:].strip()).decode("utf-8")
            u, p = raw.split(":", 1)
            if (
                secrets.compare_digest(u, settings.dashboard_username)
                and secrets.compare_digest(p, settings.dashboard_password)
            ):
                return True
        except Exception:
            pass
    raise HTTPException(status_code=401, detail="Unauthorized", headers={"WWW-Authenticate": "Bearer"})


def _aware(value: datetime | None) -> str | None:
    if not value:
        return None
    if value.tzinfo is None:
        value = value.replace(tzinfo=timezone.utc)
    return value.astimezone(timezone.utc).isoformat()


def _aware_dt(value: datetime | None) -> datetime | None:
    if not value:
        return None
    if value.tzinfo is None:
        return value.replace(tzinfo=timezone.utc)
    return value.astimezone(timezone.utc)


def _stage_counts(session, user_id: int | None = None) -> dict[str, int]:
    stmt = select(JobPosting.pipeline_stage, func.count())
    if user_id is not None:
        stmt = stmt.where(JobPosting.user_id == user_id)
    rows = session.execute(stmt.group_by(JobPosting.pipeline_stage)).all()
    return {stage.value if hasattr(stage, "value") else str(stage): int(count) for stage, count in rows}


def _job_dict(job: JobPosting) -> dict:
    return {
        "id": job.id,
        "title": job.title,
        "company": job.company,
        "location": job.location,
        "source_site": job.source_site,
        "is_remote": job.is_remote,
        "score": job.match_score,
        "ai_relevance_score": job.ai_relevance_score,
        "visa": job.visa_sponsorship_detected,
        "visa_status_notes": job.visa_status_notes,
        "skills": json.loads(job.key_skills_matched) if job.key_skills_matched else [],
        "stage": job.pipeline_stage.value,
        "evaluation_status": job.evaluation_status,
        "evaluation_method": getattr(job, "evaluation_method", None) or ("heuristic" if job.evaluated else None),
        "evaluation_reason": job.evaluation_reason,
        "evaluated": job.evaluated,
        "evaluation_attempted_at": _aware(job.evaluation_attempted_at),
        "application_email": job.application_email,
        "application_emails": job.application_emails,
        "application_url": job.application_url,
        "company_url": job.company_url,
        "application_status": job.application_status,
        "application_method": job.application_method,
        "application_error": job.application_error,
        "application_screenshot_path": job.application_screenshot_path,
        "has_screenshot": bool(
            (job.application_screenshot_path and Path(job.application_screenshot_path).is_file())
            or list(Path(settings.apply_screenshot_dir).glob(f"{job.id}_*.png"))
        ),
        "applied_at": _aware(job.applied_at or job.application_attempted_at or (job.last_contact_at if job.pipeline_stage.value == 'applied' else None)),
        "application_attempted_at": _aware(job.application_attempted_at),
        "last_contact_at": _aware(job.last_contact_at),
        "follow_up_count": job.follow_up_count,
        "job_url": job.job_url,
        "date_posted": job.date_posted,
        "scraped_at": _aware(job.scraped_at),
        "outreach_generated": job.outreach_generated,
        "alerted": job.alerted,
        "pitch_en": job.recruiter_pitch_en,
        "pitch_fr": job.recruiter_pitch_fr,
        "application_subject": job.application_subject,
        "application_email_body": job.application_email_body,
        "offer_details": job.offer_details,
        "offer_counter_draft": job.offer_counter_draft,
        "interview_notes": job.interview_notes,
        "description": job.raw_description,
        "continent": getattr(job, "continent", None) or "Europe",
        "experience_level": getattr(job, "experience_level", None) or "entry",
        "experience_years_required": getattr(job, "experience_years_required", None),
        "degree_required": getattr(job, "degree_required", None) or "none",
        "degree_matched": getattr(job, "degree_matched", None),
        "visa_category": getattr(job, "visa_category", None) or "unspecified",
        "relocation_detected": bool(getattr(job, "relocation_detected", False)),
    }


class StageUpdate(BaseModel):
    stage: str


class SuppressionCreate(BaseModel):
    email: str | None = None
    domain: str | None = None
    reason: str = "manual"
    details: str | None = None


class JobResponse(BaseModel):
    id: int
    title: str
    company: str | None = None
    location: str | None = None
    source_site: str | None = None
    is_remote: bool | None = False
    score: int | None = None
    ai_relevance_score: int | None = None
    visa: bool | None = None
    visa_status_notes: str | None = None
    skills: list[str] = []
    stage: str
    evaluation_status: str | None = None
    evaluation_method: str | None = None
    evaluation_reason: str | None = None
    evaluated: bool | None = False
    evaluation_attempted_at: str | None = None
    application_email: str | None = None
    application_emails: str | None = None
    application_url: str | None = None
    company_url: str | None = None
    application_status: str | None = None
    application_method: str | None = None
    application_error: str | None = None
    application_screenshot_path: str | None = None
    has_screenshot: bool | None = False
    thread_subject: str | None = None
    applied_at: str | None = None
    last_contact_at: str | None = None
    follow_up_count: int | None = 0
    job_url: str | None = None
    date_posted: str | None = None
    scraped_at: str | None = None
    outreach_generated: bool | None = False
    alerted: bool | None = False
    pitch_en: str | None = None
    pitch_fr: str | None = None
    application_subject: str | None = None
    application_email_body: str | None = None
    offer_details: str | None = None
    offer_counter_draft: str | None = None
    interview_notes: str | None = None
    description: str | None = None
    continent: str | None = "Europe"
    experience_level: str | None = "entry"
    experience_years_required: int | None = None
    degree_required: str | None = "none"
    degree_matched: bool | None = None
    visa_category: str | None = "unspecified"
    relocation_detected: bool | None = False


@app.on_event("startup")
def startup():
    if settings.startup_require_dashboard_password and not settings.dashboard_password and settings.app_environment.lower() == "production":
        raise RuntimeError("DASHBOARD_PASSWORD must be configured in production")
    init_db()
    root = logging.getLogger()
    root.setLevel(logging.INFO)
    logger.setLevel(logging.INFO)
    logging.getLogger("autohunt").setLevel(logging.INFO)
    if system_log_buffer not in root.handlers:
        root.addHandler(system_log_buffer)
    u_acc = logging.getLogger("uvicorn.access")
    if system_log_buffer not in u_acc.handlers:
        u_acc.addHandler(system_log_buffer)

    # Initialize Real-time Notification System (WebSocket Hub + Message Broker Consumer)
    try:
        import asyncio
        loop = asyncio.get_event_loop()
        notification_hub.set_loop(loop)
    except Exception as loop_err:
        logger.debug("Failed to set event loop on notification_hub: %s", loop_err)

    try:
        start_notification_consumer()
    except Exception as cons_err:
        logger.warning("Could not launch notification consumer: %s", cons_err)

    logger.info("AutoHunt Pro dashboard startup completed. Operational log stream active.")


@app.get("/health")
def health():
    try:
        with get_session() as session:
            session.execute(select(func.count(JobPosting.id))).scalar_one()
        return {"status": "ok", "utc": datetime.now(timezone.utc).isoformat()}
    except Exception as exc:
        return JSONResponse(status_code=503, content={"status": "error", "error": str(exc)})


@app.get("/api/summary")
@limiter.limit("120/minute")
def api_summary(request: Request, _: bool = Depends(_auth)):
    from src.auth.service import get_current_user_optional
    from src.storage.models import User, UserJobApplication
    user = get_current_user_optional(request)
    user_id = user.id if user else None
    is_admin = bool(user and user.role == "admin")

    with get_session() as session:
        now = datetime.now(timezone.utc)
        day_start = now.replace(hour=0, minute=0, second=0, microsecond=0)
        hour_start = now - timedelta(hours=1)

        db_user = session.get(User, user_id) if user_id else None
        if not is_admin and db_user:
            is_admin = bool(db_user.role == "admin")

        run_query = select(PipelineRun)
        if user_id is not None:
            run_query = run_query.where(PipelineRun.user_id == user_id)
        latest_run = session.scalar(run_query.order_by(desc(PipelineRun.started_at)).limit(1))

        counts = _stage_counts(session, user_id=user_id)

        def _scope_q(stmt):
            if user_id is not None:
                return stmt.where(JobPosting.user_id == user_id)
            return stmt

        jobs_total = session.scalar(_scope_q(select(func.count(JobPosting.id)))) or 0
        jobs_evaluated = session.scalar(_scope_q(select(func.count(JobPosting.id)).where(JobPosting.evaluated.is_(True)))) or 0
        jobs_pending = session.scalar(_scope_q(select(func.count(JobPosting.id)).where(JobPosting.evaluated.is_(False), JobPosting.evaluation_status.in_(["pending", "queued", "budget_skipped"])))) or 0
        good_matches = session.scalar(_scope_q(select(func.count(JobPosting.id)).where(JobPosting.pipeline_stage == PipelineStage.EVALUATED_MATCH))) or 0

        # Scope replies and follow-ups to this specific user's jobs
        replies_q = select(func.count(EmailEvent.id)).where(EmailEvent.direction == "inbound", EmailEvent.created_at >= day_start)
        followups_q = select(func.count(EmailEvent.id)).where(EmailEvent.direction == "outbound", EmailEvent.email_type == "follow_up", EmailEvent.created_at >= day_start)
        if user_id is not None:
            replies_q = replies_q.join(JobPosting, EmailEvent.job_id == JobPosting.id).where(JobPosting.user_id == user_id)
            followups_q = followups_q.join(JobPosting, EmailEvent.job_id == JobPosting.id).where(JobPosting.user_id == user_id)
        replies_today = session.scalar(replies_q) or 0
        followups_today = session.scalar(followups_q) or 0

        applications_today = session.scalar(_scope_q(select(func.count(JobPosting.id)).where(JobPosting.applied_at >= day_start))) or 0
        application_failures_today = session.scalar(_scope_q(select(func.count(JobPosting.id)).where(JobPosting.application_attempted_at >= day_start, JobPosting.application_status.in_(["error", "blocked", "application_error"])))) or 0
        application_manual_today = session.scalar(_scope_q(select(func.count(JobPosting.id)).where(JobPosting.application_attempted_at >= day_start, JobPosting.application_status.like("manual%")))) or 0
        if not is_admin and user_id is not None:
            outbound_hour = session.scalar(
                select(func.count(OutboundMessage.id))
                .outerjoin(JobPosting, OutboundMessage.job_id == JobPosting.id)
                .where(
                    OutboundMessage.created_at >= hour_start,
                    OutboundMessage.status.in_(["pending", "sending", "sent", "unknown"]),
                    or_(OutboundMessage.user_id == user_id, JobPosting.user_id == user_id)
                )
            ) or 0
            outbound_statuses = {str(k): int(v) for k, v in session.execute(
                select(OutboundMessage.status, func.count())
                .outerjoin(JobPosting, OutboundMessage.job_id == JobPosting.id)
                .where(or_(OutboundMessage.user_id == user_id, JobPosting.user_id == user_id))
                .group_by(OutboundMessage.status)
            ).all()}
            inbox_statuses = {str(k): int(v) for k, v in session.execute(
                select(InboxMessage.status, func.count())
                .outerjoin(JobPosting, InboxMessage.job_id == JobPosting.id)
                .where(or_(InboxMessage.user_id == user_id, JobPosting.user_id == user_id))
                .group_by(InboxMessage.status)
            ).all()}
            suppressions = session.scalar(
                select(func.count(OutboundSuppression.id)).where(OutboundSuppression.user_id == user_id)
            ) or 0
        else:
            outbound_hour = session.scalar(select(func.count(OutboundMessage.id)).where(OutboundMessage.created_at >= hour_start, OutboundMessage.status.in_(["pending", "sending", "sent", "unknown"]))) or 0
            outbound_statuses = {str(k): int(v) for k, v in session.execute(select(OutboundMessage.status, func.count()).group_by(OutboundMessage.status)).all()}
            inbox_statuses = {str(k): int(v) for k, v in session.execute(select(InboxMessage.status, func.count()).group_by(InboxMessage.status)).all()}
            suppressions = session.scalar(select(func.count(OutboundSuppression.id))) or 0

        # User-specific application limits and quotas
        user_limit = db_user.daily_apply_limit if db_user and db_user.daily_apply_limit is not None else (200 if is_admin else 5)
        user_sent_today = 0
        if user_id is not None:
            user_sent_today = session.scalar(
                select(func.count(UserJobApplication.id)).where(
                    UserJobApplication.user_id == user_id,
                    UserJobApplication.applied_at >= day_start,
                )
            ) or 0
        else:
            user_sent_today = applications_today
        user_remaining = max(0, user_limit - user_sent_today)

        if is_admin:
            quota = quota_snapshot(session)
            quota["applications_sent_today"] = user_sent_today
            quota["applications_remaining"] = user_remaining
            quota["daily_limit"] = user_limit
            providers = []
            for p in session.scalars(select(AIProviderState).order_by(AIProviderState.provider)).all():
                cooldown_active = bool(p.cooldown_until and _aware_dt(p.cooldown_until) > now)
                providers.append({
                    "provider": p.provider,
                    "status": "cooldown" if cooldown_active else "ready",
                    "cooldown_until": _aware(p.cooldown_until),
                    "failure_count": p.failure_count,
                    "last_error": p.last_error,
                })
        else:
            # Independent user quota: do not expose server-wide AI Calls or provider telemetry
            quota = {
                "usage_date": now.date().isoformat(),
                "ai_calls_today": None,
                "ai_calls_remaining": None,
                "applications_sent_today": user_sent_today,
                "applications_remaining": user_remaining,
                "daily_limit": user_limit,
                "is_user_quota": True,
            }
            providers = []

        progress = {}
        if latest_run and latest_run.summary_json:
            try:
                progress = json.loads(latest_run.summary_json)
            except Exception:
                progress = {}

        # Fetch active profile for candidate context
        from src.candidate.profile_manager import get_active_profile
        active_prof = get_active_profile(user_id=user_id, session=session)
        active_profile_info = None
        if active_prof:
            active_profile_info = {
                "id": active_prof.get("id"),
                "name": active_prof.get("name"),
                "headline": active_prof.get("headline"),
                "target_titles": active_prof.get("target_titles") or [],
                "core_stack": active_prof.get("core_stack") or [],
                "keywords": active_prof.get("keywords") or [],
                "target_locations": active_prof.get("target_locations") or [],
            }

        # Fetch recent events and duration for the latest run
        recent_events = []
        duration_seconds = None
        if latest_run:
            evs = session.scalars(
                select(PipelineRunEvent)
                .where(PipelineRunEvent.run_id == latest_run.id)
                .order_by(desc(PipelineRunEvent.created_at))
                .limit(40)
            ).all()
            for ev in reversed(evs):
                d_obj = None
                if ev.details_json:
                    try:
                        d_obj = json.loads(ev.details_json)
                    except Exception:
                        d_obj = None
                recent_events.append({
                    "id": ev.id,
                    "phase": ev.phase,
                    "status": ev.status,
                    "message": ev.message,
                    "details": d_obj,
                    "created_at": _aware(ev.created_at),
                })
            if latest_run.finished_at and latest_run.started_at:
                duration_seconds = int((_aware_dt(latest_run.finished_at) - _aware_dt(latest_run.started_at)).total_seconds())
            elif latest_run.started_at:
                duration_seconds = int((now - _aware_dt(latest_run.started_at)).total_seconds())

        # Fetch recent runs history (last 5 runs)
        run_hist_query = select(PipelineRun)
        if user_id is not None:
            run_hist_query = run_hist_query.where(PipelineRun.user_id == user_id)
        recent_runs_objs = session.scalars(run_hist_query.order_by(desc(PipelineRun.started_at)).limit(5)).all()
        recent_runs_list = [{
            "id": r.id,
            "status": r.status,
            "phase": r.phase or ("complete" if r.status == "success" else "running"),
            "started_at": _aware(r.started_at),
            "finished_at": _aware(r.finished_at),
            "duration_seconds": int((_aware_dt(r.finished_at) - _aware_dt(r.started_at)).total_seconds()) if (r.finished_at and r.started_at) else None,
            "raw_scraped": r.raw_scraped or r.raw_scraped_live or 0,
            "new_postings": r.new_postings or r.new_postings_live or 0,
            "evaluated": r.evaluated or r.evaluated_live or 0,
            "applications_sent": r.applications_sent or 0,
            "scrape_errors": r.scrape_errors or 0,
            "error": r.error,
        } for r in recent_runs_objs]

        return {
            "server_utc": now.isoformat(),
            "configured_timezone": settings.candidate_timezone,
            "is_admin": is_admin,
            "user_quota": {
                "daily_limit": user_limit,
                "applications_sent_today": user_sent_today,
                "applications_remaining": user_remaining,
            },
            "jobs": {"total": jobs_total, "evaluated": jobs_evaluated, "pending": jobs_pending, "good_matches": good_matches},
            "stages": counts,
            "action_count": sum(counts.get(x, 0) for x in ("applied", "interview_requested", "offer_negotiating", "response_received", "offer_received")),
            "replies_today": int(replies_today),
            "follow_ups_today": int(followups_today),
            "applications_today": int(user_sent_today if not is_admin else applications_today),
            "application_failures_today": int(application_failures_today),
            "application_manual_today": int(application_manual_today),
            "outbound_hour": int(outbound_hour),
            "outbound_statuses": outbound_statuses,
            "inbox_statuses": inbox_statuses,
            "suppression_count": int(suppressions),
            "quota": quota,
            "outbound_send_enabled": bool(settings.outbound_send_enabled and not settings.outbound_send_kill_switch),
            "outbound_send_configured": bool(settings.outbound_send_enabled),
            "outbound_kill_switch": bool(settings.outbound_send_kill_switch),
            "modes": {
                "auto_apply": settings.auto_apply_mode,
                "auto_reply": settings.auto_reply_mode,
                "auto_negotiate": settings.auto_negotiate_mode,
            },
            "limits": {
                "ai_daily": settings.max_ai_calls_per_day if is_admin else None,
                "applications_daily": user_limit,
                "outbound_daily": settings.max_outbound_emails_per_day,
                "outbound_hourly": settings.max_outbound_emails_per_hour,
                "min_seconds_between_external": settings.min_seconds_between_external_emails,
            },
            "providers": providers,
            "latest_run": None if not latest_run else {
                "id": latest_run.id,
                "status": latest_run.status,
                "phase": latest_run.phase or ("complete" if latest_run.status == "success" else "idle"),
                "progress_pct": latest_run.progress_pct if latest_run.progress_pct is not None else (100 if latest_run.status == "success" else 0),
                "current_task": latest_run.current_task or ("Pipeline execution complete." if latest_run.status == "success" else "Idle"),
                "started_at": _aware(latest_run.started_at),
                "finished_at": _aware(latest_run.finished_at),
                "duration_seconds": duration_seconds,
                "total_queries": latest_run.total_queries or 0,
                "completed_queries": latest_run.completed_queries or 0,
                "raw_scraped": latest_run.raw_scraped or latest_run.raw_scraped_live or 0,
                "new_postings": latest_run.new_postings or latest_run.new_postings_live or 0,
                "evaluated": latest_run.evaluated or latest_run.evaluated_live or 0,
                "applications_sent": latest_run.applications_sent or 0,
                "application_failures": latest_run.application_failures or 0,
                "application_manual": latest_run.application_manual or 0,
                "ai_calls": latest_run.claude_calls if is_admin else None,
                "prefilter_skipped": latest_run.claude_skipped_prefilter if is_admin else None,
                "budget_skipped": latest_run.claude_skipped_budget if is_admin else None,
                "scrape_errors": latest_run.scrape_errors or 0,
                "evaluation_failures": latest_run.evaluation_failures or 0,
                "error": latest_run.error,
                "progress": progress,
                "events": recent_events,
            },
            "active_profile": active_profile_info,
            "recent_runs": recent_runs_list,
        }



@app.get("/api/jobs", response_model=list[JobResponse])
@limiter.limit("60/minute")
def api_jobs(
    request: Request,
    q: str | None = None,
    stage: str | None = None,
    continent: str | None = None,
    experience: str | None = None,
    degree: str | None = None,
    visa: str | None = None,
    min_score: int | None = None,
    sort_by: str = "latest",
    limit: int = 100,
    _: bool = Depends(_auth)
):
    limit = max(1, min(limit, 500))
    from src.auth.service import get_current_user_optional
    user = get_current_user_optional(request)
    user_id = user.id if user else None

    with get_session() as session:
        query = select(JobPosting)
        if user_id is not None:
            query = query.where(JobPosting.user_id == user_id)
        if stage:
            if stage in ("applied", "applied_all"):
                query = query.where(
                    or_(
                        JobPosting.pipeline_stage == PipelineStage.APPLIED,
                        and_(
                            JobPosting.applied_at.isnot(None),
                            JobPosting.pipeline_stage.in_([
                                PipelineStage.RESPONSE_RECEIVED,
                                PipelineStage.INTERVIEW_REQUESTED,
                                PipelineStage.INTERVIEW_SCHEDULED,
                                PipelineStage.OFFER_RECEIVED,
                                PipelineStage.OFFER_NEGOTIATING,
                                PipelineStage.ACCEPTED,
                                PipelineStage.REJECTED,
                            ])
                        )
                    )
                )
            elif stage == "applied_only":
                query = query.where(JobPosting.pipeline_stage == PipelineStage.APPLIED)
            else:
                try:
                    query = query.where(JobPosting.pipeline_stage == PipelineStage(stage))
                except ValueError:
                    raise HTTPException(400, "Invalid stage")
        if continent and continent != "all":
            query = query.where(JobPosting.continent == continent)
        if experience and experience != "all":
            query = query.where(JobPosting.experience_level == experience)
        if degree and degree != "all":
            query = query.where(JobPosting.degree_required == degree)
        if visa and visa != "all":
            query = query.where(JobPosting.visa_category == visa)
        if min_score is not None and min_score > 0:
            query = query.where(JobPosting.match_score >= min_score)
        if q:
            needle = f"%{q.strip()}%"
            query = query.where(or_(JobPosting.title.ilike(needle), JobPosting.company.ilike(needle), JobPosting.location.ilike(needle), JobPosting.application_email.ilike(needle), JobPosting.application_emails.ilike(needle)))

        # "Keep the last thing up" — default order is latest scraped/discovered jobs first
        if sort_by == "score":
            order_clause = [desc(JobPosting.match_score), desc(JobPosting.scraped_at), desc(JobPosting.id)]
        else:
            order_clause = [desc(JobPosting.scraped_at), desc(JobPosting.id)]

        jobs = session.scalars(query.order_by(*order_clause).limit(limit)).all()
        return [_job_dict(j) for j in jobs]


@app.post("/api/jobs/re_evaluate_batch")
def re_evaluate_batch_jobs(force_all: bool = True, _: bool = Depends(_auth)):
    """Batch re-evaluate stored jobs using heuristic precision scorer to update continent, experience, degree, and visa fields."""
    from src.evaluation.heuristic_scorer import evaluate_heuristic
    from src.candidate.profile_manager import get_active_profile
    with get_session() as session:
        profile = get_active_profile(session)
        query = select(JobPosting)
        if not force_all:
            query = query.where(or_(JobPosting.continent.is_(None), JobPosting.experience_level.is_(None)))
        jobs = session.scalars(query).all()
        updated_count = 0
        for job in jobs:
            result = evaluate_heuristic(job, profile)
            job.match_score = result.get("score", job.match_score or 0)
            job.key_skills_matched = json.dumps(result.get("skills", []), ensure_ascii=False)
            job.visa_sponsorship_detected = result.get("visa_sponsored", job.visa_sponsorship_detected)
            job.visa_status_notes = result.get("visa_notes", job.visa_status_notes)
            job.evaluation_reason = result.get("reason", job.evaluation_reason)
            job.continent = result.get("continent", job.continent or "Europe")
            job.experience_level = result.get("experience_level", job.experience_level or "entry")
            job.experience_years_required = result.get("experience_years_required")
            job.degree_required = result.get("degree_required", job.degree_required or "none")
            job.degree_matched = result.get("degree_matched")
            job.visa_category = result.get("visa_category", job.visa_category or "unspecified")
            job.relocation_detected = bool(result.get("relocation_detected", False))
            job.evaluated = True
            job.evaluation_status = "evaluated"
            job.evaluation_method = "heuristic"
            updated_count += 1
        session.commit()
        return {"ok": True, "updated": updated_count}


@app.get("/api/jobs/{job_id}")
def api_job(job_id: int, request: Request, _: bool = Depends(_auth)):
    from src.auth.service import get_current_user_optional
    user = get_current_user_optional(request)
    with get_session() as session:
        job = session.get(JobPosting, job_id)
        if not job:
            raise HTTPException(404, "Job not found")
        if user and user.role != "admin" and job.user_id and job.user_id != user.id:
            raise HTTPException(404, "Job not found")
        data = _job_dict(job)
        data["email_events"] = [{
            "id": e.id, "direction": e.direction, "message_id": e.message_id, "in_reply_to": e.in_reply_to,
            "references": e.references, "sender_email": e.sender_email, "recipient_email": e.recipient_email,
            "subject": e.subject, "intent": e.classified_intent, "created_at": _aware(e.created_at), "body": e.body,
            "email_type": e.email_type,
        } for e in sorted(job.email_events, key=lambda x: x.created_at)]
        return data
 

@app.get("/api/jobs/{job_id}/screenshot")
def api_job_screenshot(job_id: int, _: bool = Depends(_auth)):
    with get_session() as session:
        job = session.get(JobPosting, job_id)
        if not job:
            raise HTTPException(404, "Job not found")
        path_str = job.application_screenshot_path
        if not path_str or not Path(path_str).is_file():
            # Fallback search by job ID prefix in screenshot directory
            screenshot_dir = Path(settings.apply_screenshot_dir)
            matches = list(screenshot_dir.glob(f"{job_id}_*.png"))
            if matches:
                path_str = str(matches[0])
        if not path_str or not Path(path_str).is_file():
            raise HTTPException(404, "No application screenshot found for this job")
        return FileResponse(path_str, media_type="image/png")


@app.patch("/api/jobs/{job_id}/stage")
def update_stage(job_id: int, payload: StageUpdate, _: bool = Depends(_auth)):
    try:
        stage = PipelineStage(payload.stage)
    except ValueError:
        raise HTTPException(400, "Invalid stage")
    with get_session() as session:
        job = session.get(JobPosting, job_id)
        if not job:
            raise HTTPException(404, "Job not found")
        prev_stage = job.pipeline_stage
        job.pipeline_stage = stage
        if stage in (PipelineStage.INTERVIEW_SCHEDULED, PipelineStage.INTERVIEW_REQUESTED) and prev_stage != stage:
            try:
                from src.alerting.telegram_bot import send_telegram_interview_alert
                send_telegram_interview_alert({
                    "company": job.company,
                    "title": job.title,
                    "summary": f"Stage updated to {stage.value.replace('_', ' ').title()}",
                    "url": "http://localhost:8080/app#postings",
                })
            except Exception as e:
                logging.getLogger("src.dashboard").debug("Failed to send stage change Telegram alert: %s", e)
        return {"ok": True, "job": _job_dict(job)}


@app.get("/api/inbox")
def api_inbox(request: Request, limit: int = 100, status_filter: str | None = None, _: bool = Depends(_auth)):
    from src.auth.service import get_current_user_optional
    user = get_current_user_optional(request)
    user_id = user.id if user else None
    is_admin = user and user.role == "admin"
    all_users = bool(request.query_params.get("all_users"))

    with get_session() as session:
        query = select(InboxMessage)
        if not (is_admin and all_users) and user_id is not None:
            query = query.outerjoin(JobPosting, InboxMessage.job_id == JobPosting.id).where(
                or_(InboxMessage.user_id == user_id, JobPosting.user_id == user_id)
            )
        if status_filter:
            query = query.where(InboxMessage.status == status_filter)
        rows = session.scalars(query.order_by(desc(InboxMessage.created_at)).limit(max(1, min(limit, 500)))).all()
        return [{
            "id": x.id, "message_id": x.message_id, "imap_uid": x.imap_uid, "status": x.status,
            "subject": x.subject, "sender_email": x.sender_email, "reason": x.reason, "created_at": _aware(x.created_at),
        } for x in rows]


@app.get("/api/outbound")
def api_outbound(request: Request, limit: int = 100, status_filter: str | None = None, _: bool = Depends(_auth)):
    from src.auth.service import get_current_user_optional
    user = get_current_user_optional(request)
    user_id = user.id if user else None
    is_admin = user and user.role == "admin"
    all_users = bool(request.query_params.get("all_users"))

    with get_session() as session:
        query = select(OutboundMessage)
        if not (is_admin and all_users) and user_id is not None:
            query = query.outerjoin(JobPosting, OutboundMessage.job_id == JobPosting.id).where(
                or_(OutboundMessage.user_id == user_id, JobPosting.user_id == user_id)
            )
        if status_filter:
            query = query.where(OutboundMessage.status == status_filter)
        rows = session.scalars(query.order_by(desc(OutboundMessage.created_at)).limit(max(1, min(limit, 500)))).all()
        return [{
            "id": x.id, "job_id": x.job_id, "idempotency_key": x.idempotency_key, "email_type": x.email_type,
            "recipient_email": x.recipient_email, "subject": x.subject, "message_id": x.message_id,
            "status": x.status, "failure_reason": x.failure_reason, "created_at": _aware(x.created_at), "sent_at": _aware(x.sent_at),
        } for x in rows]


@app.get("/api/suppressions")
def api_suppressions(request: Request, limit: int = 200, _: bool = Depends(_auth)):
    from src.auth.service import get_current_user_optional
    user = get_current_user_optional(request)
    user_id = user.id if user else None
    is_admin = user and user.role == "admin"
    all_users = bool(request.query_params.get("all_users"))

    with get_session() as session:
        query = select(OutboundSuppression)
        if not (is_admin and all_users) and user_id is not None:
            query = query.where(OutboundSuppression.user_id == user_id)
        rows = session.scalars(query.order_by(desc(OutboundSuppression.created_at)).limit(max(1, min(limit, 500)))).all()
        return [{"id": x.id, "email": x.recipient_email, "domain": x.domain, "reason": x.reason, "details": x.details, "created_at": _aware(x.created_at)} for x in rows]


@app.post("/api/suppressions")
def create_suppression(request: Request, payload: SuppressionCreate, _: bool = Depends(_auth)):
    if not payload.email and not payload.domain:
        raise HTTPException(400, "Provide email or domain")
    from src.auth.service import get_current_user_optional
    user = get_current_user_optional(request)
    user_id = user.id if user else None

    from src.storage.outbound import suppress_recipient
    with get_session() as session:
        suppress_recipient(
            session,
            address=payload.email,
            domain=payload.domain,
            reason=payload.reason,
            details=payload.details,
            user_id=user_id,
        )
        return {"ok": True}


@app.delete("/api/suppressions/{suppression_id}")
def delete_suppression(suppression_id: int, request: Request, _: bool = Depends(_auth)):
    from src.auth.service import get_current_user_optional
    user = get_current_user_optional(request)
    user_id = user.id if user else None
    is_admin = user and user.role == "admin"

    with get_session() as session:
        sup = session.get(OutboundSuppression, suppression_id)
        if not sup:
            raise HTTPException(404, "Suppression not found")
        if not is_admin and sup.user_id != user_id:
            raise HTTPException(403, "Access denied")
        session.delete(sup)
        session.commit()
        return {"ok": True}


@app.get("/api/runs/{run_id}/events")
def api_run_events(run_id: int, limit: int = 200, _: bool = Depends(_auth)):
    with get_session() as session:
        rows = session.scalars(select(PipelineRunEvent).where(PipelineRunEvent.run_id == run_id).order_by(desc(PipelineRunEvent.created_at)).limit(max(1, min(limit, 500)))).all()
        return [{"id": x.id, "phase": x.phase, "status": x.status, "message": x.message, "details": json.loads(x.details_json) if x.details_json else None, "created_at": _aware(x.created_at)} for x in reversed(rows)]


@app.get("/api/runs")
def api_runs(request: Request, limit: int = 100, _: bool = Depends(_auth)):
    from src.auth.service import get_current_user_optional
    user = get_current_user_optional(request)
    user_id = user.id if user else None
    with get_session() as session:
        stmt = select(PipelineRun)
        if user_id is not None:
            stmt = stmt.where(PipelineRun.user_id == user_id)
        runs = session.scalars(stmt.order_by(desc(PipelineRun.started_at)).limit(max(1, min(limit, 200)))).all()
        return [{
            "id": r.id, "started_at": _aware(r.started_at), "finished_at": _aware(r.finished_at), "status": r.status,
            "phase": r.phase or ("complete" if r.status == "success" else "running"),
            "progress_pct": r.progress_pct,
            "current_task": r.current_task,
            "duration_seconds": int((_aware_dt(r.finished_at) - _aware_dt(r.started_at)).total_seconds()) if (r.finished_at and r.started_at) else None,
            "raw_scraped": r.raw_scraped or r.raw_scraped_live or 0,
            "scrape_errors": r.scrape_errors or 0,
            "new_postings": r.new_postings or r.new_postings_live or 0,
            "evaluated": r.evaluated or r.evaluated_live or 0,
            "applications_sent": r.applications_sent or 0,
            "ai_calls": r.claude_calls, "prefilter_skipped": r.claude_skipped_prefilter,
            "budget_skipped": r.claude_skipped_budget, "evaluation_failures": r.evaluation_failures,
            "anomaly_alerted": r.anomaly_alerted, "error": r.error, "summary": json.loads(r.summary_json) if r.summary_json else None,
        } for r in runs]


_pipeline_lock = threading.Lock()
_pipeline_thread: threading.Thread | None = None


@app.post("/api/pipeline/run")
def api_pipeline_run(request: Request, _: bool = Depends(_auth)):
    from src.auth.service import get_current_user_optional
    user = get_current_user_optional(request)
    user_id = user.id if user else None

    # Gate: require at least one active profile with essential fields populated for this user
    with get_session() as session:
        query = select(CandidateProfile).where(CandidateProfile.is_active.is_(True))
        if user_id is not None:
            query = query.where(CandidateProfile.user_id == user_id)
        active_profiles = session.scalars(query).all()
        if not active_profiles and user_id is not None:
            active_profiles = session.scalars(
                select(CandidateProfile).where(CandidateProfile.user_id == user_id).order_by(desc(CandidateProfile.id)).limit(1)
            ).all()
        if not active_profiles:
            raise HTTPException(
                400,
                "Cannot start pipeline: No active candidate profile found for your account. "
                "Please create and activate a profile with your target titles, core stack, and keywords first."
            )
        # Check that the profile has meaningful data
        profile = active_profiles[0]
        missing = []
        if not profile.core_stack or profile.core_stack in ("[]", "null", ""):
            missing.append("core_stack (your technical skills)")
        if not profile.target_titles or profile.target_titles in ("[]", "null", ""):
            missing.append("target_titles (job titles you're looking for)")
        if not profile.keywords or profile.keywords in ("[]", "null", ""):
            missing.append("keywords (search keywords)")
        if missing:
            raise HTTPException(
                400,
                f"Cannot start pipeline: Your active profile is missing required fields: {', '.join(missing)}. "
                "Please complete your profile before running the job discovery pipeline."
            )

    global _pipeline_thread
    with _pipeline_lock:
        if _pipeline_thread and _pipeline_thread.is_alive():
            return JSONResponse(status_code=409, content={"ok": False, "message": "Job discovery pipeline is already running"})

        from src.orchestrator import run_pipeline

        def _bg_worker():
            try:
                run_pipeline(user_id=user_id)
            except Exception as exc:
                logging.getLogger("src.dashboard").exception("Background pipeline execution error: %s", exc)

        _pipeline_thread = threading.Thread(target=_bg_worker, daemon=True, name="pipeline-run-worker")
        _pipeline_thread.start()
        return {"ok": True, "message": "Job discovery pipeline started in background"}


@app.get("/api/config")
def api_config(request: Request, _: bool = Depends(_auth)):
    """Expose operational configuration without exposing API/email credentials."""
    from src.auth.service import get_current_user_optional
    user = get_current_user_optional(request)
    if user and user.role != "admin":
        raise HTTPException(status_code=403, detail="System configuration is restricted to administrators")
    return {
        "ai": {"provider": settings.ai_provider, "fallbacks": settings.ai_fallback_providers, "ollama_url": settings.ollama_base_url, "ollama_model": settings.ollama_model, "timeout_seconds": settings.ai_request_timeout_seconds, "daily_limit": settings.max_ai_calls_per_day, "run_limit": settings.max_ai_calls_per_run},
        "search": {"results_wanted_per_query": settings.results_wanted_per_query, "hours_old": settings.hours_old, "scrape_workers": settings.scrape_workers, "scrape_sites": settings.scrape_sites, "linkedin_fetch_description": settings.linkedin_fetch_description, "min_match_score": settings.min_match_score, "min_visa_confidence": settings.min_visa_confidence, "prefilter_enabled": settings.claude_prefilter_enabled},
        "automation": {"auto_apply": settings.auto_apply_mode, "web_enabled": settings.auto_apply_web_enabled, "known_ats_only": settings.auto_apply_known_ats_only, "min_score": settings.auto_apply_min_score, "require_visa_or_remote": settings.auto_apply_require_visa_or_remote, "auto_reply": settings.auto_reply_mode, "auto_negotiate": settings.auto_negotiate_mode},
        "email_safety": {"send_enabled": settings.outbound_send_enabled, "kill_switch": settings.outbound_send_kill_switch, "daily": settings.max_outbound_emails_per_day, "hourly": settings.max_outbound_emails_per_hour, "applications_daily": settings.max_applications_per_day, "min_gap_seconds": settings.min_seconds_between_external_emails, "min_body_chars": settings.outbound_min_body_chars, "max_urls": settings.outbound_max_urls, "max_exclamation_marks": settings.outbound_max_exclamation_marks},
        "inbox": {"poll_minutes": settings.inbox_poll_minutes, "follow_up_after_days": settings.follow_up_after_days, "max_follow_ups": settings.max_follow_ups},
        "timezone": settings.candidate_timezone,
        "pipeline_interval_hours": settings.pipeline_interval_hours,
    }


# ---------------------------------------------------------------------------
# Candidate Profile & Keyword Management API
# ---------------------------------------------------------------------------

class ProfileCreate(BaseModel):
    name: str
    headline: str | None = None
    current_location: str | None = None
    target_locations: list[str] = []
    target_titles: list[str] = []
    core_stack: list[str] = []
    keywords: list[str] = []
    negative_keywords: list[str] = []
    experience_years: int = 3
    visa_requirement: str | None = None
    languages: dict[str, str] = {}
    resume_text: str | None = None
    resume_path: str | None = None
    freelance_services: list[str] = []
    freelance_hourly_usd: int = 50
    freelance_daily_eur: int = 400
    freelance_currency: str = "EUR"
    calendar_url: str | None = None
    degree_level: str = "bachelor"
    target_experience_level: str = "entry"
    is_active: bool = False


class ProfileUpdate(BaseModel):
    name: str | None = None
    headline: str | None = None
    current_location: str | None = None
    target_locations: list[str] | None = None
    target_titles: list[str] | None = None
    core_stack: list[str] | None = None
    keywords: list[str] | None = None
    negative_keywords: list[str] | None = None
    experience_years: int | None = None
    visa_requirement: str | None = None
    languages: dict[str, str] | None = None
    resume_text: str | None = None
    resume_path: str | None = None
    freelance_services: list[str] | None = None
    freelance_hourly_usd: int | None = None
    freelance_daily_eur: int | None = None
    freelance_currency: str | None = None
    calendar_url: str | None = None
    degree_level: str | None = None
    target_experience_level: str | None = None
    is_active: bool | None = None


@app.get("/api/profiles")
def api_get_profiles(request: Request, _: bool = Depends(_auth)):
    from src.auth.service import get_current_user_optional
    from src.candidate.profile_manager import list_profiles
    user = get_current_user_optional(request)
    return list_profiles(user_id=user.id if user else None)


@app.get("/api/profiles/active")
def api_get_active_profile(request: Request, _: bool = Depends(_auth)):
    from src.auth.service import get_current_user_optional
    from src.candidate.profile_manager import get_active_profile
    user = get_current_user_optional(request)
    return {"ok": True, "profile": get_active_profile(user_id=user.id if user else None)}


@app.get("/api/profiles/{profile_id}")
def api_get_profile(profile_id: int, request: Request, _: bool = Depends(_auth)):
    from src.auth.service import get_current_user_optional
    from src.candidate.profile_manager import get_profile_by_id
    user = get_current_user_optional(request)
    p = get_profile_by_id(profile_id, user_id=user.id if user else None)
    if not p:
        raise HTTPException(404, f"Profile {profile_id} not found")
    return p


@app.post("/api/profiles")
def api_create_profile(payload: ProfileCreate, request: Request, _: bool = Depends(_auth)):
    from src.auth.service import get_current_user_optional
    from src.candidate.profile_manager import create_profile
    user = get_current_user_optional(request)
    try:
        return create_profile(payload.model_dump(), user_id=user.id if user else None)
    except ValueError as exc:
        raise HTTPException(400, str(exc)) from exc


@app.put("/api/profiles/{profile_id}")
def api_update_profile(profile_id: int, payload: ProfileUpdate, request: Request, _: bool = Depends(_auth)):
    from src.auth.service import get_current_user_optional
    from src.candidate.profile_manager import update_profile
    user = get_current_user_optional(request)
    data = {k: v for k, v in payload.model_dump().items() if v is not None}
    try:
        return update_profile(profile_id, data, user_id=user.id if user else None)
    except ValueError as exc:
        raise HTTPException(400, str(exc)) from exc


@app.delete("/api/profiles/{profile_id}")
def api_delete_profile(profile_id: int, request: Request, _: bool = Depends(_auth)):
    from src.auth.service import get_current_user_optional
    from src.candidate.profile_manager import delete_profile
    user = get_current_user_optional(request)
    try:
        ok = delete_profile(profile_id, user_id=user.id if user else None)
        if not ok:
            raise HTTPException(404, f"Profile {profile_id} not found")
        return {"ok": True, "message": f"Profile {profile_id} deleted"}
    except ValueError as exc:
        raise HTTPException(400, str(exc)) from exc


@app.post("/api/profiles/{profile_id}/activate")
def api_activate_profile(profile_id: int, request: Request, _: bool = Depends(_auth)):
    from src.auth.service import get_current_user_optional
    from src.candidate.profile_manager import set_active_profile
    user = get_current_user_optional(request)
    try:
        p = set_active_profile(profile_id, user_id=user.id if user else None)
        return {"ok": True, "profile": p}
    except ValueError as exc:
        raise HTTPException(400, str(exc)) from exc


@app.get("/api/profiles/active/matrix")
def api_preview_active_matrix(request: Request, _: bool = Depends(_auth)):
    from src.auth.service import get_current_user_optional
    from src.candidate.profile_manager import generate_search_matrix, get_active_profile
    user = get_current_user_optional(request)
    p = get_active_profile(user_id=user.id if user else None)
    if not p:
        return {"profile_name": None, "queries_count": 0, "matrix": []}
    matrix = generate_search_matrix(p)
    return {"profile_name": p.get("name"), "queries_count": len(matrix), "matrix": matrix}


# ---------------------------------------------------------------------------
# Candidate CV Download & Machine Learning Analysis Endpoints
# ---------------------------------------------------------------------------

@app.get("/api/candidate/cv/download")
def api_download_candidate_cv(_: bool = Depends(_auth)):
    """Download the candidate's CV/Resume PDF directly from the platform."""
    resume_path = Path(settings.candidate_resume_path)
    if not resume_path.is_file():
        resume_path = Path("candidate_data/resume.pdf")
    if not resume_path.is_file():
        raise HTTPException(404, "Candidate CV / resume PDF not found on server")

    from src.candidate.profile_manager import get_active_profile
    profile = get_active_profile()
    name = (profile.get("name") or "Candidate").split(" - ")[0].strip().replace(" ", "_")
    filename = f"{name}_Resume.pdf"

    return FileResponse(
        str(resume_path),
        media_type="application/pdf",
        filename=filename,
        headers={"Content-Disposition": f'attachment; filename="{filename}"'}
    )


@app.get("/api/candidate/cv/analyze")
def api_analyze_candidate_cv(_: bool = Depends(_auth)):
    """Analyze the candidate's CV using machine learning and NLP feature extraction."""
    from src.candidate.cv_ml_engine import extract_text_from_pdf, analyze_cv_content
    resume_path = Path(settings.candidate_resume_path)
    if not resume_path.is_file():
        resume_path = Path("candidate_data/resume.pdf")
    if not resume_path.is_file():
        raise HTTPException(404, "Candidate CV / resume PDF not found on server")

    text = extract_text_from_pdf(resume_path)
    if not text:
        raise HTTPException(500, "Could not extract readable text from candidate CV PDF")

    analysis = analyze_cv_content(text)
    return {
        "ok": True,
        "resume_path": str(resume_path),
        "text_length": len(text),
        "analysis": analysis,
    }


@app.post("/api/candidate/cv/sync_profile")
def api_sync_cv_to_profile(profile_id: Optional[int] = None, _: bool = Depends(_auth)):
    """Synchronize ML-extracted CV features into the candidate profile database."""
    from src.candidate.cv_ml_engine import extract_text_from_pdf, analyze_cv_content
    from src.candidate.profile_manager import get_active_profile, update_profile

    resume_path = Path(settings.candidate_resume_path)
    if not resume_path.is_file():
        resume_path = Path("candidate_data/resume.pdf")
    if not resume_path.is_file():
        raise HTTPException(404, "Candidate CV / resume PDF not found on server")

    text = extract_text_from_pdf(resume_path)
    analysis = analyze_cv_content(text)

    target_id = profile_id
    if not target_id:
        active = get_active_profile()
        target_id = active.get("id")

    if not target_id:
        raise HTTPException(404, "No active profile to sync with")

    update_data = {
        "name": analysis.get("name") or active.get("name") or "Candidate",
        "headline": f"Software Engineer ({', '.join(analysis.get('top_skills', [])[:4])})" if analysis.get("top_skills") else (active.get("headline") or "Software Engineer"),
        "current_location": analysis.get("location") or active.get("current_location") or "",
        "experience_years": analysis.get("experience_years", active.get("experience_years", 0)),
        "degree_level": analysis.get("degree_level", active.get("degree_level", "bachelor")),
        "core_stack": analysis.get("top_skills", []),
        "keywords": [s.lower() for s in analysis.get("top_skills", [])],
        "target_titles": analysis.get("target_roles", []),
        "resume_text": text[:5000],
    }

    updated = update_profile(target_id, update_data)
    return {
        "ok": True,
        "message": "Active candidate profile successfully updated from CV ML analysis! ✓",
        "profile": updated
    }


@app.get("/api/jobs/{job_id}/ml_match")
def api_job_ml_match(job_id: int, _: bool = Depends(_auth)):
    """Calculate Vector Cosine Similarity and skill-fit breakdown between CV and job posting."""
    with get_session() as session:
        job = session.get(JobPosting, job_id)
        if not job:
            raise HTTPException(404, "Job not found")
        job_dict = _job_dict(job)

    from src.candidate.cv_ml_engine import extract_text_from_pdf, analyze_cv_content, compute_job_cv_match
    resume_path = Path(settings.candidate_resume_path)
    if not resume_path.is_file():
        resume_path = Path("candidate_data/resume.pdf")

    cv_text = extract_text_from_pdf(resume_path) if resume_path.is_file() else ""
    cand_features = analyze_cv_content(cv_text) if cv_text else {}

    match_result = compute_job_cv_match(job_dict, cand_features)
    return {"ok": True, "job_id": job_id, "ml_match": match_result}


# ---------------------------------------------------------------------------
# Authentication, RBAC, Payments & Admin Control Endpoints
# ---------------------------------------------------------------------------

class RegisterRequest(BaseModel):
    email: str
    password: str
    full_name: str = ""
    plan: str = "free"


class LoginRequest(BaseModel):
    email: str
    password: str


class GoogleAuthRequest(BaseModel):
    id_token: str | None = None
    email: str | None = None
    name: str | None = None


class CheckoutRequest(BaseModel):
    plan_name: str  # starter_99, pro_499
    amount_mad: int  # 99, 499
    payment_method: str  # cih_wire, attijari_wire, cmi_card, stripe
    reference_code: str
    receipt_note: str | None = None


class AdminUserUpdate(BaseModel):
    role: str | None = None
    current_plan: str | None = None
    daily_apply_limit: int | None = None
    is_active: bool | None = None


class AdminPaymentAction(BaseModel):
    admin_notes: str | None = None


class AdminPlanCreate(BaseModel):
    slug: str
    name: str
    badge: str | None = None
    description: str | None = None
    price_usd: float = 0.0
    price_mad: int = 0
    price_eur: float = 0.0
    price_usdt: float = 0.0
    billing_interval: str = "/ month"
    daily_apply_limit: int = 50
    max_ai_calls_per_day: int = 50
    can_access_freelance: bool = False
    is_active: bool = True
    is_recommended: bool = False
    features: list[str] = []
    sort_order: int = 0
    sync_users: bool = True


class AdminPlanUpdate(BaseModel):
    name: str | None = None
    badge: str | None = None
    description: str | None = None
    price_usd: float | None = None
    price_mad: int | None = None
    price_eur: float | None = None
    price_usdt: float | None = None
    billing_interval: str | None = None
    daily_apply_limit: int | None = None
    max_ai_calls_per_day: int | None = None
    can_access_freelance: bool | None = None
    is_active: bool | None = None
    is_recommended: bool | None = None
    features: list[str] | None = None
    sort_order: int | None = None
    sync_users: bool = True


class AdminGatewayUpdate(BaseModel):
    name: str | None = None
    category: str | None = None
    is_enabled: bool | None = None
    instructions: str | None = None
    config: dict[str, Any] | None = None
    sort_order: int | None = None


class StripeSessionRequest(BaseModel):
    plan_slug: str
    success_url: str | None = None
    cancel_url: str | None = None


AdminPlanCreate.model_rebuild()
AdminPlanUpdate.model_rebuild()
AdminGatewayUpdate.model_rebuild()
StripeSessionRequest.model_rebuild()


@app.post("/api/auth/register")
def api_auth_register(req: RegisterRequest, response: Response):
    email = req.email.strip().lower()
    if "@" not in email or "." not in email:
        raise HTTPException(400, "Invalid email address")
    if len(req.password) < 6:
        raise HTTPException(400, "Password must be at least 6 characters")

    from src.auth.service import hash_password, create_session_token
    from src.storage.models import User, UserRole, CandidateProfile
    from src.storage.plans import get_plan_by_slug

    with get_session() as session:
        existing = session.scalar(select(User).where(User.email == email))
        if existing:
            raise HTTPException(400, "An account with this email already exists")

        db_plan = get_plan_by_slug(session, req.plan)
        plan = db_plan.slug if db_plan else "free"
        daily_limit = db_plan.daily_apply_limit if db_plan else 5

        user = User(
            email=email,
            full_name=req.full_name.strip() or email.split("@")[0],
            password_hash=hash_password(req.password),
            role=UserRole.USER.value,
            is_active=True,
            is_verified=False,
            auth_provider="local",
            current_plan=plan,
            daily_apply_limit=daily_limit,
        )
        session.add(user)
        session.flush()

        # Initialize clean, independent candidate profile for this specific user
        user_prof = CandidateProfile(
            user_id=user.id,
            name=user.full_name,
            headline="",
            current_location="",
            target_locations="[]",
            target_titles="[]",
            core_stack="[]",
            keywords="[]",
            negative_keywords="[]",
            experience_years=0,
            languages="{}",
            freelance_services="[]",
            is_active=True,
        )
        session.add(user_prof)
        session.commit()

        token = create_session_token(user.id, user.email, user.role)
        response.set_cookie(
            key="session_token",
            value=token,
            max_age=7 * 24 * 3600,
            httponly=True,
            samesite="lax",
        )

        logger.info("New candidate registered: %s (Plan: %s, Limit: %s/day)", user.email, user.current_plan, user.daily_apply_limit)

        # Notify administrator only of new user registration
        try:
            from src.notifications.publisher import publish_notification
            publish_notification(
                user_id=None,
                event_type="user_registered",
                title=f"New Candidate Registered: {user.full_name or user.email}",
                message=f"Candidate {user.email} registered on plan '{user.current_plan}' (Default quota: {user.daily_apply_limit} apps/day).",
                link="#admin",
                admin_only=True,
                data={
                    "user_id": user.id,
                    "email": user.email,
                    "full_name": user.full_name,
                    "current_plan": user.current_plan,
                    "daily_apply_limit": user.daily_apply_limit,
                    "admin_only": True,
                },
            )
        except Exception as notif_err:
            logger.warning("Failed to publish user_registered notification: %s", notif_err)

        return {
            "ok": True,
            "token": token,
            "user": {
                "id": user.id,
                "email": user.email,
                "full_name": user.full_name,
                "role": user.role,
                "current_plan": user.current_plan,
                "daily_apply_limit": user.daily_apply_limit,
            }
        }


@app.post("/api/auth/login")
def api_auth_login(req: LoginRequest, response: Response):
    ident = req.email.strip().lower()
    from src.auth.service import verify_password, create_session_token, hash_password
    from src.storage.models import User, UserRole

    with get_session() as session:
        # Look up user by email (try both plain email and @autohunt.internal variant)
        user = session.scalar(select(User).where(or_(User.email == ident, User.email == f"{ident}@autohunt.internal")))

        # If not found, check if this is the .env admin email or dashboard username
        admin_email = (settings.admin_email or "").strip().lower()
        admin_password = settings.admin_password or settings.dashboard_password or ""
        if not user:
            is_admin_login = (
                ident == admin_email
                or ident == settings.dashboard_username
                or ident == f"{settings.dashboard_username}@autohunt.internal"
            )
            if is_admin_login and secrets.compare_digest(req.password, admin_password):
                # Auto-create admin if it doesn't exist yet
                user = User(
                    email=admin_email or f"{settings.dashboard_username}@autohunt.internal",
                    full_name="Platform Administrator",
                    password_hash=hash_password(req.password),
                    role=UserRole.ADMIN.value,
                    is_active=True,
                    is_verified=True,
                    auth_provider="local",
                    current_plan="pro_499",
                    daily_apply_limit=200,
                )
                session.add(user)
                session.commit()

        if not user:
            raise HTTPException(401, "Invalid email or password")

        pwd_valid = False
        if user.password_hash and verify_password(req.password, user.password_hash):
            pwd_valid = True
        # Also accept .env admin password for any admin user
        elif user.role == UserRole.ADMIN.value and admin_password and secrets.compare_digest(req.password, admin_password):
            pwd_valid = True
        # Backward compat: accept dashboard_password for admin
        elif user.role == UserRole.ADMIN.value and settings.dashboard_password and secrets.compare_digest(req.password, settings.dashboard_password):
            pwd_valid = True

        if not pwd_valid:
            raise HTTPException(401, "Invalid email or password")

        if not user.is_active:
            raise HTTPException(403, "Account is disabled. Contact support.")

        token = create_session_token(user.id, user.email, user.role)
        response.set_cookie(
            key="session_token",
            value=token,
            max_age=7 * 24 * 3600,
            httponly=True,
            samesite="lax",
        )

        logger.info("User login successful: %s (Role: %s)", user.email, user.role)

        return {
            "ok": True,
            "token": token,
            "user": {
                "id": user.id,
                "email": user.email,
                "full_name": user.full_name,
                "role": user.role,
                "current_plan": user.current_plan,
                "daily_apply_limit": user.daily_apply_limit,
            }
        }


@app.post("/api/auth/google")
def api_auth_google(req: GoogleAuthRequest, response: Response):
    from src.auth.service import verify_google_id_token, create_session_token
    from src.storage.models import User, UserRole, CandidateProfile

    verified_email = None
    full_name = None
    sub = None
    avatar = None

    if req.id_token:
        google_data = verify_google_id_token(req.id_token)
        if not google_data:
            raise HTTPException(400, "Invalid or unverified Google token")
        verified_email = google_data["email"].lower()
        full_name = google_data.get("name")
        sub = google_data.get("sub")
        avatar = google_data.get("picture")
    elif req.email and "@" in req.email:
        verified_email = req.email.strip().lower()
        full_name = req.name or verified_email.split("@")[0]
        sub = f"gmail_{hashlib.md5(verified_email.encode()).hexdigest()[:16]}"
    else:
        raise HTTPException(400, "Google token or verified email required")

    with get_session() as session:
        user = session.scalar(select(User).where(User.email == verified_email))
        if not user:
            user = User(
                email=verified_email,
                full_name=full_name or verified_email.split("@")[0],
                role=UserRole.USER.value,
                is_active=True,
                is_verified=True,
                auth_provider="google",
                google_sub=sub,
                avatar_url=avatar,
                current_plan="free",
                daily_apply_limit=5,
            )
            session.add(user)
            session.flush()

            # Initialize clean, independent candidate profile for this specific user
            user_prof = CandidateProfile(
                user_id=user.id,
                name=user.full_name,
                headline="",
                current_location="",
                target_locations="[]",
                target_titles="[]",
                core_stack="[]",
                keywords="[]",
                negative_keywords="[]",
                experience_years=0,
                languages="{}",
                freelance_services="[]",
                is_active=True,
            )
            session.add(user_prof)
            session.commit()

            # Notify administrator & WebSocket subscribers of new Google user registration
            try:
                from src.notifications.publisher import publish_notification
                publish_notification(
                    user_id=None,
                    event_type="user_registered",
                    title=f"New User (Google): {user.full_name or user.email}",
                    message=f"Google user {user.email} registered on 'free' plan (Default quota: {user.daily_apply_limit} apps/day).",
                    link="#admin",
                    admin_only=True,
                    data={
                        "user_id": user.id,
                        "email": user.email,
                        "full_name": user.full_name,
                        "current_plan": user.current_plan,
                        "daily_apply_limit": user.daily_apply_limit,
                        "admin_only": True,
                    },
                )
            except Exception as notif_err:
                logger.warning("Failed to publish Google user_registered notification: %s", notif_err)
        else:
            if not user.is_verified:
                user.is_verified = True
            if avatar and not user.avatar_url:
                user.avatar_url = avatar
            session.commit()

        token = create_session_token(user.id, user.email, user.role)
        response.set_cookie(
            key="session_token",
            value=token,
            max_age=7 * 24 * 3600,
            httponly=True,
            samesite="lax",
        )

        return {
            "ok": True,
            "token": token,
            "user": {
                "id": user.id,
                "email": user.email,
                "full_name": user.full_name,
                "role": user.role,
                "current_plan": user.current_plan,
                "daily_apply_limit": user.daily_apply_limit,
            }
        }


@app.get("/api/auth/me")
def api_auth_me(request: Request):
    from src.auth.service import get_current_user
    user = get_current_user(request)
    with get_session() as session:
        today_start = datetime.now(timezone.utc).replace(hour=0, minute=0, second=0, microsecond=0)
        from src.storage.models import UserJobApplication
        total_platform_today = session.scalar(
            select(func.count(JobPosting.id)).where(JobPosting.applied_at >= today_start)
        ) or 0
        user_apps_today = session.scalar(
            select(func.count(UserJobApplication.id)).where(
                UserJobApplication.user_id == user.id,
                UserJobApplication.applied_at >= today_start,
            )
        ) or 0

        # Synchronize: for admin role, ensure sent_today reflects actual platform applications today
        if user.role == "admin":
            sent_today = max(user_apps_today, total_platform_today)
        else:
            sent_today = user_apps_today

        return {
            "ok": True,
            "user": {
                "id": user.id,
                "email": user.email,
                "full_name": user.full_name,
                "role": user.role,
                "avatar_url": user.avatar_url,
                "current_plan": user.current_plan,
                "daily_apply_limit": user.daily_apply_limit,
                "applications_sent_today": sent_today,
                "applications_remaining_today": max(0, user.daily_apply_limit - sent_today),
                "is_verified": user.is_verified,
            },
            "applications_sent_today": sent_today,
            "applications_today": sent_today,
            "remaining_today": max(0, user.daily_apply_limit - sent_today),
            "daily_apply_limit": user.daily_apply_limit,
        }


@app.post("/api/auth/logout")
def api_auth_logout(response: Response):
    response.delete_cookie(key="session_token")
    return {"ok": True, "message": "Logged out successfully"}



# ---------------------------------------------------------------------------
# Admin Management: Users, Payments & Real-Time System Logs
# ---------------------------------------------------------------------------

@app.get("/api/admin/users")
def api_admin_list_users(request: Request):
    from src.auth.service import require_admin
    require_admin(request)
    from src.storage.models import User
    from src.storage.plans import get_all_plans_db, resolve_plan_slug

    with get_session() as session:
        users = session.scalars(select(User).order_by(desc(User.created_at))).all()
        db_plans = get_all_plans_db(session, include_inactive=True)

        plans_dict = {}
        for p in db_plans:
            plans_dict[p.slug.strip().lower()] = p
            norm = resolve_plan_slug(p.slug)
            if norm not in plans_dict:
                plans_dict[norm] = p

        user_list = []
        for u in users:
            curr_slug = (u.current_plan or "free").strip().lower()
            p_obj = plans_dict.get(curr_slug) or plans_dict.get(resolve_plan_slug(curr_slug))
            p_name = p_obj.name if p_obj else (u.current_plan or "Free").capitalize()
            p_badge = p_obj.badge if p_obj else ""
            p_limit = p_obj.daily_apply_limit if p_obj else 5
            is_synced = bool(u.daily_apply_limit == p_limit) if p_obj else False

            user_list.append({
                "id": u.id,
                "email": u.email,
                "full_name": u.full_name,
                "role": u.role,
                "is_active": u.is_active,
                "is_verified": u.is_verified,
                "auth_provider": u.auth_provider,
                "current_plan": u.current_plan,
                "plan_name": p_name,
                "plan_badge": p_badge,
                "plan_daily_limit": p_limit,
                "is_quota_synced": is_synced,
                "daily_apply_limit": u.daily_apply_limit,
                "created_at": _aware(u.created_at),
            })

        return {
            "ok": True,
            "users": user_list,
            "plans": [
                {
                    "id": p.id,
                    "slug": p.slug,
                    "name": p.name,
                    "badge": p.badge or "",
                    "daily_apply_limit": p.daily_apply_limit,
                    "price_mad": p.price_mad,
                    "price_usd": p.price_usd,
                    "price_eur": p.price_eur,
                    "price_usdt": p.price_usdt,
                    "can_access_freelance": p.can_access_freelance,
                    "is_active": p.is_active,
                    "is_recommended": p.is_recommended,
                    "sort_order": p.sort_order,
                }
                for p in db_plans
            ],
        }


@app.patch("/api/admin/users/{user_id}")
def api_admin_update_user(user_id: int, req: AdminUserUpdate, request: Request):
    from src.auth.service import require_admin
    require_admin(request)
    from src.storage.models import User

    with get_session() as session:
        user = session.get(User, user_id)
        if not user:
            raise HTTPException(404, "User not found")

        if req.role in ["admin", "user"]:
            user.role = req.role
        if req.current_plan:
            from src.storage.plans import get_effective_daily_limit
            user.current_plan = req.current_plan
            if req.daily_apply_limit is None:
                user.daily_apply_limit = get_effective_daily_limit(session, req.current_plan, fallback_limit=5)
        if req.daily_apply_limit is not None:
            user.daily_apply_limit = max(1, req.daily_apply_limit)
        if req.is_active is not None:
            user.is_active = req.is_active

        user.updated_at = datetime.now(timezone.utc)
        session.commit()
        return {"ok": True, "message": f"User {user.email} updated successfully"}


class AdminDailyLimitUpdate(BaseModel):
    daily_apply_limit: int


@app.patch("/api/admin/users/{user_id}/daily_limit")
def api_admin_update_daily_limit(user_id: int, req: AdminDailyLimitUpdate, request: Request):
    """Admin: adjust application limit per day for a specific user."""
    from src.auth.service import require_admin
    require_admin(request)
    from src.storage.models import User

    if req.daily_apply_limit < 1:
        raise HTTPException(400, "Daily application limit must be at least 1")

    with get_session() as session:
        user = session.get(User, user_id)
        if not user:
            raise HTTPException(404, "User not found")
        user.daily_apply_limit = req.daily_apply_limit
        user.updated_at = datetime.now(timezone.utc)
        session.commit()
        return {
            "ok": True,
            "message": f"Daily limit for {user.email} updated to {user.daily_apply_limit} applications/day.",
            "daily_apply_limit": user.daily_apply_limit,
        }


@app.post("/api/admin/users/{user_id}/sync-plan")
def api_admin_sync_user_to_plan(user_id: int, request: Request):
    """Admin: synchronize a single user's application quota with their current plan's configured quota."""
    from src.auth.service import require_admin
    admin = require_admin(request)
    from src.storage.models import User
    from src.storage.plans import sync_user_quota_to_plan, get_plan_by_slug

    with get_session() as session:
        user = session.get(User, user_id)
        if not user:
            raise HTTPException(404, "User not found")

        plan_obj = get_plan_by_slug(session, user.current_plan)
        new_limit = sync_user_quota_to_plan(session, user)
        plan_name = plan_obj.name if plan_obj else (user.current_plan or "free").capitalize()
        logger.info("Admin #%s synced user #%s (%s) quota to %d apps/day (Plan: %s)", admin.id, user.id, user.email, new_limit, user.current_plan)
        return {
            "ok": True,
            "message": f"User {user.email} quota synced with plan '{plan_name}' to {new_limit} apps/day.",
            "daily_apply_limit": new_limit,
            "plan_name": plan_name,
        }


@app.post("/api/admin/users/sync-all")
def api_admin_sync_all_users_to_plans(request: Request):
    """Admin: synchronize all users' daily application limits to their active platform plan."""
    from src.auth.service import require_admin
    admin = require_admin(request)
    from src.storage.plans import sync_all_users_quotas

    with get_session() as session:
        synced_count = sync_all_users_quotas(session)
        logger.info("Admin #%s synced %d users to their respective plan quotas", admin.id, synced_count)
        return {
            "ok": True,
            "message": f"Successfully synchronized {synced_count} user(s) to match their assigned plan quotas.",
            "synced_users": synced_count,
        }



# ---------------------------------------------------------------------------
# Universal Multi-Method Payment Endpoints (No LTD Required)
# ---------------------------------------------------------------------------

class PaymentCheckoutRequest(BaseModel):
    plan_name: str
    payment_method: str
    reference_code: str
    amount: float | None = None
    amount_mad: int | None = None
    amount_usd: float | None = None
    currency: str | None = "USD"
    receipt_note: str | None = None
    receipt_image_base64: str | None = None


@app.get("/api/payments/methods")
def api_get_payment_methods(request: Request):
    """Return available payment channels and dynamic subscription plans configured by admin."""
    from src.storage.plans import get_all_plans_db, get_payment_gateways_dict
    import json

    with get_session() as session:
        db_plans = get_all_plans_db(session, include_inactive=False)
        plans_data = []
        for p in db_plans:
            try:
                features = json.loads(p.features_json or "[]")
            except Exception:
                features = []
            plans_data.append({
                "id": p.id,
                "slug": p.slug,
                "name": p.name,
                "badge": p.badge or "",
                "price_usd": p.price_usd,
                "price_mad": p.price_mad,
                "price_eur": p.price_eur,
                "price_usdt": p.price_usdt,
                "billing": p.billing_interval or "/ month",
                "daily_limit": p.daily_apply_limit,
                "max_ai_calls": p.max_ai_calls_per_day,
                "can_access_freelance": p.can_access_freelance,
                "description": p.description or "",
                "features": features,
                "recommended": bool(p.is_recommended),
            })

        methods_data = get_payment_gateways_dict(session, include_secrets=False)

        # Compatibility keys for frontend UI
        if "morocco_banks" in methods_data and "morocco" not in methods_data:
            methods_data["morocco"] = methods_data["morocco_banks"]
        if "crypto_usdt_trc20" in methods_data and "usdt_trc20" not in methods_data:
            methods_data["usdt_trc20"] = methods_data["crypto_usdt_trc20"]
        if "crypto_usdt_polygon" in methods_data and "usdt_polygon" not in methods_data:
            methods_data["usdt_polygon"] = methods_data["crypto_usdt_polygon"]
        if "crypto_solana" in methods_data and "solana" not in methods_data:
            methods_data["solana"] = methods_data["crypto_solana"]
        if "crypto_btc" in methods_data and "btc" not in methods_data:
            methods_data["btc"] = methods_data["crypto_btc"]

        return {
            "ok": True,
            "plans": plans_data,
            "methods": methods_data,
        }


@app.post("/api/payments/checkout")
def api_payments_checkout(req: PaymentCheckoutRequest, request: Request):
    """User submits a payment reference for plan activation (Crypto, PayPal, Card, Wise, CIH)."""
    from src.auth.service import get_current_user
    user = get_current_user(request)
    from src.storage.models import SubscriptionPayment

    ref = (req.reference_code or "").strip()
    if not ref:
        raise HTTPException(400, "Reference code, transaction hash, or receipt ID is required")

    currency = (req.currency or "USD").upper()
    amount = float(req.amount or req.amount_usd or (req.amount_mad / 10.0 if req.amount_mad else 15.0))
    amount_usd = amount if currency in ("USD", "USDT", "EUR") else round(amount / 10.0, 2)
    amount_mad = int(req.amount_mad or (amount if currency == "MAD" else round(amount * 10)))

    receipt_path = None
    if req.receipt_image_base64:
        try:
            b64_data = req.receipt_image_base64
            if "," in b64_data:
                b64_data = b64_data.split(",", 1)[1]
            raw_bytes = base64.b64decode(b64_data)
            receipts_dir = Path("outreach_drafts/payment_receipts")
            receipts_dir.mkdir(parents=True, exist_ok=True)
            safe_name = f"receipt_u{user.id}_{int(datetime.now().timestamp())}_{secrets.token_hex(4)}.png"
            file_dest = receipts_dir / safe_name
            file_dest.write_bytes(raw_bytes)
            receipt_path = str(file_dest).replace("\\", "/")
        except Exception as exc:
            logger.warning("Failed to decode receipt image: %s", exc)

    with get_session() as session:
        payment = SubscriptionPayment(
            user_id=user.id,
            plan_name=req.plan_name,
            amount_mad=amount_mad,
            amount_usd=amount_usd,
            currency=currency,
            payment_method=req.payment_method,
            reference_code=ref,
            receipt_note=req.receipt_note or "",
            receipt_image_path=receipt_path,
            status="pending",
        )
        session.add(payment)
        session.commit()
        session.refresh(payment)

        # Notify User via real-time WebSocket
        try:
            publish_notification(
                user_id=user.id,
                event_type="payment_verification",
                title="Payment Submitted ⏳",
                message=f"Payment for {req.plan_name} ({ref[:18]}) submitted. Activation verification in progress.",
                link="#pricing",
                data={"payment_id": payment.id, "plan": req.plan_name, "status": "pending"}
            )
        except Exception:
            pass

        # Notify Admin
        try:
            from src.notifications.publisher import publish_admin_alert
            publish_admin_alert(
                title=f"💳 New Payment Submitted: #{payment.id}",
                message=f"User {user.email} submitted {req.plan_name} via {req.payment_method}. Ref: {ref}.",
                link="#admin"
            )
        except Exception:
            pass

        return {
            "ok": True,
            "message": "Payment reference submitted successfully! Your account will be upgraded immediately upon verification.",
            "payment_id": payment.id,
            "status": "pending"
        }


@app.get("/api/payments/my-payments")
def api_get_my_payments(request: Request):
    """User views their own payment and subscription history."""
    from src.auth.service import get_current_user
    user = get_current_user(request)
    from src.storage.models import SubscriptionPayment

    with get_session() as session:
        payments = session.scalars(
            select(SubscriptionPayment)
            .where(SubscriptionPayment.user_id == user.id)
            .order_by(desc(SubscriptionPayment.created_at))
        ).all()
        return {
            "ok": True,
            "current_plan": user.current_plan,
            "daily_apply_limit": user.daily_apply_limit,
            "plan_expires_at": _aware(user.plan_expires_at),
            "payments": [
                {
                    "id": p.id,
                    "plan": p.plan_name,
                    "amount_mad": p.amount_mad,
                    "amount_usd": p.amount_usd,
                    "currency": p.currency or "MAD",
                    "payment_method": p.payment_method,
                    "reference_code": p.reference_code,
                    "receipt_note": p.receipt_note,
                    "has_receipt": bool(p.receipt_image_path),
                    "receipt_url": f"/api/payments/receipt/{p.id}" if p.receipt_image_path else None,
                    "status": p.status,
                    "admin_notes": p.admin_notes,
                    "created_at": _aware(p.created_at),
                    "reviewed_at": _aware(p.reviewed_at),
                }
                for p in payments
            ]
        }


@app.get("/api/payments/receipt/{payment_id}")
def api_get_payment_receipt(payment_id: int, request: Request):
    """Securely serve uploaded receipt image to user or admin."""
    from src.auth.service import get_current_user
    user = get_current_user(request)
    from src.storage.models import SubscriptionPayment, UserRole

    with get_session() as session:
        payment = session.get(SubscriptionPayment, payment_id)
        if not payment or not payment.receipt_image_path:
            raise HTTPException(404, "Receipt not found")
        if payment.user_id != user.id and user.role != UserRole.ADMIN.value:
            raise HTTPException(403, "Access denied")
        p = Path(payment.receipt_image_path)
        if not p.exists():
            raise HTTPException(404, "Receipt file missing on server")
        return FileResponse(p)


@app.get("/api/admin/payments")
def api_admin_list_payments(request: Request):
    from src.auth.service import require_admin
    require_admin(request)
    from src.storage.models import SubscriptionPayment, User

    with get_session() as session:
        payments = session.scalars(
            select(SubscriptionPayment).order_by(desc(SubscriptionPayment.created_at))
        ).all()
        return {
            "ok": True,
            "payments": [
                {
                    "id": p.id,
                    "user_id": p.user_id,
                    "user_email": p.user.email if p.user else "Unknown",
                    "user_name": p.user.full_name if p.user else "Unknown",
                    "plan": p.plan_name,
                    "amount_mad": p.amount_mad,
                    "amount_usd": p.amount_usd,
                    "currency": p.currency or "MAD",
                    "payment_method": p.payment_method,
                    "reference_code": p.reference_code,
                    "receipt_note": p.receipt_note,
                    "has_receipt": bool(p.receipt_image_path),
                    "receipt_url": f"/api/payments/receipt/{p.id}" if p.receipt_image_path else None,
                    "status": p.status,
                    "admin_notes": p.admin_notes,
                    "created_at": _aware(p.created_at),
                    "reviewed_at": _aware(p.reviewed_at),
                }
                for p in payments
            ]
        }


@app.post("/api/admin/payments/{payment_id}/approve")
def api_admin_approve_payment(payment_id: int, request: Request, act: AdminPaymentAction = AdminPaymentAction()):
    from src.auth.service import require_admin
    admin = require_admin(request)
    from src.storage.models import SubscriptionPayment, User

    with get_session() as session:
        payment = session.get(SubscriptionPayment, payment_id)
        if not payment:
            raise HTTPException(404, "Payment record not found")

        payment.status = "approved"
        payment.reviewed_by = admin.id
        payment.reviewed_at = datetime.now(timezone.utc)
        if act.admin_notes:
            payment.admin_notes = act.admin_notes

        user = session.get(User, payment.user_id)
        if user:
            from src.storage.plans import get_effective_daily_limit
            limit = get_effective_daily_limit(session, payment.plan_name, fallback_limit=50)
            user.current_plan = payment.plan_name
            user.daily_apply_limit = limit
            user.plan_expires_at = datetime.now(timezone.utc) + timedelta(days=30)
            user.updated_at = datetime.now(timezone.utc)

        session.commit()
        logger.info("Payment #%s approved by Admin #%s: User %s upgraded to %s (%s apps/day)", payment.id, admin.id, user.email if user else "", payment.plan_name, user.daily_apply_limit if user else 5)

        try:
            publish_notification(
                event_type="payment_approved",
                title="Subscription Activated 🎉",
                message=f"Your subscription to {payment.plan_name} has been approved! Daily application quota upgraded to {user.daily_apply_limit if user else 5} jobs/day.",
                user_id=payment.user_id,
                link="#plans",
                data={"payment_id": payment.id, "plan": payment.plan_name}
            )
        except Exception as notif_err:
            logger.warning("Failed to publish payment approved notification: %s", notif_err)

        return {
            "ok": True,
            "message": f"Payment #{payment.id} approved! User {user.email if user else ''} upgraded to {payment.plan_name} ({user.daily_apply_limit if user else 5} apps/day)."
        }


@app.post("/api/admin/payments/{payment_id}/reject")
def api_admin_reject_payment(payment_id: int, request: Request, act: AdminPaymentAction = AdminPaymentAction()):
    from src.auth.service import require_admin
    admin = require_admin(request)
    from src.storage.models import SubscriptionPayment

    with get_session() as session:
        payment = session.get(SubscriptionPayment, payment_id)
        if not payment:
            raise HTTPException(404, "Payment record not found")

        payment.status = "rejected"
        payment.reviewed_by = admin.id
        payment.reviewed_at = datetime.now(timezone.utc)
        payment.admin_notes = act.admin_notes or "Payment verification declined."
        session.commit()

        try:
            publish_notification(
                event_type="payment_rejected",
                title="Payment Verification Notice",
                message=f"Payment #{payment.id} for {payment.plan_name} was declined: {payment.admin_notes}",
                user_id=payment.user_id,
                link="#plans",
                data={"payment_id": payment.id, "reason": payment.admin_notes}
            )
        except Exception as notif_err:
            logger.warning("Failed to publish payment rejected notification: %s", notif_err)

        return {"ok": True, "message": f"Payment #{payment.id} rejected."}


@app.delete("/api/admin/users/{user_id}")
def api_admin_delete_user(user_id: int, request: Request):
    """Admin: permanently delete a user and all their data (cascade)."""
    from src.auth.service import require_admin
    admin = require_admin(request)
    from src.storage.models import User, CandidateProfile, SubscriptionPayment, UserJobApplication, UserSetting, Notification

    if admin.id == user_id:
        raise HTTPException(400, "Cannot delete your own admin account")

    with get_session() as session:
        user = session.get(User, user_id)
        if not user:
            raise HTTPException(404, "User not found")

        # Cascade delete all related records
        session.query(Notification).filter(Notification.user_id == user_id).delete()
        session.query(UserSetting).filter(UserSetting.user_id == user_id).delete()
        session.query(UserJobApplication).filter(UserJobApplication.user_id == user_id).delete()
        session.query(SubscriptionPayment).filter(SubscriptionPayment.user_id == user_id).delete()
        session.query(CandidateProfile).filter(CandidateProfile.user_id == user_id).delete()
        session.delete(user)
        session.commit()

        logger.info("Admin #%s deleted user #%s (%s)", admin.id, user_id, user.email)
        return {"ok": True, "message": f"User {user.email} and all related data deleted."}


class AdminCreateUser(BaseModel):
    email: str
    full_name: str = ""
    password: str = ""
    role: str = "user"
    current_plan: str = "free"
    daily_apply_limit: int | None = None
    is_active: bool = True


@app.post("/api/admin/users")
def api_admin_create_user(req: AdminCreateUser, request: Request):
    """Admin: create a new user directly (bypass registration flow)."""
    from src.auth.service import require_admin, hash_password
    require_admin(request)
    from src.storage.models import User, UserRole

    email = req.email.strip().lower()
    if "@" not in email or "." not in email:
        raise HTTPException(400, "Invalid email address")

    with get_session() as session:
        existing = session.scalar(select(User).where(User.email == email))
        if existing:
            raise HTTPException(400, f"A user with email {email} already exists")

        from src.storage.plans import get_effective_daily_limit, get_plan_by_slug
        db_plan = get_plan_by_slug(session, req.current_plan)
        plan = db_plan.slug if db_plan else (req.current_plan or "free")
        if req.daily_apply_limit is not None:
            daily_limit = max(1, req.daily_apply_limit)
        else:
            daily_limit = db_plan.daily_apply_limit if db_plan else get_effective_daily_limit(session, plan, fallback_limit=5)

        role = req.role if req.role in ["admin", "user"] else "user"
        password = req.password or secrets.token_urlsafe(12)

        user = User(
            email=email,
            full_name=req.full_name.strip() or email.split("@")[0],
            password_hash=hash_password(password),
            role=role,
            is_active=req.is_active,
            is_verified=True,
            auth_provider="local",
            current_plan=plan,
            daily_apply_limit=daily_limit,
        )
        session.add(user)
        session.commit()

        logger.info("Admin created user: %s (role=%s, plan=%s)", email, role, plan)

        # Notify administrators only
        try:
            from src.notifications.publisher import publish_notification
            publish_notification(
                user_id=None,
                event_type="user_created",
                title=f"User Created: {user.email}",
                message=f"Admin created account for {user.email} (Role: {user.role}, Plan: {user.current_plan}, Limit: {user.daily_apply_limit} apps/day).",
                link="#admin",
                admin_only=True,
                data={
                    "user_id": user.id,
                    "email": user.email,
                    "role": user.role,
                    "current_plan": user.current_plan,
                    "daily_apply_limit": user.daily_apply_limit,
                    "admin_only": True,
                },
            )
        except Exception as notif_err:
            logger.warning("Failed to publish user_created notification: %s", notif_err)

        return {
            "ok": True,
            "message": f"User {email} created successfully",
            "user": {
                "id": user.id,
                "email": user.email,
                "full_name": user.full_name,
                "role": user.role,
                "current_plan": user.current_plan,
                "daily_apply_limit": user.daily_apply_limit,
            },
            "generated_password": password if not req.password else None,
        }


@app.get("/api/admin/users/{user_id}")
def api_admin_get_user(user_id: int, request: Request):
    """Admin: get full details of a single user including settings and profiles."""
    from src.auth.service import require_admin
    require_admin(request)
    from src.storage.models import User, CandidateProfile, UserSetting, UserJobApplication, SubscriptionPayment

    with get_session() as session:
        user = session.get(User, user_id)
        if not user:
            raise HTTPException(404, "User not found")

        today_start = datetime.now(timezone.utc).replace(hour=0, minute=0, second=0, microsecond=0)
        apps_today = session.scalar(
            select(func.count(UserJobApplication.id)).where(
                UserJobApplication.user_id == user_id,
                UserJobApplication.applied_at >= today_start,
            )
        ) or 0

        profiles = session.scalars(
            select(CandidateProfile).where(CandidateProfile.user_id == user_id)
        ).all()

        user_setting = session.scalar(
            select(UserSetting).where(UserSetting.user_id == user_id)
        )

        payments = session.scalars(
            select(SubscriptionPayment).where(SubscriptionPayment.user_id == user_id)
            .order_by(desc(SubscriptionPayment.created_at))
        ).all()

        return {
            "ok": True,
            "user": {
                "id": user.id,
                "email": user.email,
                "full_name": user.full_name,
                "role": user.role,
                "is_active": user.is_active,
                "is_verified": user.is_verified,
                "auth_provider": user.auth_provider,
                "current_plan": user.current_plan,
                "daily_apply_limit": user.daily_apply_limit,
                "plan_expires_at": _aware(user.plan_expires_at),
                "applications_today": apps_today,
                "applications_remaining": max(0, user.daily_apply_limit - apps_today),
                "created_at": _aware(user.created_at),
                "updated_at": _aware(user.updated_at),
            },
            "profiles": [
                {
                    "id": p.id,
                    "name": p.name,
                    "is_active": p.is_active,
                    "headline": p.headline,
                    "core_stack": p.core_stack,
                }
                for p in profiles
            ],
            "settings": {
                "sender_email": user_setting.sender_email if user_setting else None,
                "smtp_host": user_setting.smtp_host if user_setting else None,
                "telegram_chat_id": user_setting.telegram_chat_id if user_setting else None,
                "min_match_score": user_setting.min_match_score if user_setting else None,
                "auto_apply_mode": user_setting.auto_apply_mode if user_setting else None,
            } if user_setting else None,
            "payments": [
                {
                    "id": pay.id,
                    "plan": pay.plan_name,
                    "amount_mad": pay.amount_mad,
                    "status": pay.status,
                    "created_at": _aware(pay.created_at),
                }
                for pay in payments
            ],
        }


@app.delete("/api/admin/payments/{payment_id}")
def api_admin_delete_payment(payment_id: int, request: Request):
    """Admin: permanently delete a payment record."""
    from src.auth.service import require_admin
    require_admin(request)
    from src.storage.models import SubscriptionPayment

    with get_session() as session:
        payment = session.get(SubscriptionPayment, payment_id)
        if not payment:
            raise HTTPException(404, "Payment record not found")
        session.delete(payment)
        session.commit()
        return {"ok": True, "message": f"Payment #{payment_id} deleted."}


class AdminPaymentEdit(BaseModel):
    plan_name: str | None = None
    amount_mad: int | None = None
    payment_method: str | None = None
    reference_code: str | None = None
    status: str | None = None
    admin_notes: str | None = None


@app.patch("/api/admin/payments/{payment_id}")
def api_admin_edit_payment(payment_id: int, req: AdminPaymentEdit, request: Request):
    """Admin: edit payment details (status, notes, plan, amount, etc.)."""
    from src.auth.service import require_admin
    admin = require_admin(request)
    from src.storage.models import SubscriptionPayment

    with get_session() as session:
        payment = session.get(SubscriptionPayment, payment_id)
        if not payment:
            raise HTTPException(404, "Payment record not found")

        if req.plan_name:
            payment.plan_name = req.plan_name.strip()
        if req.amount_mad is not None:
            payment.amount_mad = req.amount_mad
        if req.payment_method:
            payment.payment_method = req.payment_method
        if req.reference_code:
            payment.reference_code = req.reference_code.strip()
        if req.status and req.status in ["pending", "approved", "rejected"]:
            payment.status = req.status
            payment.reviewed_by = admin.id
            payment.reviewed_at = datetime.now(timezone.utc)
        if req.admin_notes is not None:
            payment.admin_notes = req.admin_notes

        # If payment is approved, keep user's active plan and daily quota synchronized
        if payment.status == "approved" and payment.user_id:
            from src.storage.models import User
            user = session.get(User, payment.user_id)
            if user:
                from src.storage.plans import get_effective_daily_limit
                limit = get_effective_daily_limit(session, payment.plan_name, fallback_limit=50)
                user.current_plan = payment.plan_name
                user.daily_apply_limit = limit
                user.updated_at = datetime.now(timezone.utc)

        payment.updated_at = datetime.now(timezone.utc)
        session.commit()
        return {"ok": True, "message": f"Payment #{payment_id} updated."}


# ---------------------------------------------------------------------------
# Admin Plans, Prices, Quotas & Dynamic Payment Gateways Management
# ---------------------------------------------------------------------------

@app.get("/api/admin/plans")
def api_admin_list_plans(request: Request):
    """Admin: retrieve all subscription plans, quotas, prices, and subscriber counts."""
    from src.auth.service import require_admin
    require_admin(request)
    from src.storage.plans import get_all_plans_db, resolve_plan_slug
    from src.storage.models import User
    import json

    with get_session() as session:
        plans = get_all_plans_db(session, include_inactive=True)
        users = session.scalars(select(User)).all()
        counts: dict[str, int] = {}
        for u in users:
            s = resolve_plan_slug(u.current_plan)
            counts[s] = counts.get(s, 0) + 1
            exact = (u.current_plan or "").lower()
            if exact != s:
                counts[exact] = counts.get(exact, 0) + 1

        result = []
        for p in plans:
            try:
                feats = json.loads(p.features_json or "[]")
            except Exception:
                feats = []
            sub_count = counts.get(p.slug.lower(), 0)
            result.append({
                "id": p.id,
                "slug": p.slug,
                "name": p.name,
                "badge": p.badge or "",
                "description": p.description or "",
                "price_usd": p.price_usd,
                "price_mad": p.price_mad,
                "price_eur": p.price_eur,
                "price_usdt": p.price_usdt,
                "billing_interval": p.billing_interval,
                "daily_apply_limit": p.daily_apply_limit,
                "max_ai_calls_per_day": p.max_ai_calls_per_day,
                "can_access_freelance": p.can_access_freelance,
                "is_active": p.is_active,
                "is_recommended": p.is_recommended,
                "features": feats,
                "sort_order": p.sort_order,
                "subscriber_count": sub_count,
                "created_at": _aware(p.created_at),
                "updated_at": _aware(p.updated_at),
            })
        return {"ok": True, "plans": result}


@app.post("/api/admin/plans")
def api_admin_create_plan(req: AdminPlanCreate, request: Request):
    """Admin: create a brand new plan with customized pricing and quota limits."""
    from src.auth.service import require_admin
    admin = require_admin(request)
    from src.storage.models import PlatformPlan
    import json

    slug = req.slug.strip().lower().replace(" ", "_")
    if not slug:
        raise HTTPException(400, "Plan slug is required")

    with get_session() as session:
        existing = session.scalar(select(PlatformPlan).where(PlatformPlan.slug == slug))
        if existing:
            raise HTTPException(400, f"A plan with slug '{slug}' already exists")

        plan = PlatformPlan(
            slug=slug,
            name=req.name.strip() or slug.capitalize(),
            badge=req.badge.strip() if req.badge else None,
            description=req.description.strip() if req.description else None,
            price_usd=max(0.0, float(req.price_usd)),
            price_mad=max(0, int(req.price_mad)),
            price_eur=max(0.0, float(req.price_eur)),
            price_usdt=max(0.0, float(req.price_usdt)),
            billing_interval=req.billing_interval.strip() or "/ month",
            daily_apply_limit=max(1, int(req.daily_apply_limit)),
            max_ai_calls_per_day=max(1, int(req.max_ai_calls_per_day)),
            can_access_freelance=bool(req.can_access_freelance),
            is_active=bool(req.is_active),
            is_recommended=bool(req.is_recommended),
            features_json=json.dumps(req.features or []),
            sort_order=int(req.sort_order),
        )
        session.add(plan)
        session.commit()
        session.refresh(plan)

        synced_count = 0
        if req.sync_users:
            from src.storage.plans import sync_plan_quota_to_users
            synced_count = sync_plan_quota_to_users(session, plan.slug, plan.daily_apply_limit)

        logger.info("Admin #%s created new plan '%s' (MAD: %s, Limit: %s, Synced: %d)", admin.id, plan.slug, plan.price_mad, plan.daily_apply_limit, synced_count)
        return {
            "ok": True,
            "message": f"Plan '{plan.name}' created successfully!" + (f" ({synced_count} subscriber quotas synced)" if synced_count else ""),
            "plan_id": plan.id,
            "synced_users": synced_count,
        }



@app.put("/api/admin/plans/{plan_id}")
def api_admin_update_plan(plan_id: int, req: AdminPlanUpdate, request: Request):
    """Admin: update plan prices, quotas, and instantly sync quotas to all existing subscribers."""
    from src.auth.service import require_admin
    admin = require_admin(request)
    from src.storage.models import PlatformPlan
    from src.storage.plans import sync_plan_quota_to_users
    import json

    with get_session() as session:
        plan = session.get(PlatformPlan, plan_id)
        if not plan:
            raise HTTPException(404, "Plan not found")

        old_limit = plan.daily_apply_limit
        if req.name is not None:
            plan.name = req.name.strip()
        if req.badge is not None:
            plan.badge = req.badge.strip() or None
        if req.description is not None:
            plan.description = req.description.strip() or None
        if req.price_usd is not None:
            plan.price_usd = max(0.0, float(req.price_usd))
        if req.price_mad is not None:
            plan.price_mad = max(0, int(req.price_mad))
        if req.price_eur is not None:
            plan.price_eur = max(0.0, float(req.price_eur))
        if req.price_usdt is not None:
            plan.price_usdt = max(0.0, float(req.price_usdt))
        if req.billing_interval is not None:
            plan.billing_interval = req.billing_interval.strip() or "/ month"
        if req.daily_apply_limit is not None:
            plan.daily_apply_limit = max(1, int(req.daily_apply_limit))
        if req.max_ai_calls_per_day is not None:
            plan.max_ai_calls_per_day = max(1, int(req.max_ai_calls_per_day))
        if req.can_access_freelance is not None:
            plan.can_access_freelance = bool(req.can_access_freelance)
        if req.is_active is not None:
            plan.is_active = bool(req.is_active)
        if req.is_recommended is not None:
            plan.is_recommended = bool(req.is_recommended)
        if req.features is not None:
            plan.features_json = json.dumps(req.features)
        if req.sort_order is not None:
            plan.sort_order = int(req.sort_order)

        plan.updated_at = datetime.now(timezone.utc)
        session.commit()

        synced_count = 0
        if req.sync_users and plan.daily_apply_limit != old_limit:
            synced_count = sync_plan_quota_to_users(session, plan.slug, plan.daily_apply_limit)

        logger.info(
            "Admin #%s updated plan '%s' (MAD: %s, USD: %s, Limit: %s, Synced Users: %d)",
            admin.id, plan.slug, plan.price_mad, plan.price_usd, plan.daily_apply_limit, synced_count
        )
        return {
            "ok": True,
            "message": f"Plan '{plan.name}' updated successfully! ({synced_count} subscriber quotas updated immediately)",
            "synced_users": synced_count,
        }


@app.post("/api/admin/plans/{plan_id}/sync-quotas")
def api_admin_sync_plan_quotas(plan_id: int, request: Request):
    """Admin: manually sync this plan's daily limit to all users currently subscribed."""
    from src.auth.service import require_admin
    admin = require_admin(request)
    from src.storage.models import PlatformPlan
    from src.storage.plans import sync_plan_quota_to_users

    with get_session() as session:
        plan = session.get(PlatformPlan, plan_id)
        if not plan:
            raise HTTPException(404, "Plan not found")

        synced = sync_plan_quota_to_users(session, plan.slug, plan.daily_apply_limit)
        return {
            "ok": True,
            "message": f"Updated daily limit of {plan.daily_apply_limit} apps/day to {synced} users on plan '{plan.slug}'.",
            "synced_users": synced,
        }


@app.delete("/api/admin/plans/{plan_id}")
def api_admin_delete_plan(plan_id: int, request: Request):
    """Admin: delete or deactivate plan."""
    from src.auth.service import require_admin
    admin = require_admin(request)
    from src.storage.models import PlatformPlan, User
    from src.storage.plans import resolve_plan_slug

    with get_session() as session:
        plan = session.get(PlatformPlan, plan_id)
        if not plan:
            raise HTTPException(404, "Plan not found")

        user_count = session.query(User).filter(User.current_plan.in_([plan.slug, resolve_plan_slug(plan.slug)])).count()
        if user_count > 0:
            plan.is_active = False
            plan.updated_at = datetime.now(timezone.utc)
            session.commit()
            return {"ok": True, "message": f"Plan '{plan.name}' has {user_count} active subscribers, so it was deactivated instead of deleted."}

        session.delete(plan)
        session.commit()
        logger.info("Admin #%s deleted plan #%s (%s)", admin.id, plan_id, plan.slug)
        return {"ok": True, "message": f"Plan '{plan.slug}' permanently deleted."}


@app.get("/api/admin/gateways")
def api_admin_list_gateways(request: Request):
    """Admin: list all configured payment gateways with credentials."""
    from src.auth.service import require_admin
    require_admin(request)
    from src.storage.models import PaymentGatewayConfig
    from src.storage.plans import seed_default_plans_and_gateways
    import json

    with get_session() as session:
        seed_default_plans_and_gateways(session)
        gws = session.scalars(select(PaymentGatewayConfig).order_by(PaymentGatewayConfig.sort_order.asc())).all()
        result = []
        for g in gws:
            try:
                cfg = json.loads(g.config_json or "{}")
            except Exception:
                cfg = {}
            result.append({
                "id": g.id,
                "gateway_key": g.gateway_key,
                "name": g.name,
                "category": g.category,
                "is_enabled": g.is_enabled,
                "instructions": g.instructions or "",
                "config": cfg,
                "sort_order": g.sort_order,
                "updated_at": _aware(g.updated_at),
            })
        return {"ok": True, "gateways": result}


@app.put("/api/admin/gateways/{gateway_key}")
def api_admin_update_gateway(gateway_key: str, req: AdminGatewayUpdate, request: Request):
    """Admin: update payment gateway live credentials, enable/disable toggle and instructions."""
    from src.auth.service import require_admin
    admin = require_admin(request)
    from src.storage.models import PaymentGatewayConfig
    from src.storage.plans import seed_default_plans_and_gateways
    import json

    with get_session() as session:
        seed_default_plans_and_gateways(session)
        gw = session.scalar(select(PaymentGatewayConfig).where(PaymentGatewayConfig.gateway_key == gateway_key))
        if not gw:
            raise HTTPException(404, f"Gateway '{gateway_key}' not found")

        if req.name is not None:
            gw.name = req.name.strip()
        if req.category is not None:
            gw.category = req.category.strip()
        if req.is_enabled is not None:
            gw.is_enabled = bool(req.is_enabled)
        if req.instructions is not None:
            gw.instructions = req.instructions.strip()
        if req.sort_order is not None:
            gw.sort_order = int(req.sort_order)
        if req.config is not None:
            try:
                cur = json.loads(gw.config_json or "{}")
            except Exception:
                cur = {}
            cur.update(req.config)
            gw.config_json = json.dumps(cur)

        gw.updated_at = datetime.now(timezone.utc)
        session.commit()

        logger.info("Admin #%s updated gateway '%s' (enabled=%s)", admin.id, gateway_key, gw.is_enabled)
        return {"ok": True, "message": f"Gateway '{gw.name}' updated successfully."}


class StripeTestRequest(BaseModel):
    secret_key: str | None = None


StripeTestRequest.model_rebuild()


@app.post("/api/admin/gateways/test-stripe")
def api_admin_test_stripe(request: Request, req: StripeTestRequest = StripeTestRequest()):
    """Admin: test connection to Stripe API with provided or stored secret key."""
    from src.auth.service import require_admin
    require_admin(request)
    from src.billing.stripe_service import test_stripe_credentials, get_stripe_config

    key = (req.secret_key or "").strip()
    if not key:
        cfg = get_stripe_config()
        key = cfg.get("secret_key", "").strip()

    if not key:
        raise HTTPException(400, "No Stripe secret key provided or saved in settings.")

    ok, msg = test_stripe_credentials(key)
    return {"ok": ok, "message": msg}


# ---------------------------------------------------------------------------
# Stripe Checkout & Automation Endpoints
# ---------------------------------------------------------------------------

@app.post("/api/payments/stripe/create-checkout-session")
def api_payments_stripe_create_session(req: StripeSessionRequest, request: Request):
    """Create a live Stripe Checkout Session for instant automated card payments."""
    from src.auth.service import get_current_user
    user = get_current_user(request)
    from src.billing.stripe_service import create_stripe_checkout_session

    base_url = str(request.base_url).rstrip("/")
    success_url = req.success_url or f"{base_url}/#payment_success?session_id={{CHECKOUT_SESSION_ID}}"
    cancel_url = req.cancel_url or f"{base_url}/#pricing"

    try:
        data = create_stripe_checkout_session(
            user_id=user.id,
            user_email=user.email,
            plan_slug=req.plan_slug,
            success_url=success_url,
            cancel_url=cancel_url,
        )
        return data
    except Exception as exc:
        raise HTTPException(400, str(exc))


@app.get("/api/payments/stripe/verify-session/{session_id}")
def api_payments_stripe_verify_session(session_id: str, request: Request):
    """Verify completed Stripe session and activate user account immediately."""
    from src.auth.service import get_current_user
    get_current_user(request)
    from src.billing.stripe_service import process_stripe_session_completed

    success = process_stripe_session_completed(session_id)
    if success:
        return {"ok": True, "message": "Payment verified and subscription activated successfully! 🎉"}
    return {"ok": False, "message": "Payment verification is still processing or session not completed."}


@app.post("/api/payments/stripe/webhook")
async def api_payments_stripe_webhook(request: Request):
    """Handle incoming Stripe webhooks (checkout.session.completed)."""
    from src.billing.stripe_service import process_stripe_session_completed
    try:
        body = await request.json()
        event_type = body.get("type")
        if event_type == "checkout.session.completed":
            session_obj = body.get("data", {}).get("object", {})
            session_id = session_obj.get("id")
            if session_id:
                process_stripe_session_completed(session_id)
        return {"status": "success"}
    except Exception as exc:
        logger.warning("Stripe webhook processing exception: %s", exc)
        return {"status": "ignored"}


# ---------------------------------------------------------------------------
# User Self-Service Settings & Environment Configuration
# ---------------------------------------------------------------------------

class UserSettingsUpdate(BaseModel):
    sender_email: str | None = None
    sender_name: str | None = None
    smtp_host: str | None = None
    smtp_port: int | None = None
    smtp_password: str | None = None
    imap_host: str | None = None
    imap_port: int | None = None
    imap_password: str | None = None
    phone_number: str | None = None
    linkedin_url: str | None = None
    github_url: str | None = None
    portfolio_url: str | None = None
    min_match_score: int | None = None
    auto_apply_mode: str | None = None
    auto_apply_min_score: int | None = None
    telegram_bot_token: str | None = None
    telegram_chat_id: str | None = None
    alert_email: str | None = None
    linkedin_cookie: str | None = None
    indeed_cookie: str | None = None
    auto_apply_linkedin_enabled: bool | None = None
    auto_apply_indeed_enabled: bool | None = None
    custom_env_json: str | None = None


@app.get("/api/user/settings")
def api_user_get_settings(request: Request):
    """Get current user's platform settings (SMTP, IMAP, alerts, custom env vars)."""
    from src.auth.service import get_current_user
    user = get_current_user(request)
    from src.storage.models import UserSetting

    with get_session() as session:
        us = session.scalar(select(UserSetting).where(UserSetting.user_id == user.id))
        if not us:
            return {
                "ok": True,
                "settings": None,
                "message": "No custom settings configured yet. System defaults are in use.",
            }

        custom_env = {}
        if us.custom_env_json:
            try:
                custom_env = json.loads(us.custom_env_json)
            except Exception:
                custom_env = {}

        return {
            "ok": True,
            "settings": {
                "sender_email": us.sender_email,
                "sender_name": us.sender_name,
                "smtp_host": us.smtp_host,
                "smtp_port": us.smtp_port,
                "smtp_password": "••••••••" if us.smtp_password else None,
                "imap_host": us.imap_host,
                "imap_port": us.imap_port,
                "imap_password": "••••••••" if us.imap_password else None,
                "phone_number": us.phone_number,
                "linkedin_url": us.linkedin_url,
                "github_url": us.github_url,
                "portfolio_url": us.portfolio_url,
                "min_match_score": us.min_match_score,
                "auto_apply_mode": us.auto_apply_mode,
                "auto_apply_min_score": us.auto_apply_min_score,
                "telegram_bot_token": "••••••••" if us.telegram_bot_token else None,
                "telegram_chat_id": us.telegram_chat_id,
                "alert_email": us.alert_email,
                "linkedin_cookie": "••••••••" if custom_env.get("LINKEDIN_COOKIE") else None,
                "indeed_cookie": "••••••••" if custom_env.get("INDEED_COOKIE") else None,
                "auto_apply_linkedin_enabled": custom_env.get("AUTO_APPLY_LINKEDIN_ENABLED", True),
                "auto_apply_indeed_enabled": custom_env.get("AUTO_APPLY_INDEED_ENABLED", True),
                "custom_env": custom_env,
            }
        }


@app.put("/api/user/settings")
def api_user_update_settings(req: UserSettingsUpdate, request: Request):
    """Create or update current user's platform settings (upsert)."""
    from src.auth.service import get_current_user
    user = get_current_user(request)
    from src.storage.models import UserSetting

    with get_session() as session:
        us = session.scalar(select(UserSetting).where(UserSetting.user_id == user.id))
        if not us:
            us = UserSetting(user_id=user.id)
            session.add(us)

        # Update only provided fields (None means "don't change")
        updatable = [
            "sender_email", "sender_name", "smtp_host", "smtp_port", "smtp_password",
            "imap_host", "imap_port", "imap_password",
            "phone_number", "linkedin_url", "github_url", "portfolio_url",
            "min_match_score", "auto_apply_mode", "auto_apply_min_score",
            "telegram_bot_token", "telegram_chat_id", "alert_email",
        ]
        for field in updatable:
            val = getattr(req, field, None)
            if val is not None:
                setattr(us, field, val)

        custom_env = {}
        if us.custom_env_json:
            try:
                custom_env = json.loads(us.custom_env_json)
            except Exception:
                custom_env = {}
        if req.custom_env_json:
            try:
                custom_env.update(json.loads(req.custom_env_json))
            except Exception:
                pass
        if req.linkedin_cookie is not None:
            custom_env["LINKEDIN_COOKIE"] = req.linkedin_cookie
        if req.indeed_cookie is not None:
            custom_env["INDEED_COOKIE"] = req.indeed_cookie
        if req.auto_apply_linkedin_enabled is not None:
            custom_env["AUTO_APPLY_LINKEDIN_ENABLED"] = req.auto_apply_linkedin_enabled
        if req.auto_apply_indeed_enabled is not None:
            custom_env["AUTO_APPLY_INDEED_ENABLED"] = req.auto_apply_indeed_enabled
        us.custom_env_json = json.dumps(custom_env)

        us.updated_at = datetime.now(timezone.utc)
        session.commit()

        logger.info("User #%s updated their platform settings", user.id)
        return {"ok": True, "message": "Settings updated successfully."}


@app.get("/api/user/settings/env")
def api_user_get_env(request: Request):
    """Get only the custom environment variable key-value pairs for the current user."""
    from src.auth.service import get_current_user
    user = get_current_user(request)
    from src.storage.models import UserSetting

    with get_session() as session:
        us = session.scalar(select(UserSetting).where(UserSetting.user_id == user.id))
        custom_env = {}
        if us and us.custom_env_json:
            try:
                custom_env = json.loads(us.custom_env_json)
            except Exception:
                custom_env = {}
        return {"ok": True, "custom_env": custom_env}


class CustomEnvUpdate(BaseModel):
    env_vars: dict


@app.put("/api/user/settings/env")
def api_user_update_env(req: CustomEnvUpdate, request: Request):
    """Update custom environment variables for the current user (complete replacement)."""
    from src.auth.service import get_current_user
    user = get_current_user(request)
    from src.storage.models import UserSetting

    # Validate all keys/values are strings
    for k, v in req.env_vars.items():
        if not isinstance(k, str) or not isinstance(v, (str, int, float, bool)):
            raise HTTPException(400, f"Invalid env var: {k}={v}. Keys must be strings, values must be scalars.")

    with get_session() as session:
        us = session.scalar(select(UserSetting).where(UserSetting.user_id == user.id))
        if not us:
            us = UserSetting(user_id=user.id)
            session.add(us)

        us.custom_env_json = json.dumps(req.env_vars, ensure_ascii=False)
        us.updated_at = datetime.now(timezone.utc)
        session.commit()

        return {"ok": True, "message": f"Custom env vars updated ({len(req.env_vars)} variables)."}


# ---------------------------------------------------------------------------
# First-Time User Setup & Onboarding Wizard APIs
# ---------------------------------------------------------------------------

class QuickOnboardRequest(BaseModel):
    name: Optional[str] = None
    headline: Optional[str] = None
    target_titles: Optional[list[str]] = None
    core_stack: Optional[list[str]] = None
    target_locations: Optional[list[str]] = None
    experience_years: Optional[int] = None
    resume_text: Optional[str] = None
    sender_name: Optional[str] = None
    sender_email: Optional[str] = None
    smtp_host: Optional[str] = None
    smtp_port: Optional[int] = None
    smtp_password: Optional[str] = None
    alert_email: Optional[str] = None
    min_match_score: Optional[int] = None
    auto_apply_mode: Optional[str] = None


class CvQuickParseRequest(BaseModel):
    file_base64: Optional[str] = None
    filename: Optional[str] = None
    raw_text: Optional[str] = None


@app.get("/api/user/setup_status")
def api_user_get_setup_status(request: Request):
    """Calculate the user's readiness score, missing environments, and onboarding checklist."""
    from src.auth.service import get_current_user
    user = get_current_user(request)
    from src.storage.models import UserSetting, CandidateProfile, UserJobApplication

    with get_session() as session:
        # Check active or user profile
        prof = session.scalar(
            select(CandidateProfile)
            .where(CandidateProfile.user_id == user.id)
            .order_by(desc(CandidateProfile.is_active), desc(CandidateProfile.id))
        )
        if not prof and user.role == "admin":
            prof = session.scalar(select(CandidateProfile).order_by(desc(CandidateProfile.is_active), desc(CandidateProfile.id)))

        # Check user settings
        us = session.scalar(select(UserSetting).where(UserSetting.user_id == user.id))

        # Check application history for first-time status
        apps_count = session.scalar(
            select(func.count(UserJobApplication.id)).where(UserJobApplication.user_id == user.id)
        ) or 0

        def _get_list(raw):
            if not raw: return []
            if isinstance(raw, list): return raw
            try: return json.loads(raw)
            except Exception: return []

        titles = _get_list(prof.target_titles) if prof else []
        stack = _get_list(prof.core_stack) if prof else []
        locations = _get_list(prof.target_locations) if prof else []
        has_titles = bool(titles and len(titles) > 0)
        has_stack = bool(stack and len(stack) > 0)
        has_resume = bool(prof and ((prof.resume_text and len(prof.resume_text.strip()) > 30) or bool(prof.resume_path)))
        has_smtp_email = bool(us and us.sender_email)
        has_smtp_pass = bool(us and us.smtp_password)
        has_locations = bool(locations and len(locations) > 0)

        # 4 Core Pillars for Automation:
        # 1. Target Titles (25%)
        # 2. Tech Stack (25%)
        # 3. Resume / CV text (25%)
        # 4. Outbound Email / SMTP (25%)
        score = 0
        if has_titles: score += 25
        if has_stack: score += 25
        if has_resume: score += 25
        if has_smtp_email and has_smtp_pass: score += 25
        elif has_smtp_email: score += 15

        missing = []
        if not has_titles:
            missing.append({
                "id": "titles",
                "title": "Target Job Titles",
                "desc": "Specify roles you want to target (e.g. Fullstack Developer, Backend Engineer)"
            })
        if not has_stack:
            missing.append({
                "id": "stack",
                "title": "Core Tech Stack",
                "desc": "Add your primary skills & frameworks (e.g. React, Python, Docker)"
            })
        if not has_resume:
            missing.append({
                "id": "resume",
                "title": "CV & Resume Studio",
                "desc": "Upload your CV PDF/DOCX or paste resume text for AI matching"
            })
        if not (has_smtp_email and has_smtp_pass):
            missing.append({
                "id": "smtp",
                "title": "Outbound Email Credentials",
                "desc": "Configure your sender email & SMTP App Password to dispatch applications"
            })

        is_first_time = (apps_count == 0 and score < 100)

        return {
            "ok": True,
            "completion_percent": score,
            "is_complete": score >= 100,
            "is_first_time": is_first_time,
            "is_ready_for_automation": has_titles and has_stack and (has_resume or bool(prof and prof.resume_text)),
            "checklist": {
                "titles": has_titles,
                "stack": has_stack,
                "resume": has_resume,
                "smtp": has_smtp_email and has_smtp_pass,
                "locations": has_locations,
            },
            "missing_items": missing,
            "profile": {
                "name": prof.name if prof else (user.full_name or "Candidate"),
                "headline": prof.headline if prof else "Software Engineer",
                "target_titles": titles if titles else ["Fullstack Developer", "Backend Engineer"],
                "core_stack": stack if stack else ["Python", "JavaScript", "React"],
                "target_locations": locations if locations else ["Remote"],
                "experience_years": prof.experience_years if prof else 3,
                "resume_text": prof.resume_text if prof else "",
            },
            "settings": {
                "sender_name": us.sender_name if us else (user.full_name or ""),
                "sender_email": us.sender_email if us else (user.email or ""),
                "smtp_host": us.smtp_host if us else "smtp.gmail.com",
                "smtp_port": us.smtp_port if us else 587,
                "has_smtp_password": bool(us and us.smtp_password),
                "alert_email": us.alert_email if us else (user.email or ""),
                "min_match_score": us.min_match_score if us else 65,
                "auto_apply_mode": us.auto_apply_mode if us else "draft",
            }
        }


@app.post("/api/user/quick_onboard")
def api_user_quick_onboard(req: QuickOnboardRequest, request: Request):
    """Atomic multi-step setup saver from the onboarding wizard."""
    from src.auth.service import get_current_user
    user = get_current_user(request)
    from src.storage.models import UserSetting, CandidateProfile

    with get_session() as session:
        # CandidateProfile upsert
        prof = session.scalar(
            select(CandidateProfile)
            .where(CandidateProfile.user_id == user.id)
            .order_by(desc(CandidateProfile.is_active), desc(CandidateProfile.id))
        )
        if not prof:
            prof = CandidateProfile(
                user_id=user.id,
                name=req.name or user.full_name or "My Profile",
                is_active=True,
                headline=req.headline or "Software Engineer",
                target_titles=json.dumps(req.target_titles or ["Fullstack Developer"]),
                core_stack=json.dumps(req.core_stack or ["Python", "JavaScript"]),
                target_locations=json.dumps(req.target_locations or ["Remote"]),
                experience_years=req.experience_years or 3,
                resume_text=req.resume_text or "",
            )
            session.add(prof)
        else:
            if req.name: prof.name = req.name
            if req.headline: prof.headline = req.headline
            if req.target_titles is not None: prof.target_titles = json.dumps(req.target_titles)
            if req.core_stack is not None: prof.core_stack = json.dumps(req.core_stack)
            if req.target_locations is not None: prof.target_locations = json.dumps(req.target_locations)
            if req.experience_years is not None: prof.experience_years = req.experience_years
            if req.resume_text is not None: prof.resume_text = req.resume_text
            prof.is_active = True
            prof.updated_at = datetime.now(timezone.utc)

        # UserSetting upsert
        us = session.scalar(select(UserSetting).where(UserSetting.user_id == user.id))
        if not us:
            us = UserSetting(user_id=user.id)
            session.add(us)

        if req.sender_name is not None: us.sender_name = req.sender_name
        if req.sender_email is not None: us.sender_email = req.sender_email
        if req.smtp_host is not None: us.smtp_host = req.smtp_host
        if req.smtp_port is not None: us.smtp_port = req.smtp_port
        if req.smtp_password is not None and req.smtp_password.strip(): us.smtp_password = req.smtp_password.strip()
        if req.alert_email is not None: us.alert_email = req.alert_email
        if req.min_match_score is not None: us.min_match_score = req.min_match_score
        if req.auto_apply_mode is not None: us.auto_apply_mode = req.auto_apply_mode
        us.updated_at = datetime.now(timezone.utc)

        session.commit()
        return {"ok": True, "message": "Onboarding information saved successfully! 🚀"}


@app.post("/api/candidate/cv/quick_parse")
def api_candidate_quick_parse(req: CvQuickParseRequest, request: Request):
    """Extract and analyze CV from uploaded base64 PDF or pasted text in real time."""
    from src.candidate.cv_ml_engine import analyze_cv_content, extract_text_from_pdf
    import base64
    from pathlib import Path

    extracted_text = ""
    if req.raw_text and len(req.raw_text.strip()) > 10:
        extracted_text = req.raw_text.strip()
    elif req.file_base64:
        try:
            raw_b64 = req.file_base64.split(",")[-1]
            content = base64.b64decode(raw_b64)
            filename = (req.filename or "cv.pdf").lower()
            if filename.endswith(".pdf"):
                temp_dir = Path("candidate_data/temp_uploads")
                temp_dir.mkdir(parents=True, exist_ok=True)
                temp_path = temp_dir / f"temp_{int(datetime.now().timestamp())}.pdf"
                temp_path.write_bytes(content)
                extracted_text = extract_text_from_pdf(temp_path)
                try:
                    temp_path.unlink()
                except Exception:
                    pass
            else:
                extracted_text = content.decode("utf-8", errors="ignore")
        except Exception as e:
            logger.warning("Error parsing CV base64 upload: %s", e)

    analysis = analyze_cv_content(extracted_text) if extracted_text else {}
    return {
        "ok": True,
        "extracted_text": extracted_text,
        "analysis": analysis,
    }


@app.get("/api/admin/logs")
def api_admin_get_logs(request: Request, level: str = "ALL", q: str = "", limit: int = 150):
    from src.auth.service import require_admin
    require_admin(request)
    from src.dashboard.log_capture import system_log_buffer

    logs = system_log_buffer.get_logs(level=level, query=q, limit=limit)
    formatted = []
    for l in logs:
        ts = l["timestamp"]
        time_part = ts.split("T")[1][:8] if "T" in ts else ts
        formatted.append({
            "timestamp": ts,
            "time": time_part,
            "level": l["level"],
            "logger": l["logger"],
            "message": l["message"],
        })
    return {
        "ok": True,
        "count": len(formatted),
        "level": level,
        "logs": formatted
    }


class AdminTestAlertRequest(BaseModel):
    level: str = "critical"  # "critical", "security", "maintenance"
    title: str = "Test System Alert"
    message: str = "This is a test notification dispatched strictly to administrators."


@app.post("/api/admin/test_alert")
def api_admin_test_alert(req: AdminTestAlertRequest, request: Request):
    """Publish a real-time test security or maintenance alert strictly to administrators."""
    from src.auth.service import require_admin
    require_admin(request)
    from src.notifications.publisher import publish_admin_alert
    notif_id = publish_admin_alert(
        title=req.title,
        message=req.message,
        level=req.level,
        link="#admin",
        data={"test": True, "level": req.level},
    )
    return {"ok": True, "notification_id": notif_id, "message": "Alert published to administrators"}


# ---------------------------------------------------------------------------
# Real-Time Notifications (WebSocket & REST API)
# ---------------------------------------------------------------------------

@app.websocket("/ws/notifications")
async def ws_notifications(websocket: WebSocket, token: str | None = None):
    """Real-time WebSocket notification endpoint with automatic reconnection and token auth."""
    uid: int | None = None
    is_admin: bool = False
    if not token:
        token = websocket.cookies.get("session_token") or websocket.cookies.get("access_token")
    if token:
        try:
            from src.auth.service import decode_session_token
            payload = decode_session_token(token)
            if payload and "uid" in payload:
                uid = int(payload["uid"])
                is_admin = bool(payload.get("role") == "admin")
        except Exception:
            pass

    await notification_hub.connect(websocket, uid, is_admin=is_admin)
    try:
        while True:
            # Client heartbeat / ping-pong
            data = await websocket.receive_text()
            if data == "ping":
                await websocket.send_text("pong")
    except WebSocketDisconnect:
        notification_hub.disconnect(websocket, uid)
    except Exception:
        notification_hub.disconnect(websocket, uid)


@app.get("/api/notifications")
def api_get_notifications(
    request: Request,
    limit: int = Query(50, ge=1, le=100),
    unread_only: bool = Query(False),
    _: bool = Depends(_auth)
):
    """Fetch notifications in strict newest-first order with strict user-isolation RBAC."""
    from src.auth.service import get_current_user_optional
    user = get_current_user_optional(request)
    user_id = user.id if user else None
    role = getattr(user, "role", "user")
    is_admin = bool(role == "admin")

    with get_session() as session:
        query = select(Notification)
        if is_admin:
            # Admins see their own notifications AND system/admin-level notifications
            if user_id:
                query = query.where(or_(Notification.user_id == user_id, Notification.user_id.is_(None)))
            else:
                query = query.where(Notification.user_id.is_(None))
        else:
            # Regular users MUST strictly only see their own notifications
            if user_id:
                query = query.where(Notification.user_id == user_id)
            else:
                return {"ok": True, "count": 0, "notifications": []}

        if unread_only:
            query = query.where(Notification.is_read.is_(False))

        # Strict newest-first sorting: latest timestamp & highest ID first
        query = query.order_by(desc(Notification.created_at), desc(Notification.id)).limit(limit)
        items = session.execute(query).scalars().all()

        return {
            "ok": True,
            "count": len(items),
            "notifications": [
                {
                    "id": n.id,
                    "user_id": n.user_id,
                    "event_type": n.event_type,
                    "title": n.title,
                    "message": n.message,
                    "link": n.link,
                    "is_read": n.is_read,
                    "created_at": n.created_at.isoformat() if n.created_at else None,
                }
                for n in items
            ]
        }


@app.get("/api/notifications/unread_count")
def api_get_unread_notification_count(request: Request, _: bool = Depends(_auth)):
    """Get the unread notification badge count."""
    from src.auth.service import get_current_user_optional
    user = get_current_user_optional(request)
    user_id = user.id if user else None
    role = getattr(user, "role", "user")
    is_admin = bool(role == "admin")

    with get_session() as session:
        query = select(func.count(Notification.id)).where(Notification.is_read.is_(False))
        if is_admin:
            if user_id:
                query = query.where(or_(Notification.user_id == user_id, Notification.user_id.is_(None)))
            else:
                query = query.where(Notification.user_id.is_(None))
        else:
            if user_id:
                query = query.where(Notification.user_id == user_id)
            else:
                return {"ok": True, "unread_count": 0}

        count = session.execute(query).scalar_one()
        return {"ok": True, "unread_count": count}


@app.post("/api/notifications/{notification_id}/read")
def api_mark_notification_read(notification_id: int, request: Request, _: bool = Depends(_auth)):
    """Mark a specific notification as read."""
    from src.auth.service import get_current_user_optional
    user = get_current_user_optional(request)
    user_id = user.id if user else None
    role = getattr(user, "role", "user")

    with get_session() as session:
        notif = session.get(Notification, notification_id)
        if not notif:
            raise HTTPException(status_code=404, detail="Notification not found")
        if role != "admin" and notif.user_id != user_id:
            raise HTTPException(status_code=403, detail="Forbidden")

        notif.is_read = True
        session.commit()
    return {"ok": True, "message": "Notification marked as read"}


@app.post("/api/notifications/mark_all_read")
def api_mark_all_notifications_read(request: Request, _: bool = Depends(_auth)):
    """Mark all notifications as read for current user."""
    from src.auth.service import get_current_user_optional
    user = get_current_user_optional(request)
    user_id = user.id if user else None
    role = getattr(user, "role", "user")
    is_admin = bool(role == "admin")

    with get_session() as session:
        from sqlalchemy import update
        stmt = update(Notification).where(Notification.is_read.is_(False))
        if is_admin:
            if user_id:
                stmt = stmt.where(or_(Notification.user_id == user_id, Notification.user_id.is_(None)))
            else:
                stmt = stmt.where(Notification.user_id.is_(None))
        else:
            if user_id:
                stmt = stmt.where(Notification.user_id == user_id)
            else:
                return {"ok": True, "message": "All notifications marked as read"}

        stmt = stmt.values(is_read=True)
        session.execute(stmt)
        session.commit()
    return {"ok": True, "message": "All notifications marked as read"}


@app.post("/api/notifications/test")
def api_test_notification(
    request: Request,
    title: str = Query("Test Event 🔔"),
    message: str = Query("Real-time message broker notification received successfully!"),
    event_type: str = Query("system"),
    _: bool = Depends(_auth)
):
    """Publish a test notification to verify RabbitMQ/Kafka + WebSocket + UI delivery."""
    from src.auth.service import get_current_user_optional
    user = get_current_user_optional(request)
    user_id = user.id if user else None

    res = publish_notification(
        event_type=event_type,
        title=title,
        message=message,
        user_id=user_id,
        link="#notifications",
        data={"test": True, "time": datetime.now(timezone.utc).isoformat()}
    )
    return {"ok": True, "result": res}


# ---------------------------------------------------------------------------
# Multilingual AI Assistant & Admin Live Support Chat Endpoints
# ---------------------------------------------------------------------------

class ChatSendMessageRequest(BaseModel):
    message: str


class AdminChatReplyRequest(BaseModel):
    target_user_id: int
    message: str


def _support_msg_dict(msg: SupportMessage) -> dict:
    return {
        "id": msg.id,
        "user_id": msg.user_id,
        "sender_role": msg.sender_role,
        "sender_name": msg.sender_name,
        "admin_id": msg.admin_id,
        "message": msg.message,
        "language": msg.language,
        "is_read_by_user": msg.is_read_by_user,
        "is_read_by_admin": msg.is_read_by_admin,
        "created_at": _aware(msg.created_at),
    }


@app.get("/api/chat/history")
def api_chat_history(request: Request, limit: int = Query(60, ge=1, le=200)):
    """Fetch recent support and AI assistant chat messages for the authenticated user."""
    from src.auth.service import get_current_user_optional
    user = get_current_user_optional(request)
    if not user:
        return {"messages": []}

    with get_session() as session:
        msgs = (
            session.query(SupportMessage)
            .filter(SupportMessage.user_id == user.id)
            .order_by(SupportMessage.created_at.asc())
            .limit(limit)
            .all()
        )
        # Mark admin/bot replies as read by user
        unread = [m for m in msgs if m.sender_role in ("admin", "bot") and not m.is_read_by_user]
        if unread:
            for m in unread:
                m.is_read_by_user = True
            session.commit()

        return {"messages": [_support_msg_dict(m) for m in msgs]}


@app.post("/api/chat/send")
async def api_chat_send(request: Request, payload: ChatSendMessageRequest):
    """User sends a message to the multilingual AI assistant; notifies admin if requested or needed."""
    from src.auth.service import get_current_user_optional
    from src.chatbot.service import chatbot_service
    from src.notifications.publisher import publish_admin_alert

    user = get_current_user_optional(request)
    if not user:
        raise HTTPException(status_code=401, detail="Authentication required to chat with assistant")

    text = payload.message.strip()
    if not text:
        raise HTTPException(status_code=400, detail="Message cannot be empty")

    now_utc = datetime.now(timezone.utc)
    user_dict = {
        "id": user.id,
        "email": user.email,
        "full_name": user.full_name,
        "current_plan": user.current_plan,
        "role": user.role,
    }

    # 1. Save user message to database
    with get_session() as session:
        user_msg = SupportMessage(
            user_id=user.id,
            sender_role="user",
            sender_name=user.full_name or user.email,
            message=text,
            is_read_by_user=True,
            is_read_by_admin=False,
            created_at=now_utc,
        )
        session.add(user_msg)
        session.commit()
        session.refresh(user_msg)

        # Retrieve recent history for conversational context
        recent = (
            session.query(SupportMessage)
            .filter(SupportMessage.user_id == user.id)
            .order_by(SupportMessage.created_at.desc())
            .limit(6)
            .all()
        )
        recent_history = [
            {"sender_role": m.sender_role, "message": m.message}
            for m in reversed(recent)
        ]

    # 2. Generate multilingual AI response via Ollama / Gemini / semantic fallback
    bot_reply_text, needs_admin, detected_lang = chatbot_service.generate_response(
        text,
        history=recent_history,
        user_info=user_dict,
    )

    # 3. Save AI response to database
    with get_session() as session:
        bot_msg = SupportMessage(
            user_id=user.id,
            sender_role="bot",
            sender_name="AutoHunt Assistant",
            message=bot_reply_text,
            language=detected_lang,
            is_read_by_user=True,
            is_read_by_admin=False,
            created_at=datetime.now(timezone.utc),
        )
        session.add(bot_msg)
        session.commit()
        session.refresh(bot_msg)

        user_msg_data = _support_msg_dict(user_msg)
        bot_msg_data = _support_msg_dict(bot_msg)

    # 4. If user asked for human support or admin, notify administrators
    if needs_admin:
        try:
            publish_admin_alert(
                title="💬 Live Chat: User Needs Human Support",
                message=f"{user.full_name} ({user.email}) requested support in live chat: '{text[:90]}'",
                level="info",
                link="#admin",
                data={"user_id": user.id, "user_email": user.email, "message": text},
            )
        except Exception as aerr:
            logger.debug("Failed publishing admin chat alert: %s", aerr)

    # 5. Broadcast real-time events over WebSocket
    try:
        # Deliver bot reply directly to user
        await notification_hub.broadcast(
            user_id=user.id,
            payload={
                "event": "support_chat_message",
                "message": bot_msg_data,
            },
        )
        # Notify admins watching the Live Support console
        await notification_hub.broadcast(
            user_id=None,
            payload={
                "event": "admin_chat_event",
                "user_id": user.id,
                "user_name": user.full_name or user.email,
                "user_email": user.email,
                "user_plan": user.current_plan,
                "user_message": user_msg_data,
                "bot_message": bot_msg_data,
                "needs_admin": needs_admin,
            },
            admin_only=True,
        )
    except Exception as ws_err:
        logger.debug("WebSocket broadcast error in chat: %s", ws_err)

    return {
        "ok": True,
        "user_message": user_msg_data,
        "bot_message": bot_msg_data,
        "needs_admin": needs_admin,
    }


@app.get("/api/chat/admin/conversations")
def api_chat_admin_conversations(request: Request, _: bool = Depends(_auth)):
    """Admin endpoint: lists all user conversation threads with unread counts and latest messages."""
    from src.auth.service import require_admin
    require_admin(request)

    with get_session() as session:
        # Find all users with support messages
        user_ids = [
            uid for (uid,) in
            session.query(SupportMessage.user_id).distinct().all()
            if uid is not None
        ]
        if not user_ids:
            return {"conversations": []}

        users = session.query(User).filter(User.id.in_(user_ids)).all()
        user_map = {u.id: u for u in users}

        conversations = []
        for uid in user_ids:
            u = user_map.get(uid)
            if not u:
                continue

            last_msg = (
                session.query(SupportMessage)
                .filter(SupportMessage.user_id == uid)
                .order_by(SupportMessage.created_at.desc())
                .first()
            )
            unread_count = (
                session.query(func.count(SupportMessage.id))
                .filter(
                    SupportMessage.user_id == uid,
                    SupportMessage.sender_role == "user",
                    SupportMessage.is_read_by_admin.is_(False),
                )
                .scalar() or 0
            )

            conversations.append({
                "user_id": u.id,
                "user_email": u.email,
                "user_name": u.full_name or u.email,
                "user_role": u.role,
                "current_plan": u.current_plan,
                "daily_apply_limit": u.daily_apply_limit,
                "avatar_url": u.avatar_url,
                "unread_count": unread_count,
                "last_message": last_msg.message if last_msg else "",
                "last_message_role": last_msg.sender_role if last_msg else "user",
                "last_message_at": _aware(last_msg.created_at) if last_msg else None,
            })

        # Sort conversations by last_message_at descending
        conversations.sort(
            key=lambda c: c["last_message_at"] or "",
            reverse=True,
        )
        return {"conversations": conversations}


@app.get("/api/chat/admin/conversation/{target_user_id}")
def api_chat_admin_get_conversation(target_user_id: int, request: Request, _: bool = Depends(_auth)):
    """Admin endpoint: fetch full chat history for a target user and mark their messages as read by admin."""
    from src.auth.service import require_admin
    require_admin(request)

    with get_session() as session:
        target_user = session.get(User, target_user_id)
        if not target_user:
            raise HTTPException(status_code=404, detail="User not found")

        msgs = (
            session.query(SupportMessage)
            .filter(SupportMessage.user_id == target_user_id)
            .order_by(SupportMessage.created_at.asc())
            .all()
        )

        # Mark user messages as read by admin
        for m in msgs:
            if m.sender_role == "user" and not m.is_read_by_admin:
                m.is_read_by_admin = True
        session.commit()

        return {
            "user": {
                "id": target_user.id,
                "email": target_user.email,
                "full_name": target_user.full_name,
                "role": target_user.role,
                "current_plan": target_user.current_plan,
                "daily_apply_limit": target_user.daily_apply_limit,
                "created_at": _aware(target_user.created_at),
            },
            "messages": [_support_msg_dict(m) for m in msgs],
        }


@app.post("/api/chat/admin/reply")
async def api_chat_admin_reply(request: Request, payload: AdminChatReplyRequest, _: bool = Depends(_auth)):
    """Admin endpoint: reply directly to a user's chat through the platform."""
    from src.auth.service import require_admin
    admin_user = require_admin(request)

    text = payload.message.strip()
    if not text:
        raise HTTPException(status_code=400, detail="Reply cannot be empty")

    with get_session() as session:
        target_user = session.get(User, payload.target_user_id)
        if not target_user:
            raise HTTPException(status_code=404, detail="Target user not found")

        admin_msg = SupportMessage(
            user_id=payload.target_user_id,
            sender_role="admin",
            sender_name=f"Support Admin ({admin_user.full_name or 'Platform Team'})",
            admin_id=admin_user.id,
            message=text,
            is_read_by_user=False,
            is_read_by_admin=True,
            created_at=datetime.now(timezone.utc),
        )
        session.add(admin_msg)
        session.commit()
        session.refresh(admin_msg)
        msg_data = _support_msg_dict(admin_msg)

    # 1. Push directly into user's live chat via WebSocket
    try:
        await notification_hub.broadcast(
            user_id=payload.target_user_id,
            payload={
                "event": "support_chat_message",
                "message": msg_data,
            },
        )
    except Exception as ws_err:
        logger.debug("Failed broadcasting admin reply via WS: %s", ws_err)

    # 2. Push standard notification alert to candidate
    try:
        publish_notification(
            user_id=payload.target_user_id,
            event_type="support_reply",
            title="💬 Message from Platform Support",
            message=f"{admin_user.full_name or 'Support'}: {text[:80]}",
            link="#chat",
            data={"admin_id": admin_user.id, "message_id": admin_msg.id},
        )
    except Exception as notif_err:
        logger.debug("Failed publishing support reply notification: %s", notif_err)

    # 3. Inform other admin sockets so all admin tabs sync instantly
    try:
        await notification_hub.broadcast(
            user_id=None,
            payload={
                "event": "admin_chat_event",
                "user_id": payload.target_user_id,
                "admin_reply": msg_data,
            },
            admin_only=True,
        )
    except Exception:
        pass

    return {"ok": True, "message": msg_data}


# ---------------------------------------------------------------------------
# Interactive Action Endpoints & CSV Export
# ---------------------------------------------------------------------------

_eval_lock = threading.Lock()
_eval_thread: threading.Thread | None = None
_eval_status_by_user: dict[int | None, dict] = {}


def _get_eval_status(user_id: int | None) -> dict:
    if user_id not in _eval_status_by_user:
        _eval_status_by_user[user_id] = {"running": False, "total": 0, "processed": 0, "message": "Idle"}
    return _eval_status_by_user[user_id]


@app.post("/api/actions/evaluate_backlog")
def api_evaluate_backlog(request: Request, limit: int = 50, _: bool = Depends(_auth)):
    """Evaluate un-evaluated / pending job postings in background using hybrid AI/heuristic."""
    global _eval_thread
    from src.auth.service import get_current_user_optional
    user = get_current_user_optional(request)
    user_id = user.id if user else None
    user_status = _get_eval_status(user_id)

    # Pre-check: Does this user actually have un-evaluated jobs?
    with get_session() as session:
        check_q = select(func.count(JobPosting.id)).where(JobPosting.evaluated.is_(False))
        if user_id is not None:
            check_q = check_q.where(JobPosting.user_id == user_id)
        pending_count = session.scalar(check_q) or 0

    if pending_count == 0:
        user_status["running"] = False
        user_status["message"] = "No un-evaluated jobs found for your profile. Please run a job discovery scan first."
        return {
            "ok": False,
            "count": 0,
            "message": "No un-evaluated jobs found for your account. Please run a job discovery scan first.",
            "status": user_status,
        }

    with _eval_lock:
        if _eval_thread and _eval_thread.is_alive() and user_status.get("running"):
            return JSONResponse(status_code=409, content={"ok": False, "message": "Backlog evaluation is already running", "status": user_status})

        def _worker(target_user_id: int | None):
            from src.evaluation.claude_client import evaluate_job
            from src.candidate.profile_manager import get_active_profile
            st = _get_eval_status(target_user_id)
            st["running"] = True
            st["message"] = "Fetching un-evaluated jobs…"
            try:
                with get_session() as session:
                    active_profile = get_active_profile(user_id=target_user_id, session=session)
                    q = select(JobPosting).where(JobPosting.evaluated.is_(False))
                    if target_user_id is not None:
                        q = q.where(JobPosting.user_id == target_user_id)
                    un_eval_jobs = session.scalars(
                        q.order_by(
                            case((JobPosting.application_emails.isnot(None) & (JobPosting.application_emails != ""), 0), else_=1),
                            desc(JobPosting.id)
                        )
                        .limit(max(1, min(limit, 200)))
                    ).all()
                    st["total"] = len(un_eval_jobs)
                    st["processed"] = 0

                    for idx, job in enumerate(un_eval_jobs, start=1):
                        st["processed"] = idx
                        st["message"] = f"Evaluating [{idx}/{len(un_eval_jobs)}] {job.title} at {job.company}"
                        job_dict = {
                            "title": job.title,
                            "company": job.company,
                            "location": job.location,
                            "source_site": job.source_site,
                            "is_remote": job.is_remote,
                            "raw_description": job.raw_description,
                            "job_url": job.job_url,
                            "application_url": job.application_url,
                            "application_emails": job.application_emails,
                            "company_url": job.company_url,
                        }
                        try:
                            eval_res = evaluate_job(job_dict, profile=active_profile, session=session)
                            if eval_res:
                                job.evaluated = True
                                job.evaluation_status = "evaluated"
                                job.evaluation_method = eval_res.get("evaluation_method") or "heuristic"
                                job.match_score = eval_res.get("match_score", 0)
                                job.visa_sponsorship_detected = eval_res.get("visa_sponsorship_detected", False)
                                job.visa_status_notes = eval_res.get("visa_status_notes")
                                job.key_skills_matched = json.dumps(eval_res.get("key_skills_matched", []), ensure_ascii=False)
                                job.recruiter_pitch_fr = eval_res.get("recruiter_pitch_fr")
                                job.recruiter_pitch_en = eval_res.get("recruiter_pitch_en")
                                from src.evaluation.language import is_french_job
                                is_fr = is_french_job(job_dict)
                                job.application_subject = (eval_res.get("application_subject_fr") if is_fr else eval_res.get("application_subject")) or eval_res.get("application_subject")
                                job.application_email_body = (eval_res.get("application_email_fr") if is_fr else eval_res.get("application_email_en")) or eval_res.get("application_email_en")
                                clears = (job.match_score >= settings.min_match_score) and (job.visa_sponsorship_detected or not settings.min_visa_confidence)
                                job.pipeline_stage = PipelineStage.EVALUATED_MATCH if clears else PipelineStage.EVALUATED_LOW
                                if clears:
                                    # HR enrichment to find real recruiter emails (Pro / Admin check)
                                    target_u = (session.get(User, target_user_id) if target_user_id else None) or session.scalar(select(User).order_by(User.id))
                                    is_pro = target_u and (target_u.role == "admin" or getattr(target_u, "current_plan", None) == "pro_499")
                                    if settings.enable_hr_enrichment and not job.application_emails and is_pro:
                                        try:
                                            from src.ingestion.hr_enrichment import enrich_job_hr_contacts
                                            enrich_job_hr_contacts(job)
                                        except Exception as enrich_err:
                                            logger.debug("HR enrichment skipped for job %s: %s", job.id, enrich_err)
                                    try:
                                        from src.application.auto_apply import apply_to_job
                                        # Rebuild dict with latest data (including any emails added by enrichment)
                                        apply_dict = {
                                            "id": job.id,
                                            "title": job.title,
                                            "company": job.company,
                                            "location": job.location,
                                            "continent": job.continent,
                                            "source_site": job.source_site,
                                            "is_remote": job.is_remote,
                                            "raw_description": job.raw_description,
                                            "job_url": job.job_url,
                                            "application_url": job.application_url,
                                            "application_emails": job.application_emails,
                                            "company_url": job.company_url,
                                            "recruiter_pitch_en": job.recruiter_pitch_en,
                                            "recruiter_pitch_fr": job.recruiter_pitch_fr,
                                            "application_subject": job.application_subject,
                                            "application_subject_fr": eval_res.get("application_subject_fr"),
                                            "application_email_body": job.application_email_body,
                                            "application_email_fr": eval_res.get("application_email_fr"),
                                            "application_email_en": eval_res.get("application_email_en"),
                                        }
                                        apply_res = apply_to_job(apply_dict, session=session)
                                        job.application_method = apply_res.get("application_method")
                                        job.application_email = apply_res.get("application_email")
                                        job.application_url = apply_res.get("application_url") or job.application_url
                                        job.application_status = apply_res.get("application_status")
                                        job.application_error = apply_res.get("application_error")
                                        job.application_attempted_at = datetime.now(timezone.utc)
                                        job.resume_attached = bool(apply_res.get("resume_attached"))
                                        job.application_screenshot_path = apply_res.get("application_screenshot_path")
                                        job.thread_subject = apply_res.get("thread_subject")
                                        if apply_res.get("applied"):
                                            job.pipeline_stage = PipelineStage.APPLIED
                                            job.applied_at = apply_res.get("applied_at") or datetime.now(timezone.utc)
                                            try:
                                                from src.storage.models import UserJobApplication, User
                                                if target_u:
                                                    session.add(
                                                        UserJobApplication(
                                                            user_id=target_u.id,
                                                            job_id=job.id,
                                                            application_method=job.application_method or "recruiter_email",
                                                            applied_at=job.applied_at,
                                                            status=job.application_status or "sent",
                                                        )
                                                    )
                                            except Exception as uja_err:
                                                logger.debug("Failed adding UserJobApplication in backlog: %s", uja_err)
                                    except Exception as apply_err:
                                        logger.error("Auto apply error for job %s: %s", job.id, apply_err)
                        except Exception as e:
                            job.evaluation_status = "failed"
                            job.evaluation_reason = str(e)[:300]
                    session.commit()
                st["message"] = f"Finished evaluating {st['processed']} jobs"
            except Exception as exc:
                st["message"] = f"Evaluation error: {exc}"
            finally:
                st["running"] = False

        _eval_thread = threading.Thread(target=_worker, args=(user_id,), daemon=True, name="backlog-evaluator")
        _eval_thread.start()
        return {"ok": True, "message": f"Evaluating up to {limit} pending jobs in background", "status": user_status}


@app.get("/api/actions/evaluate_status")
def api_evaluate_status(request: Request, _: bool = Depends(_auth)):
    from src.auth.service import get_current_user_optional
    user = get_current_user_optional(request)
    user_id = user.id if user else None
    return _get_eval_status(user_id)


_apply_status = {"running": False, "total": 0, "processed": 0, "applied": 0, "message": "Idle"}
_apply_thread = None


@app.post("/api/actions/apply_pending_matches")
def api_action_apply_pending_matches(request: Request, _: bool = Depends(_auth)):
    global _apply_thread, _apply_status
    if _apply_status["running"]:
        return JSONResponse(status_code=409, content={"ok": False, "message": "Apply job already running", "status": _apply_status})

    from src.auth.service import get_current_user_optional
    calling_user = get_current_user_optional(request)
    calling_user_id = calling_user.id if calling_user else None

    def _apply_worker(target_user_id: int | None):
        global _apply_status
        _apply_status = {"running": True, "total": 0, "processed": 0, "applied": 0, "message": "Starting batch applications..."}
        try:
            with get_session() as session:
                from src.application.auto_apply import apply_to_job
                from src.ingestion.hr_enrichment import enrich_job_hr_contacts
                from src.storage.models import User, UserJobApplication
                from src.candidate.profile_manager import get_active_profile
                from src.evaluation.heuristic_scorer import evaluate_job_heuristic
                from src.evaluation.language import is_french_job

                user = session.get(User, target_user_id) if target_user_id else None
                if not user:
                    user = session.scalar(select(User).where(User.role == "user").order_by(User.id))

                effective_uid = user.id if user else target_user_id
                active_profile = get_active_profile(user_id=effective_uid, session=session)

                from src.storage.plans import get_effective_daily_limit
                daily_limit = user.daily_apply_limit if (user and user.daily_apply_limit) else get_effective_daily_limit(session, getattr(user, "current_plan", "starter"), fallback_limit=(200 if user and user.role == "admin" else 50))
                today_start = datetime.now(timezone.utc).replace(hour=0, minute=0, second=0, microsecond=0)
                sent_today = 0
                if user:
                    user_apps_today = session.scalar(
                        select(func.count(UserJobApplication.id)).where(
                            UserJobApplication.user_id == user.id,
                            UserJobApplication.applied_at >= today_start,
                        )
                    ) or 0
                    if user.role == "admin":
                        total_platform_today = session.scalar(
                            select(func.count(JobPosting.id)).where(JobPosting.applied_at >= today_start)
                        ) or 0
                        sent_today = max(user_apps_today, total_platform_today)
                    else:
                        sent_today = user_apps_today

                remaining_quota = max(0, daily_limit - sent_today)
                if user and remaining_quota <= 0:
                    _apply_status["running"] = False
                    _apply_status["message"] = f"Daily limit reached ({sent_today}/{daily_limit} applications sent today). Quota resets at midnight UTC."
                    try:
                        from src.notifications.publisher import publish_notification
                        publish_notification(
                            user_id=target_user_id,
                            event_type="applications_done",
                            title="Applications Complete for Today 🎯",
                            message=f"Daily safety limit reached ({daily_limit}/{daily_limit} applications sent today). Quota resets at midnight UTC.",
                            link="#outbound",
                            data={"sent_today": sent_today, "daily_limit": daily_limit, "reason": "limit_reached"},
                        )
                    except Exception as nerr:
                        logger.debug("Failed to dispatch limit reached notification: %s", nerr)
                    return

                # Exclude known un-appliable dead ends from wasting time in batch apply
                blocked_statuses = [
                    "manual_linkedin", "manual_job_board", "manual_security_challenge",
                    "blocked", "invalid_recipient", "recruiter_email_blocked"
                ]
                apply_q = select(JobPosting).where(
                    JobPosting.match_score >= settings.auto_apply_min_score,
                    JobPosting.applied_at.is_(None),
                    JobPosting.pipeline_stage != PipelineStage.APPLIED,
                    or_(
                        JobPosting.application_status.in_(["not_attempted", "daily_quota_reached", None]),
                        (JobPosting.application_emails.isnot(None) & (JobPosting.application_emails != ""))
                    ),
                    ~JobPosting.application_status.in_(blocked_statuses)
                )
                if target_user_id is not None and user and user.role != "admin":
                    apply_q = apply_q.where(or_(JobPosting.user_id == target_user_id, JobPosting.user_id.is_(None)))

                # Bounded query: inspect top candidates up to remaining quota (avoids churning through 100s of jobs)
                max_to_inspect = min(max(remaining_quota * 3, 15), 60)
                pending_matches = session.scalars(
                    apply_q.order_by(
                        case((JobPosting.application_emails.isnot(None) & (JobPosting.application_emails != ""), 0), else_=1),
                        desc(JobPosting.match_score)
                    ).limit(max_to_inspect)
                ).all()

                _apply_status["total"] = len(pending_matches)
                consecutive_unactionable = 0
                for i, job in enumerate(pending_matches, 1):
                    if user and sent_today >= daily_limit:
                        logger.info("Daily application limit (%s) reached for user %s. Stopping batch apply.", daily_limit, user.email)
                        _apply_status["message"] = f"Finished: Reached daily application limit ({sent_today}/{daily_limit} sent today)."
                        try:
                            from src.notifications.publisher import publish_notification
                            publish_notification(
                                user_id=target_user_id,
                                event_type="applications_done",
                                title="Applications Complete for Today 🎯",
                                message=f"Daily limit reached ({daily_limit}/{daily_limit} applications sent today). More will resume tomorrow!",
                                link="#outbound",
                                data={"sent_today": sent_today, "daily_limit": daily_limit, "reason": "limit_reached"},
                            )
                        except Exception as nerr:
                            logger.debug("Failed to dispatch applications_done notification: %s", nerr)
                        break

                    # Circuit breaker: if many consecutive jobs lack automated channels, don't stall the dashboard
                    if consecutive_unactionable >= 12 and _apply_status["applied"] == 0:
                        _apply_status["message"] = f"Checked {i-1} matches with no automated apply channel available. Remaining jobs require manual application."
                        break

                    # Verify that this job aligns with the currently active candidate profile
                    eval_check = evaluate_job_heuristic(job, profile=active_profile)
                    profile_score = eval_check.get("match_score", 0)
                    if (job.match_score is None or job.match_score < settings.auto_apply_min_score) and profile_score < settings.auto_apply_min_score:
                        job.match_score = profile_score
                        clears_eval = profile_score >= settings.min_match_score
                        job.pipeline_stage = PipelineStage.EVALUATED_MATCH if clears_eval else PipelineStage.EVALUATED_LOW
                        logger.info(
                            "Skipping job %s (%s): scores %s (< %s) against active profile '%s'",
                            job.id, job.title, profile_score, settings.auto_apply_min_score,
                            active_profile.get("headline") or active_profile.get("name")
                        )
                        continue

                    # Refresh pitches for the active profile
                    is_fr = is_french_job(job)
                    job.recruiter_pitch_en = eval_check.get("recruiter_pitch_en") or job.recruiter_pitch_en
                    job.recruiter_pitch_fr = eval_check.get("recruiter_pitch_fr") or job.recruiter_pitch_fr
                    job.application_subject = (eval_check.get("application_subject_fr") if is_fr else eval_check.get("application_subject")) or job.application_subject
                    job.application_email_body = (eval_check.get("application_email_fr") if is_fr else eval_check.get("application_email_en")) or job.application_email_body

                    _apply_status["processed"] = i
                    _apply_status["message"] = f"Applying to {i}/{len(pending_matches)}: {job.title} @ {job.company}"

                    # Enrich HR contacts if missing (only once)
                    hr_attempted = False
                    if settings.enable_hr_enrichment and not job.application_emails:
                        try:
                            enrich_job_hr_contacts(job)
                            hr_attempted = True
                        except Exception as enrich_err:
                            logger.debug("HR enrichment skipped for job %s: %s", job.id, enrich_err)

                    job_dict = {
                        "id": job.id,
                        "user_id": job.user_id,
                        "title": job.title,
                        "company": job.company,
                        "location": job.location,
                        "source_site": job.source_site,
                        "is_remote": job.is_remote,
                        "raw_description": job.raw_description,
                        "job_url": job.job_url,
                        "application_url": job.application_url,
                        "application_emails": job.application_emails,
                        "company_url": job.company_url,
                        "continent": job.continent,
                        "recruiter_pitch_en": job.recruiter_pitch_en,
                        "recruiter_pitch_fr": job.recruiter_pitch_fr,
                        "application_subject": job.application_subject,
                        "application_email_body": job.application_email_body,
                        "_hr_enrichment_attempted": hr_attempted,
                    }
                    try:
                        res = apply_to_job(job_dict, session=session, profile=active_profile)

                        job.application_method = res.get("application_method")
                        job.application_email = res.get("application_email")
                        job.application_url = res.get("application_url") or job.application_url
                        job.application_status = res.get("application_status")
                        job.application_error = res.get("application_error")
                        job.application_attempted_at = datetime.now(timezone.utc)
                        job.resume_attached = bool(res.get("resume_attached"))
                        job.application_screenshot_path = res.get("application_screenshot_path")
                        job.thread_subject = res.get("thread_subject")
                        if res.get("applied"):
                            consecutive_unactionable = 0
                            job.pipeline_stage = PipelineStage.APPLIED
                            job.applied_at = res.get("applied_at") or datetime.now(timezone.utc)
                            _apply_status["applied"] += 1
                            target_u = user or session.scalar(select(User).order_by(User.id))
                            if target_u:
                                job.user_id = target_u.id
                                session.add(
                                    UserJobApplication(
                                        user_id=target_u.id,
                                        job_id=job.id,
                                        application_method=job.application_method,
                                        applied_at=job.applied_at,
                                        status=job.application_status or "sent",
                                    )
                                )
                                sent_today += 1
                            if res.get("message_id"):
                                session.add(
                                    EmailEvent(
                                        job_id=job.id,
                                        direction="outbound",
                                        message_id=res["message_id"],
                                        sender_email=settings.sender_email,
                                        recipient_email=job.application_email,
                                        subject=job.thread_subject,
                                        body=res.get("application_body") or job.application_email_body or job.recruiter_pitch_fr or job.recruiter_pitch_en,
                                        email_type="application",
                                    )
                                )
                        else:
                            consecutive_unactionable += 1
                    except Exception as err:
                        consecutive_unactionable += 1
                        logger.error("Batch apply failed for job %s: %s", job.id, err)
                    session.commit()
                applied_count = _apply_status.get("applied", 0)
                _apply_status["message"] = f"Finished! Applied to {applied_count} out of {_apply_status['total']} jobs."
                try:
                    from src.notifications.publisher import publish_notification
                    publish_notification(
                        user_id=target_user_id,
                        event_type="applications_done",
                        title="Applications Complete for Today 🎯",
                        message=f"Auto-apply complete for today: {applied_count} application{'s' if applied_count != 1 else ''} sent successfully.",
                        link="#outbound",
                        data={"applied_today": applied_count, "total_queued": _apply_status['total'], "reason": "batch_complete"},
                    )
                except Exception as nerr:
                    logger.debug("Failed to dispatch batch complete notification: %s", nerr)
        except Exception as exc:
            _apply_status["message"] = f"Batch apply error: {exc}"
        finally:
            _apply_status["running"] = False

    _apply_thread = threading.Thread(target=_apply_worker, args=(calling_user_id,), daemon=True, name="batch-auto-apply")
    _apply_thread.start()
    return {"ok": True, "message": "Batch auto-apply started in background"}


@app.post("/api/jobs/{job_id}/apply")
def api_apply_single_job(job_id: int, request: Request, _: bool = Depends(_auth)):
    """Apply to a single job posting immediately via email or web portal."""
    from src.auth.service import get_current_user_optional
    user = get_current_user_optional(request)

    with get_session() as session:
        job = session.get(JobPosting, job_id)
        if not job:
            return JSONResponse(status_code=404, content={"ok": False, "message": "Job not found"})
        if user and user.role != "admin" and job.user_id is not None and job.user_id != user.id:
            return JSONResponse(status_code=403, content={"ok": False, "message": "Not authorized to access or apply to this job"})

        from src.storage.models import User, UserJobApplication
        if not user:
            user = (session.get(User, job.user_id) if job.user_id else None) or session.scalar(select(User).where(User.role == "user").order_by(User.id))

        if user and user.role != "admin":
            today_start = datetime.now(timezone.utc).replace(hour=0, minute=0, second=0, microsecond=0)
            sent_today = session.scalar(
                select(func.count(UserJobApplication.id)).where(
                    UserJobApplication.user_id == user.id,
                    UserJobApplication.applied_at >= today_start,
                )
            ) or 0
            if sent_today >= user.daily_apply_limit:
                return JSONResponse(
                    status_code=429,
                    content={
                        "ok": False,
                        "message": f"Daily application limit reached ({sent_today}/{user.daily_apply_limit} sent today). Upgrade to Starter (50/day) or Pro (200/day) to apply more.",
                        "quota_exceeded": True,
                        "daily_apply_limit": user.daily_apply_limit,
                        "applications_sent_today": sent_today,
                    }
                )

        # Enrich HR contacts if missing
        if settings.enable_hr_enrichment and not job.application_emails:
            try:
                from src.ingestion.hr_enrichment import enrich_job_hr_contacts
                enrich_job_hr_contacts(job)
            except Exception as e:
                logger.debug("Enrichment error: %s", e)

        from src.application.auto_apply import apply_to_job
        job_dict = {
            "id": job.id,
            "title": job.title,
            "company": job.company,
            "location": job.location,
            "source_site": job.source_site,
            "is_remote": job.is_remote,
            "raw_description": job.raw_description,
            "job_url": job.job_url,
            "application_url": job.application_url,
            "application_emails": job.application_emails,
            "company_url": job.company_url,
            "continent": job.continent,
            "recruiter_pitch_en": job.recruiter_pitch_en,
            "recruiter_pitch_fr": job.recruiter_pitch_fr,
            "application_subject": job.application_subject,
            "application_email_body": job.application_email_body,
        }
        res = apply_to_job(job_dict, session=session)
        job.application_method = res.get("application_method")
        job.application_email = res.get("application_email")
        job.application_url = res.get("application_url") or job.application_url
        job.application_status = res.get("application_status")
        job.application_error = res.get("application_error")
        job.application_attempted_at = datetime.now(timezone.utc)
        job.resume_attached = bool(res.get("resume_attached"))
        job.application_screenshot_path = res.get("application_screenshot_path")
        job.thread_subject = res.get("thread_subject")
        if res.get("applied"):
            job.pipeline_stage = PipelineStage.APPLIED
            job.applied_at = res.get("applied_at") or datetime.now(timezone.utc)
            target_u = user or (session.get(User, job.user_id) if job.user_id else None) or session.scalar(select(User).order_by(User.id))
            if target_u:
                session.add(
                    UserJobApplication(
                        user_id=target_u.id,
                        job_id=job.id,
                        application_method=job.application_method,
                        applied_at=job.applied_at,
                        status=job.application_status or "sent",
                    )
                )
            if res.get("message_id"):
                session.add(EmailEvent(
                    job_id=job.id,
                    direction="outbound",
                    message_id=res["message_id"],
                    sender_email=settings.sender_email,
                    recipient_email=job.application_email,
                    subject=job.thread_subject,
                    body=res.get("application_body") or job.application_email_body or job.recruiter_pitch_fr or job.recruiter_pitch_en,
                    email_type="application",
                ))
        session.commit()
        return {
            "ok": True,
            "applied": bool(res.get("applied")),
            "method": job.application_method,
            "status": job.application_status,
            "email": job.application_email,
            "error": job.application_error,
            "message": f"Application status: {job.application_status} ({job.application_method or 'unknown'})"
        }


@app.get("/api/actions/apply_status")
def api_apply_status(_: bool = Depends(_auth)):
    return _apply_status


class HrDiscoverRequest(BaseModel):
    company_name: str = ""
    domain: str = ""
    url: str = ""


_hr_enrich_status = {"running": False, "total": 0, "processed": 0, "enriched": 0, "message": "Idle"}
_hr_enrich_thread: threading.Thread | None = None


def _require_pro_plan(request: Request):
    from src.auth.service import get_current_user_optional
    user = get_current_user_optional(request)
    if user and user.role != "admin":
        from src.storage.plans import get_plan_by_slug
        with get_session() as session:
            p = get_plan_by_slug(session, user.current_plan)
            has_access = bool(p and (p.can_access_freelance or p.slug in ("pro", "pro_499", "ultra")))
            if not has_access and user.current_plan not in ("pro", "pro_499", "ultra"):
                raise HTTPException(
                    status_code=403,
                    detail="⭐ HR & Talent Acquisition Email Discovery Engine is exclusively available on the Pro & Agency Plans. Please upgrade your subscription."
                )


@app.post("/api/hr/discover")
def api_hr_discover(payload: HrDiscoverRequest, request: Request, _: bool = Depends(_auth)):
    """Interactive HR & recruiter email discovery for any company name or domain."""
    _require_pro_plan(request)
    from src.ingestion.hr_enrichment import discover_hr_contacts_for_company, check_domain_has_mx, extract_clean_domain, resolve_company_domain
    
    target_dom = extract_clean_domain(payload.domain or payload.url)
    if not target_dom and payload.company_name:
        target_dom = resolve_company_domain(payload.company_name)

    has_mx = check_domain_has_mx(target_dom) if target_dom else False
    contacts = discover_hr_contacts_for_company(
        company_name=payload.company_name or "",
        company_url=f"https://{target_dom}" if target_dom and not target_dom.startswith("http") else (payload.url or None),
    )

    return {
        "ok": True,
        "company_name": payload.company_name,
        "resolved_domain": target_dom,
        "has_mx": has_mx,
        "total_contacts": len(contacts),
        "contacts": [c.to_dict() for c in contacts],
    }


@app.post("/api/hr/enrich_job/{job_id}")
def api_hr_enrich_job(job_id: int, request: Request, _: bool = Depends(_auth)):
    """Enrich a single job with discovered HR & recruiter emails."""
    _require_pro_plan(request)
    from src.ingestion.hr_enrichment import enrich_job_hr_contacts, discover_hr_contacts_for_company
    with get_session() as session:
        job = session.get(JobPosting, job_id)
        if not job:
            raise HTTPException(404, "Job not found")

        top_email = enrich_job_hr_contacts(job)
        session.commit()
        return {
            "ok": True,
            "job_id": job.id,
            "company": job.company,
            "application_emails": job.application_emails,
            "top_email": top_email,
        }


@app.post("/api/hr/enrich_batch")
def api_hr_enrich_batch(request: Request, limit: int = 25, _: bool = Depends(_auth)):
    """Trigger background batch enrichment for matching jobs lacking HR emails."""
    _require_pro_plan(request)
    global _hr_enrich_status, _hr_enrich_thread
    if _hr_enrich_status["running"]:
        return {"ok": False, "message": "HR enrichment is already running in background", "status": _hr_enrich_status}

    def _worker():
        global _hr_enrich_status
        from src.ingestion.hr_enrichment import enrich_job_hr_contacts
        _hr_enrich_status["running"] = True
        _hr_enrich_status["message"] = "Querying eligible jobs..."
        try:
            with get_session() as session:
                jobs = (
                    session.query(JobPosting)
                    .filter(
                        (JobPosting.application_emails == None) | (JobPosting.application_emails == ""),
                        JobPosting.company != "Unknown company",
                        JobPosting.company != None,
                        JobPosting.pipeline_stage.in_([PipelineStage.EVALUATED_MATCH, PipelineStage.DISCOVERED, PipelineStage.APPLIED]),
                    )
                    .order_by(JobPosting.match_score.desc().nullslast(), JobPosting.id.desc())
                    .limit(limit)
                    .all()
                )
                _hr_enrich_status["total"] = len(jobs)
                _hr_enrich_status["processed"] = 0
                _hr_enrich_status["enriched"] = 0

                for job in jobs:
                    _hr_enrich_status["message"] = f"Enriching #{job.id}: {job.company}..."
                    try:
                        top = enrich_job_hr_contacts(job)
                        if top:
                            _hr_enrich_status["enriched"] += 1
                    except Exception as err:
                        logger.debug("HR enrich failed for %s: %s", job.id, err)
                    _hr_enrich_status["processed"] += 1
                    session.commit()

                _hr_enrich_status["message"] = f"Finished! Enriched {_hr_enrich_status['enriched']} out of {_hr_enrich_status['total']} jobs."
        except Exception as exc:
            _hr_enrich_status["message"] = f"HR enrich error: {exc}"
        finally:
            _hr_enrich_status["running"] = False

    _hr_enrich_thread = threading.Thread(target=_worker, daemon=True, name="batch-hr-enrich")
    _hr_enrich_thread.start()
    return {"ok": True, "message": "Batch HR enrichment started in background"}


@app.get("/api/hr/enrich_status")
def api_hr_enrich_status(_: bool = Depends(_auth)):
    return _hr_enrich_status


@app.get("/api/hr/stats")
def api_hr_stats(request: Request, _: bool = Depends(_auth)):
    """Return summary stats of HR emails across jobs."""
    from src.auth.service import get_current_user_optional
    user = get_current_user_optional(request)
    user_id = user.id if user else None
    with get_session() as session:
        def _scope_hr(stmt):
            if user_id is not None:
                return stmt.where(JobPosting.user_id == user_id)
            return stmt

        total_jobs = session.scalar(_scope_hr(select(func.count(JobPosting.id)))) or 0
        with_email = session.scalar(
            _scope_hr(select(func.count(JobPosting.id)).where(
                JobPosting.application_emails.isnot(None),
                JobPosting.application_emails != "",
            ))
        ) or 0
        matched_total = session.scalar(
            _scope_hr(select(func.count(JobPosting.id)).where(JobPosting.pipeline_stage == PipelineStage.EVALUATED_MATCH))
        ) or 0
        matched_with_email = session.scalar(
            _scope_hr(select(func.count(JobPosting.id)).where(
                JobPosting.pipeline_stage == PipelineStage.EVALUATED_MATCH,
                JobPosting.application_emails.isnot(None),
                JobPosting.application_emails != "",
            ))
        ) or 0
        return {
            "total_jobs": total_jobs,
            "jobs_with_hr_email": with_email,
            "matched_total": matched_total,
            "matched_with_hr_email": matched_with_email,
            "percentage_enriched": round((with_email / total_jobs * 100) if total_jobs else 0, 1),
        }


@app.get("/api/stats")
def api_dynamic_stats(request: Request, _: bool = Depends(_auth)):
    from src.auth.service import get_current_user_optional
    user = get_current_user_optional(request)
    user_id = user.id if user else None
    with get_session() as session:
        def _scope_st(stmt):
            if user_id is not None:
                return stmt.where(JobPosting.user_id == user_id)
            return stmt

        now = datetime.now(timezone.utc)
        day_start = now.replace(hour=0, minute=0, second=0, microsecond=0)
        jobs_total = session.scalar(_scope_st(select(func.count(JobPosting.id)))) or 0
        jobs_evaluated = session.scalar(_scope_st(select(func.count(JobPosting.id)).where(JobPosting.evaluated.is_(True)))) or 0
        good_matches = session.scalar(_scope_st(select(func.count(JobPosting.id)).where(JobPosting.pipeline_stage == PipelineStage.EVALUATED_MATCH))) or 0
        applications_today = session.scalar(_scope_st(select(func.count(JobPosting.id)).where(JobPosting.applied_at >= day_start))) or 0
        quota = quota_snapshot(session)
        counts = _stage_counts(session, user_id=user_id)
        return {
            "jobs_total": jobs_total,
            "jobs_evaluated": jobs_evaluated,
            "good_matches": good_matches,
            "applications_today": applications_today,
            "quota": quota,
            "stages": counts,
        }


# =========================================================================
# n8n Automation Webhook Endpoints
# =========================================================================

def _verify_n8n_secret(request: Request):
    secret = os.environ.get("N8N_WEBHOOK_SECRET", "n8n-secret-token")
    auth_header = request.headers.get("X-N8N-Secret") or request.headers.get("Authorization", "").replace("Bearer ", "")
    if auth_header != secret:
        raise HTTPException(status_code=401, detail="Invalid N8N Webhook Secret")
    return True


@app.get("/api/webhooks/n8n/new-matches")
def api_n8n_new_matches(limit: int = 20, _: bool = Depends(_verify_n8n_secret)):
    with get_session() as session:
        matches = session.scalars(
            select(JobPosting)
            .where(JobPosting.pipeline_stage == PipelineStage.EVALUATED_MATCH)
            .order_by(desc(JobPosting.match_score), desc(JobPosting.scraped_at))
            .limit(limit)
        ).all()
        result = []
        for j in matches:
            skills = []
            if j.key_skills_matched:
                if isinstance(j.key_skills_matched, list):
                    skills = j.key_skills_matched
                else:
                    try:
                        skills = json.loads(j.key_skills_matched)
                    except Exception:
                        skills = [str(j.key_skills_matched)]
            result.append({
                "id": j.id,
                "title": j.title,
                "company": j.company,
                "location": j.location,
                "score": j.match_score,
                "is_remote": j.is_remote,
                "application_url": j.application_url or j.job_url,
                "pitch_en": j.recruiter_pitch_en,
                "pitch_fr": j.recruiter_pitch_fr,
                "visa_sponsored": j.visa_sponsorship_detected,
                "skills": skills,
            })
        return result


@app.post("/api/webhooks/n8n/trigger-pipeline")
def api_n8n_trigger_pipeline(_: bool = Depends(_verify_n8n_secret)):
    import subprocess
    cmd = ["python", "scripts/run_pipeline.py"]
    subprocess.Popen(cmd)
    return {"ok": True, "message": "Pipeline run triggered via n8n"}


@app.post("/api/webhooks/n8n/notify")
async def api_n8n_notify(request: Request, _: bool = Depends(_verify_n8n_secret)):
    body = await request.json()
    logger.info("Received n8n notification: %s", body)
    return {"ok": True, "received": body}


@app.post("/api/actions/check_inbox")
def api_action_check_inbox(_: bool = Depends(_auth)):
    from src.inbox.inbox_monitor import check_inbox_once
    try:
        summary = check_inbox_once()
        return {"ok": True, "summary": summary}
    except Exception as exc:
        return JSONResponse(status_code=500, content={"ok": False, "error": str(exc)})


@app.get("/api/export/jobs.csv")
def api_export_jobs_csv(request: Request, _: bool = Depends(_auth)):
    """Export all job postings as downloadable CSV."""
    from src.auth.service import get_current_user_optional
    user = get_current_user_optional(request)
    user_id = user.id if user else None
    with get_session() as session:
        query = select(JobPosting)
        if user_id is not None:
            query = query.where(JobPosting.user_id == user_id)
        jobs = session.scalars(query.order_by(desc(JobPosting.scraped_at))).all()
        output = io.StringIO()
        writer = csv.writer(output)
        writer.writerow([
            "ID", "Title", "Company", "Location", "Is Remote", "Continent", "Experience Level",
            "Exp Years Req", "Degree Req", "Visa Category", "Relocation", "Match Score", "Visa Detected",
            "Pipeline Stage", "Evaluation Status", "Evaluation Method", "Application Status",
            "Application URL", "Job URL", "Source Site", "Date Posted", "Scraped At"
        ])
        for j in jobs:
            writer.writerow([
                j.id, j.title, j.company, j.location, j.is_remote,
                getattr(j, "continent", "Europe") or "Europe",
                getattr(j, "experience_level", "entry") or "entry",
                getattr(j, "experience_years_required", "") or "",
                getattr(j, "degree_required", "none") or "none",
                getattr(j, "visa_category", "unspecified") or "unspecified",
                getattr(j, "relocation_detected", False),
                j.match_score, j.visa_sponsorship_detected,
                j.pipeline_stage.value if hasattr(j.pipeline_stage, "value") else str(j.pipeline_stage),
                j.evaluation_status, getattr(j, "evaluation_method", ""), j.application_status,
                j.application_url or "", j.job_url or "", j.source_site or "", j.date_posted or "", _aware(j.scraped_at) or ""
            ])
        output.seek(0)
        filename = f"autohunt_jobs_{datetime.now().strftime('%Y%m%d_%H%M%S')}.csv"
        return StreamingResponse(
            io.BytesIO(output.getvalue().encode("utf-8-sig")),
            media_type="text/csv",
            headers={"Content-Disposition": f"attachment; filename={filename}"}
        )


@app.get("/api/export/freelance.csv")
def api_export_freelance_csv(request: Request, _: bool = Depends(_auth)):
    """Export all freelance leads as downloadable CSV."""
    from src.auth.service import get_current_user_optional
    user = get_current_user_optional(request)
    user_id = user.id if user else None
    with get_session() as session:
        query = select(FreelanceLead)
        if user_id is not None:
            query = query.where(FreelanceLead.user_id == user_id)
        leads = session.scalars(query.order_by(desc(FreelanceLead.discovered_at))).all()
        output = io.StringIO()
        writer = csv.writer(output)
        writer.writerow([
            "ID", "Title", "Client Name", "Platform", "Match Score", "Stage",
            "Budget Estimate", "Currency", "Pitch Status", "Contact Email",
            "Contact URL", "Source URL", "Discovered At"
        ])
        for l in leads:
            writer.writerow([
                l.id, l.title, l.client_name, l.source_platform, l.match_score,
                l.stage.value if hasattr(l.stage, "value") else str(l.stage),
                l.budget_estimate or "", l.currency or "", l.pitch_status or "",
                l.contact_email or "", l.contact_url or "", l.source_url or "", _aware(l.discovered_at) or ""
            ])
        output.seek(0)
        filename = f"autohunt_freelance_{datetime.now().strftime('%Y%m%d_%H%M%S')}.csv"
        return StreamingResponse(
            io.BytesIO(output.getvalue().encode("utf-8-sig")),
            media_type="text/csv",
            headers={"Content-Disposition": f"attachment; filename={filename}"}
        )


# ---------------------------------------------------------------------------
# Freelance & Client Acquisition API

# ---------------------------------------------------------------------------

class FreelanceStageUpdate(BaseModel):
    stage: str


class PitchGenerateRequest(BaseModel):
    angle: str = "mvp_speed"


class FollowUpGenerateRequest(BaseModel):
    follow_up_number: int | None = None


class PitchUpdate(BaseModel):
    subject: str | None = None
    pitch_body: str | None = None


class DealUpdate(BaseModel):
    offer_amount: float | None = None
    currency: str | None = None
    offer_terms: str | None = None
    counter_offer_notes: str | None = None
    stage: str | None = None


def _lead_dict(lead: FreelanceLead) -> dict:
    return {
        "id": lead.id,
        "title": lead.title,
        "client_name": lead.client_name,
        "client_type": lead.client_type,
        "contact_email": lead.contact_email,
        "contact_url": lead.contact_url,
        "source_platform": lead.source_platform,
        "source_url": lead.source_url,
        "budget_estimate": lead.budget_estimate,
        "currency": lead.currency,
        "project_scope": lead.project_scope,
        "match_score": lead.match_score,
        "evaluation_reason": lead.evaluation_reason,
        "evaluated": lead.evaluated,
        "is_spam": lead.is_spam,
        "pitch_subject": lead.pitch_subject,
        "pitch_body": lead.pitch_body,
        "pitch_status": lead.pitch_status,
        "stage": lead.stage.value if lead.stage else "discovered",
        "last_contact_at": _aware(lead.last_contact_at),
        "follow_up_count": lead.follow_up_count,
        "next_follow_up_due": _aware(lead.next_follow_up_due),
        "offer_amount": lead.offer_amount,
        "offer_terms": lead.offer_terms,
        "counter_offer_notes": lead.counter_offer_notes,
        "discovered_at": _aware(lead.discovered_at),
        "updated_at": _aware(lead.updated_at),
        "description": lead.raw_description,
    }


@app.get("/api/freelance/stats")
def api_freelance_stats(request: Request, _: bool = Depends(_auth)):
    from src.auth.service import get_current_user_optional
    from src.freelance.outreach import get_leads_needing_follow_up
    user = get_current_user_optional(request)
    user_id = user.id if user else None
    with get_session() as session:
        def _scope_fl(stmt):
            if user_id is not None:
                return stmt.where(FreelanceLead.user_id == user_id)
            return stmt

        total = session.scalar(_scope_fl(select(func.count(FreelanceLead.id)))) or 0
        by_stage_query = select(FreelanceLead.stage, func.count())
        if user_id is not None:
            by_stage_query = by_stage_query.where(FreelanceLead.user_id == user_id)
        by_stage = {
            stage.value: int(cnt) for stage, cnt in
            session.execute(by_stage_query.group_by(FreelanceLead.stage)).all()
        }
        evaluated = session.scalar(_scope_fl(select(func.count(FreelanceLead.id)).where(FreelanceLead.evaluated.is_(True)))) or 0
        spam = session.scalar(_scope_fl(select(func.count(FreelanceLead.id)).where(FreelanceLead.is_spam.is_(True)))) or 0
        pitched = by_stage.get("pitched", 0)
        in_discussion = by_stage.get("in_discussion", 0)
        offers = by_stage.get("offer_received", 0)
        won = by_stage.get("deal_won", 0)
        avg_score = session.scalar(_scope_fl(select(func.avg(FreelanceLead.match_score)).where(FreelanceLead.evaluated.is_(True)))) or 0
        due_follow_ups = len(get_leads_needing_follow_up(session, user_id=user_id))
        return {
            "total_leads": total,
            "by_stage": by_stage,
            "evaluated": evaluated,
            "spam_filtered": spam,
            "active_pitches": pitched,
            "in_discussion": in_discussion,
            "offers_received": offers,
            "deals_won": won,
            "average_score": round(float(avg_score), 1),
            "needs_follow_up": due_follow_ups,
        }


@app.get("/api/freelance/leads")
def api_freelance_leads(request: Request, q: str | None = None, stage: str | None = None,
                        needs_follow_up: bool = False,
                        limit: int = 100, _: bool = Depends(_auth)):
    from src.auth.service import get_current_user_optional
    user = get_current_user_optional(request)
    user_id = user.id if user else None
    limit = max(1, min(limit, 500))
    with get_session() as session:
        query = select(FreelanceLead)
        if user_id is not None:
            query = query.where(FreelanceLead.user_id == user_id)
        if needs_follow_up:
            from src.freelance.outreach import get_leads_needing_follow_up
            due_ids = get_leads_needing_follow_up(session, user_id=user_id)
            if not due_ids:
                return []
            query = query.where(FreelanceLead.id.in_(due_ids))
        elif stage:
            try:
                query = query.where(FreelanceLead.stage == FreelanceStage(stage))
            except ValueError:
                raise HTTPException(400, "Invalid stage")
        if q:
            needle = f"%{q.strip()}%"
            query = query.where(
                or_(FreelanceLead.title.ilike(needle),
                    FreelanceLead.client_name.ilike(needle),
                    FreelanceLead.raw_description.ilike(needle),
                    FreelanceLead.source_platform.ilike(needle))
            )
        leads = session.scalars(query.order_by(desc(FreelanceLead.discovered_at), desc(FreelanceLead.id)).limit(limit)).all()
        return [_lead_dict(l) for l in leads]


@app.get("/api/freelance/leads/{lead_id}")
def api_freelance_lead(lead_id: int, request: Request, _: bool = Depends(_auth)):
    from src.auth.service import get_current_user_optional
    user = get_current_user_optional(request)
    user_id = user.id if user else None
    with get_session() as session:
        lead = session.get(FreelanceLead, lead_id)
        if not lead:
            raise HTTPException(404, "Lead not found")
        if user_id is not None and lead.user_id is not None and lead.user_id != user_id and getattr(user, "role", None) != "admin":
            raise HTTPException(403, "Not authorized to access this lead")
        data = _lead_dict(lead)
        data["messages"] = [{
            "id": m.id, "direction": m.direction, "message_type": m.message_type,
            "subject": m.subject, "body": m.body, "sent_at": _aware(m.sent_at),
            "created_at": _aware(m.created_at),
        } for m in sorted(lead.messages, key=lambda x: x.created_at)]
        return data


@app.get("/api/freelance/leads/{lead_id}/formats")
def api_freelance_lead_formats(lead_id: int, request: Request, _: bool = Depends(_auth)):
    """Retrieve all copy-paste ready multi-channel pitch formats (Email, LinkedIn, Chat DM, Follow-ups)."""
    from src.auth.service import get_current_user_optional
    user = get_current_user_optional(request)
    user_id = user.id if user else None
    with get_session() as session:
        lead = session.get(FreelanceLead, lead_id)
        if not lead:
            raise HTTPException(404, "Lead not found")
        if user_id is not None and lead.user_id is not None and lead.user_id != user_id and getattr(user, "role", None) != "admin":
            raise HTTPException(403, "Not authorized to access this lead")

    from src.freelance.pitch_generator import get_all_pitch_formats
    res = get_all_pitch_formats(lead_id)
    if not res:
        raise HTTPException(404, "Lead not found")
    return {"ok": True, **res}


_freelance_lock = threading.Lock()
_freelance_thread: threading.Thread | None = None
_freelance_status = {"status": "idle", "message": "Ready"}


@app.post("/api/freelance/discover")
def api_freelance_discover(request: Request, background: bool = True, _: bool = Depends(_auth)):
    global _freelance_thread, _freelance_status
    from src.auth.service import get_current_user_optional
    user = get_current_user_optional(request)
    user_id = user.id if user else None

    with _freelance_lock:
        if _freelance_thread and _freelance_thread.is_alive():
            return JSONResponse(status_code=409, content={"ok": False, "message": "Freelance discovery is already running", "status": _freelance_status})

        from src.freelance.orchestrator import run_freelance_pipeline

        if not background:
            try:
                res = run_freelance_pipeline(user_id=user_id)
                return {"ok": True, "summary": res}
            except Exception as exc:
                return JSONResponse(status_code=500, content={"ok": False, "error": str(exc)})

        def _worker(target_uid: int | None):
            global _freelance_status
            _freelance_status = {"status": "running", "message": "Scanning client sources (Hacker News, Reddit, RemoteOK, Jobicy)..."}
            try:
                res = run_freelance_pipeline(user_id=target_uid)
                _freelance_status = {
                    "status": "completed",
                    "message": f"Discovery finished. Scanned {res.get('discovered', 0)} leads, {res.get('new_leads', 0)} new.",
                    "summary": res,
                }
            except Exception as exc:
                logging.getLogger("src.dashboard").exception("Freelance discovery error: %s", exc)
                _freelance_status = {"status": "error", "message": str(exc)}

        _freelance_thread = threading.Thread(target=_worker, args=(user_id,), daemon=True, name="freelance-discovery-worker")
        _freelance_thread.start()
        return {"ok": True, "message": "Freelance client discovery started in background"}


@app.get("/api/freelance/status")
def api_freelance_status(_: bool = Depends(_auth)):
    global _freelance_status
    return _freelance_status


@app.post("/api/freelance/batch_evaluate")
def api_freelance_batch_evaluate(request: Request, limit: int = 50, _: bool = Depends(_auth)):
    from src.auth.service import get_current_user_optional
    from src.freelance.evaluation import evaluate_pending_leads
    user = get_current_user_optional(request)
    user_id = user.id if user else None
    try:
        summary = evaluate_pending_leads(limit=limit, user_id=user_id)
        return {"ok": True, "summary": summary}
    except Exception as exc:
        return JSONResponse(status_code=500, content={"ok": False, "error": str(exc)})


@app.post("/api/freelance/batch_pitch")
def api_freelance_batch_pitch(request: Request, min_score: int = 70, limit: int = 15, angle: str = "mvp_speed", _: bool = Depends(_auth)):
    from src.auth.service import get_current_user_optional
    from src.freelance.pitch_generator import generate_pitch
    user = get_current_user_optional(request)
    user_id = user.id if user else None
    pitched = 0
    with get_session() as session:
        q = session.query(FreelanceLead).filter(
            FreelanceLead.stage == FreelanceStage.EVALUATED,
            FreelanceLead.is_spam.is_(False),
            FreelanceLead.match_score >= min_score,
            FreelanceLead.pitch_status != "sent",
        )
        if user_id is not None:
            q = q.filter(FreelanceLead.user_id == user_id)
        leads = q.order_by(desc(FreelanceLead.match_score)).limit(limit).all()
        lead_ids = [l.id for l in leads]

    for lid in lead_ids:
        res = generate_pitch(lid, angle=angle)
        if res == "generated":
            pitched += 1

    return {"ok": True, "pitched_count": pitched, "total_eligible": len(lead_ids)}


@app.post("/api/freelance/leads/{lead_id}/pitch")
def api_freelance_pitch(lead_id: int, request: Request, payload: PitchGenerateRequest | None = None, angle: str = "mvp_speed", _: bool = Depends(_auth)):
    from src.auth.service import get_current_user_optional
    from src.freelance.pitch_generator import generate_pitch
    user = get_current_user_optional(request)
    user_id = user.id if user else None
    with get_session() as session:
        lead = session.get(FreelanceLead, lead_id)
        if not lead:
            raise HTTPException(404, "Lead not found")
        if user_id is not None and lead.user_id is not None and lead.user_id != user_id and getattr(user, "role", None) != "admin":
            raise HTTPException(403, "Not authorized to access this lead")

    selected_angle = (payload.angle if payload else None) or angle or "mvp_speed"
    result = generate_pitch(lead_id, angle=selected_angle)
    with get_session() as session:
        lead = session.get(FreelanceLead, lead_id)
        if not lead:
            raise HTTPException(404, "Lead not found")
        return {"ok": result in ("generated", "already_sent"), "status": result, "lead": _lead_dict(lead), "angle": selected_angle}


@app.post("/api/freelance/leads/{lead_id}/send")
def api_freelance_send(lead_id: int, request: Request, _: bool = Depends(_auth)):
    from src.auth.service import get_current_user_optional
    from src.freelance.outreach import send_pitch
    user = get_current_user_optional(request)
    user_id = user.id if user else None
    with get_session() as session:
        lead = session.get(FreelanceLead, lead_id)
        if not lead:
            raise HTTPException(404, "Lead not found")
        if user_id is not None and lead.user_id is not None and lead.user_id != user_id and getattr(user, "role", None) != "admin":
            raise HTTPException(403, "Not authorized to access this lead")

    result = send_pitch(lead_id)
    with get_session() as session:
        lead = session.get(FreelanceLead, lead_id)
        if not lead:
            raise HTTPException(404, "Lead not found")
        return {"ok": result == "sent", "status": result, "lead": _lead_dict(lead)}


@app.post("/api/freelance/leads/{lead_id}/follow_up")
def api_freelance_follow_up(lead_id: int, request: Request, payload: FollowUpGenerateRequest | None = None, follow_up_number: int | None = None, _: bool = Depends(_auth)):
    from src.auth.service import get_current_user_optional
    from src.freelance.pitch_generator import generate_follow_up
    user = get_current_user_optional(request)
    user_id = user.id if user else None
    with get_session() as session:
        lead = session.get(FreelanceLead, lead_id)
        if not lead:
            raise HTTPException(404, "Lead not found")
        if user_id is not None and lead.user_id is not None and lead.user_id != user_id and getattr(user, "role", None) != "admin":
            raise HTTPException(403, "Not authorized to access this lead")
        target_num = (payload.follow_up_number if payload else None) or follow_up_number or (lead.follow_up_count + 1)

    result = generate_follow_up(lead_id, follow_up_number=target_num)
    with get_session() as session:
        lead = session.get(FreelanceLead, lead_id)
        if not lead:
            raise HTTPException(404, "Lead not found")
        msg = session.query(FreelanceMessage).filter(
            FreelanceMessage.lead_id == lead_id,
            FreelanceMessage.message_type == f"follow_up_{target_num}",
        ).order_by(FreelanceMessage.created_at.desc()).first()
        msg_dict = {
            "id": msg.id, "subject": msg.subject, "body": msg.body,
            "message_type": msg.message_type, "created_at": _aware(msg.created_at)
        } if msg else None

        return {
            "ok": result in ("generated", "already_generated"),
            "status": result,
            "follow_up_number": target_num,
            "lead": _lead_dict(lead),
            "message": msg_dict
        }


@app.post("/api/freelance/batch_follow_up")
def api_freelance_batch_follow_up(request: Request, _: bool = Depends(_auth)):
    from src.auth.service import get_current_user_optional
    from src.freelance.outreach import process_follow_ups
    user = get_current_user_optional(request)
    user_id = user.id if user else None
    try:
        summary = process_follow_ups(user_id=user_id)
        return {"ok": True, "summary": summary}
    except Exception as exc:
        return JSONResponse(status_code=500, content={"ok": False, "error": str(exc)})


@app.patch("/api/freelance/leads/{lead_id}/stage")
def api_freelance_stage(lead_id: int, payload: FreelanceStageUpdate, request: Request, _: bool = Depends(_auth)):
    from src.auth.service import get_current_user_optional
    user = get_current_user_optional(request)
    user_id = user.id if user else None
    try:
        stage = FreelanceStage(payload.stage)
    except ValueError:
        raise HTTPException(400, "Invalid stage")
    with get_session() as session:
        lead = session.get(FreelanceLead, lead_id)
        if not lead:
            raise HTTPException(404, "Lead not found")
        if user_id is not None and lead.user_id is not None and lead.user_id != user_id and getattr(user, "role", None) != "admin":
            raise HTTPException(403, "Not authorized to access this lead")
        lead.stage = stage
        lead.updated_at = datetime.now(timezone.utc)
        return {"ok": True, "lead": _lead_dict(lead)}


@app.patch("/api/freelance/leads/{lead_id}/pitch")
def api_freelance_update_pitch(lead_id: int, payload: PitchUpdate, request: Request, _: bool = Depends(_auth)):
    from src.auth.service import get_current_user_optional
    user = get_current_user_optional(request)
    user_id = user.id if user else None
    with get_session() as session:
        lead = session.get(FreelanceLead, lead_id)
        if not lead:
            raise HTTPException(404, "Lead not found")
        if user_id is not None and lead.user_id is not None and lead.user_id != user_id and getattr(user, "role", None) != "admin":
            raise HTTPException(403, "Not authorized to access this lead")
        if payload.subject is not None:
            lead.pitch_subject = payload.subject.strip()
        if payload.pitch_body is not None:
            lead.pitch_body = payload.pitch_body.strip()
        lead.updated_at = datetime.now(timezone.utc)
        return {"ok": True, "lead": _lead_dict(lead)}


@app.patch("/api/freelance/leads/{lead_id}/deal")
def api_freelance_update_deal(lead_id: int, payload: DealUpdate, request: Request, _: bool = Depends(_auth)):
    from src.auth.service import get_current_user_optional
    user = get_current_user_optional(request)
    user_id = user.id if user else None
    with get_session() as session:
        lead = session.get(FreelanceLead, lead_id)
        if not lead:
            raise HTTPException(404, "Lead not found")
        if user_id is not None and lead.user_id is not None and lead.user_id != user_id and getattr(user, "role", None) != "admin":
            raise HTTPException(403, "Not authorized to access this lead")
        if payload.offer_amount is not None:
            lead.offer_amount = payload.offer_amount
        if payload.currency is not None:
            lead.currency = payload.currency.strip()
        if payload.offer_terms is not None:
            lead.offer_terms = payload.offer_terms.strip()
        if payload.counter_offer_notes is not None:
            lead.counter_offer_notes = payload.counter_offer_notes.strip()
        if payload.stage is not None:
            try:
                lead.stage = FreelanceStage(payload.stage)
            except ValueError:
                raise HTTPException(400, "Invalid stage")
        lead.updated_at = datetime.now(timezone.utc)
        return {"ok": True, "lead": _lead_dict(lead)}


TEMPLATES_DIR = Path(__file__).parent / "templates"


def _render_template(default_tab: str = "jobs") -> str:
    tpl_file = TEMPLATES_DIR / "dashboard.html"
    if tpl_file.exists():
        raw = tpl_file.read_text(encoding="utf-8")
        raw = raw.replace("{{DEFAULT_TAB}}", default_tab)
        if default_tab == "jobs":
            raw = raw.replace('id="modeFreelance" class="mode-btn active"', 'id="modeFreelance" class="mode-btn"')
            raw = raw.replace('id="modeJobs" class="mode-btn"', 'id="modeJobs" class="mode-btn active"')
            raw = raw.replace('id="freelanceView" class="view-section active"', 'id="freelanceView" class="view-section"')
            raw = raw.replace('id="jobsView" class="view-section"', 'id="jobsView" class="view-section active"')
        return raw
    return DASHBOARD_HTML if default_tab == "jobs" else FREELANCE_HTML


# ---------------------------------------------------------------------------
# n8n Webhook & Dynamic Stats API
# ---------------------------------------------------------------------------

import os as _os
_N8N_WEBHOOK_SECRET = _os.environ.get("N8N_WEBHOOK_SECRET", "")


def _verify_n8n_secret(request: Request):
    """Verify n8n webhook secret from header or query param."""
    if not _N8N_WEBHOOK_SECRET:
        return True  # No secret configured — allow all (dev mode)
    token = request.headers.get("x-webhook-secret") or request.query_params.get("secret")
    if not token or not secrets.compare_digest(token, _N8N_WEBHOOK_SECRET):
        raise HTTPException(status_code=403, detail="Invalid webhook secret")
    return True


@app.get("/api/stats/dynamic")
@limiter.limit("30/minute")
def api_stats_dynamic(request: Request, _: bool = Depends(_auth)):
    """Rich, real-time stats for dynamic KPI dashboards and n8n polling."""
    from src.auth.service import get_current_user_optional
    user = get_current_user_optional(request)
    user_id = user.id if user else None

    with get_session() as session:
        def _scope_st(stmt):
            if user_id is not None:
                return stmt.where(JobPosting.user_id == user_id)
            return stmt

        now = datetime.now(timezone.utc)
        day_start = now.replace(hour=0, minute=0, second=0, microsecond=0)
        hour_start = now - timedelta(hours=1)

        # Stage breakdown
        stage_counts = _stage_counts(session, user_id=user_id)

        # Time-based metrics
        scraped_last_hour = session.scalar(
            _scope_st(select(func.count(JobPosting.id)).where(JobPosting.scraped_at >= hour_start))
        ) or 0
        scraped_today = session.scalar(
            _scope_st(select(func.count(JobPosting.id)).where(JobPosting.scraped_at >= day_start))
        ) or 0

        total = session.scalar(_scope_st(select(func.count(JobPosting.id)))) or 0
        evaluated = session.scalar(
            _scope_st(select(func.count(JobPosting.id)).where(JobPosting.evaluated.is_(True)))
        ) or 0
        good_matches = session.scalar(
            _scope_st(select(func.count(JobPosting.id)).where(
                JobPosting.pipeline_stage == PipelineStage.EVALUATED_MATCH
            ))
        ) or 0
        applied = session.scalar(
            _scope_st(select(func.count(JobPosting.id)).where(JobPosting.applied_at.isnot(None)))
        ) or 0
        applications_today = session.scalar(
            _scope_st(select(func.count(JobPosting.id)).where(JobPosting.applied_at >= day_start))
        ) or 0

        replies_q = select(func.count(EmailEvent.id)).where(
            EmailEvent.direction == "inbound", EmailEvent.created_at >= day_start
        )
        if user_id is not None:
            replies_q = replies_q.join(JobPosting, EmailEvent.job_id == JobPosting.id).where(JobPosting.user_id == user_id)
        replies_today = session.scalar(replies_q) or 0


        # Conversion rates
        eval_rate = round(evaluated / total * 100, 1) if total else 0
        match_rate = round(good_matches / evaluated * 100, 1) if evaluated else 0
        apply_rate = round(applied / good_matches * 100, 1) if good_matches else 0

        # Source breakdown
        source_rows = session.execute(
            _scope_st(select(JobPosting.source_site, func.count())).group_by(JobPosting.source_site)
        ).all()
        sources = {str(k or "unknown"): int(v) for k, v in source_rows}

        # Continent breakdown
        continent_rows = session.execute(
            _scope_st(select(JobPosting.continent, func.count())).group_by(JobPosting.continent)
        ).all()
        continents = {str(k or "Unknown"): int(v) for k, v in continent_rows}

        # AI provider status
        quota = quota_snapshot(session)

        # Latest run
        run_query = select(PipelineRun)
        if user_id is not None:
            run_query = run_query.where(PipelineRun.user_id == user_id)
        latest_run = session.scalar(
            run_query.order_by(desc(PipelineRun.started_at)).limit(1)
        )
        run_info = None
        if latest_run:
            run_info = {
                "id": latest_run.id,
                "status": latest_run.status,
                "started_at": _aware(latest_run.started_at),
                "finished_at": _aware(latest_run.finished_at),
                "raw_scraped": latest_run.raw_scraped,
                "new_postings": latest_run.new_postings,
                "evaluated": latest_run.evaluated,
            }

        return {
            "server_utc": now.isoformat(),
            "kpis": {
                "total_postings": total,
                "evaluated": evaluated,
                "good_matches": good_matches,
                "applied_total": applied,
                "applications_today": applications_today,
                "replies_today": replies_today,
                "scraped_today": scraped_today,
                "scraped_last_hour": scraped_last_hour,
            },
            "conversion": {
                "evaluation_rate": eval_rate,
                "match_rate": match_rate,
                "apply_rate": apply_rate,
            },
            "stages": stage_counts,
            "sources": sources,
            "continents": continents,
            "quota": quota,
            "latest_run": run_info,
        }


@app.post("/api/webhook/n8n/notify")
def n8n_webhook_notify(request: Request, body: dict = {}):
    """Receive notifications from n8n workflows (e.g., external events, triggers)."""
    _verify_n8n_secret(request)
    event_type = body.get("event", "unknown")
    payload = body.get("data", {})
    logging.getLogger("src.dashboard.n8n").info(
        "n8n webhook received: event=%s payload_keys=%s", event_type, list(payload.keys()) if isinstance(payload, dict) else "N/A"
    )
    if event_type == "telegram_digest" or body.get("send_telegram"):
        text = payload.get("message") or payload.get("digest")
        if text and settings.telegram_bot_token and settings.telegram_chat_id:
            try:
                import requests
                from src.alerting.telegram_bot import TELEGRAM_API
                url = TELEGRAM_API.format(token=settings.telegram_bot_token)
                requests.post(url, json={
                    "chat_id": settings.telegram_chat_id,
                    "text": text,
                    "parse_mode": "Markdown",
                    "disable_web_page_preview": False
                }, timeout=10)
            except Exception as e:
                logging.getLogger("src.dashboard.n8n").warning("Failed to dispatch telegram digest: %s", e)

    return {"ok": True, "event": event_type, "received_at": datetime.now(timezone.utc).isoformat()}


@app.post("/api/webhook/n8n/trigger-pipeline")
def n8n_trigger_pipeline(request: Request):
    """Allow n8n to trigger a pipeline run via webhook."""
    _verify_n8n_secret(request)
    global _pipeline_thread
    with _pipeline_lock:
        if _pipeline_thread and _pipeline_thread.is_alive():
            return JSONResponse(status_code=409, content={"ok": False, "message": "Pipeline already running"})

        from src.orchestrator import run_pipeline

        def _bg_worker():
            try:
                run_pipeline()
            except Exception as exc:
                logging.getLogger("src.dashboard.n8n").exception("n8n-triggered pipeline error: %s", exc)

        _pipeline_thread = threading.Thread(target=_bg_worker, daemon=True, name="n8n-pipeline-worker")
        _pipeline_thread.start()
        return {"ok": True, "message": "Pipeline triggered by n8n", "started_at": datetime.now(timezone.utc).isoformat()}


@app.get("/api/webhook/n8n/new-matches")
def n8n_new_matches(request: Request, since_hours: int = 24, min_score: int = 70):
    """Return recent high-scoring job matches for n8n workflows (notifications, sheets sync, etc.)."""
    _verify_n8n_secret(request)
    cutoff = datetime.now(timezone.utc) - timedelta(hours=since_hours)
    with get_session() as session:
        jobs = session.scalars(
            select(JobPosting)
            .where(
                JobPosting.scraped_at >= cutoff,
                JobPosting.match_score >= min_score,
                JobPosting.pipeline_stage.in_([
                    PipelineStage.EVALUATED_MATCH,
                    PipelineStage.APPLIED,
                ]),
            )
            .order_by(desc(JobPosting.match_score))
            .limit(50)
        ).all()
        interviews = session.scalars(
            select(JobPosting)
            .where(
                JobPosting.pipeline_stage.in_([
                    PipelineStage.INTERVIEW_SCHEDULED,
                    PipelineStage.INTERVIEW_REQUESTED,
                ])
            )
            .order_by(desc(JobPosting.last_contact_at), desc(JobPosting.id))
            .limit(20)
        ).all()
        return {
            "count": len(jobs),
            "since_utc": cutoff.isoformat(),
            "min_score": min_score,
            "telegram_configured": bool(settings.telegram_bot_token and settings.telegram_chat_id),
            "telegram_notify_only_interviews": getattr(settings, "telegram_notify_only_interviews", True),
            "telegram_bot_token": settings.telegram_bot_token,
            "telegram_chat_id": settings.telegram_chat_id,
            "has_interviews": len(interviews) > 0,
            "interview_count": len(interviews),
            "interviews": [
                {
                    "id": i.id,
                    "title": i.title,
                    "company": i.company,
                    "location": i.location,
                    "stage": i.pipeline_stage.value,
                    "interview_notes": i.interview_notes,
                    "application_url": i.application_url or i.job_url,
                    "last_contact_at": _aware(i.last_contact_at),
                }
                for i in interviews
            ],
            "matches": [
                {
                    "id": j.id,
                    "title": j.title,
                    "company": j.company,
                    "location": j.location,
                    "score": j.match_score,
                    "visa_category": getattr(j, "visa_category", None),
                    "stage": j.pipeline_stage.value,
                    "job_url": j.job_url,
                    "application_url": j.application_url,
                    "scraped_at": _aware(j.scraped_at),
                }
                for j in jobs
            ],
        }


@app.get("/api/webhook/n8n/interviews")
def n8n_interviews(request: Request):
    """Return all jobs with scheduled or requested interviews for n8n notification workflows."""
    _verify_n8n_secret(request)
    with get_session() as session:
        interviews = session.scalars(
            select(JobPosting)
            .where(
                JobPosting.pipeline_stage.in_([
                    PipelineStage.INTERVIEW_SCHEDULED,
                    PipelineStage.INTERVIEW_REQUESTED,
                ])
            )
            .order_by(desc(JobPosting.last_contact_at), desc(JobPosting.id))
            .limit(50)
        ).all()
        return {
            "count": len(interviews),
            "has_interviews": len(interviews) > 0,
            "telegram_configured": bool(settings.telegram_bot_token and settings.telegram_chat_id),
            "telegram_notify_only_interviews": getattr(settings, "telegram_notify_only_interviews", True),
            "telegram_bot_token": settings.telegram_bot_token,
            "telegram_chat_id": settings.telegram_chat_id,
            "interviews": [
                {
                    "id": i.id,
                    "title": i.title,
                    "company": i.company,
                    "location": i.location,
                    "stage": i.pipeline_stage.value,
                    "notes": i.interview_notes,
                    "application_url": i.application_url or i.job_url,
                    "last_contact_at": _aware(i.last_contact_at),
                }
                for i in interviews
            ],
        }


@app.get("/api/webhook/n8n/status")
def n8n_system_status(request: Request):
    """Health & status endpoint for n8n to verify connectivity and check system state."""
    _verify_n8n_secret(request)
    with get_session() as session:
        total = session.scalar(select(func.count(JobPosting.id))) or 0
        latest_run = session.scalar(
            select(PipelineRun).order_by(desc(PipelineRun.started_at)).limit(1)
        )
        return {
            "status": "ok",
            "version": "3.2",
            "utc": datetime.now(timezone.utc).isoformat(),
            "total_jobs": total,
            "latest_run_status": latest_run.status if latest_run else "none",
            "latest_run_at": _aware(latest_run.started_at) if latest_run else None,
            "pipeline_running": bool(_pipeline_thread and _pipeline_thread.is_alive()),
        }

@app.get("/", response_class=HTMLResponse)
def landing_page(request: Request):
    from src.auth.service import get_current_user_optional
    from fastapi.responses import RedirectResponse
    user = get_current_user_optional(request)
    if user:
        return RedirectResponse(url="/app", status_code=302)
    tpl_file = TEMPLATES_DIR / "landing.html"
    if tpl_file.exists():
        return HTMLResponse(tpl_file.read_text(encoding="utf-8"))
    return HTMLResponse("<h1>AutoHunt AI — Stop Chasing Recruiters</h1>")


@app.get("/app", response_class=HTMLResponse)
def dashboard_app(request: Request):
    from src.auth.service import get_current_user_optional
    from fastapi.responses import RedirectResponse
    user = get_current_user_optional(request)
    if not user:
        return RedirectResponse(url="/", status_code=302)
    return HTMLResponse(_render_template(default_tab="jobs"))


@app.get("/freelance", response_class=HTMLResponse)
def freelance_dashboard(request: Request):
    from src.auth.service import get_current_user_optional
    from fastapi.responses import RedirectResponse
    user = get_current_user_optional(request)
    if not user:
        return RedirectResponse(url="/", status_code=302)
    return HTMLResponse(_render_template(default_tab="freelance"))


DASHBOARD_HTML = r'''<!doctype html>
<html><head><meta charset="utf-8"><meta name="viewport" content="width=device-width,initial-scale=1">
<title>Job Search Admin</title>
<style>
:root{--bg:#08101f;--panel:#0f1a2e;--line:#223252;--text:#edf2ff;--muted:#8fa0be;--accent:#76a9ff;--good:#56d39b;--warn:#ffcc66;--bad:#ff7474}
*{box-sizing:border-box}body{margin:0;font-family:Inter,Segoe UI,system-ui,sans-serif;background:var(--bg);color:var(--text)}
.wrap{max-width:1600px;margin:auto;padding:20px}.top{display:flex;justify-content:space-between;gap:16px;align-items:flex-start;flex-wrap:wrap}.muted{color:var(--muted)}
nav{display:flex;gap:8px;overflow:auto;padding:12px 0;position:sticky;top:0;background:linear-gradient(var(--bg) 85%,transparent);z-index:5}nav button{white-space:nowrap}
button,input,select,textarea{background:#101d34;border:1px solid var(--line);color:var(--text);border-radius:9px;padding:9px 11px}button{cursor:pointer}button:hover{border-color:var(--accent)}
.grid{display:grid;grid-template-columns:repeat(auto-fit,minmax(180px,1fr));gap:12px}.card,.panel{background:var(--panel);border:1px solid var(--line);border-radius:14px;padding:15px}.big{font-size:25px;font-weight:800;margin-top:7px}.kpi-good{border-color:#285e4b}.kpi-warn{border-color:#665225}.kpi-bad{border-color:#68383c}
.panel{margin-top:14px}.toolbar{display:flex;gap:8px;flex-wrap:wrap;align-items:center}.toolbar .grow{flex:1;min-width:220px}
table{width:100%;border-collapse:collapse}th,td{padding:9px 8px;border-bottom:1px solid #1b2945;text-align:left;font-size:13px;vertical-align:top}th{color:var(--muted);position:sticky;top:67px;background:var(--panel)}tr.click:hover{background:#13213a;cursor:pointer}.progressbar{height:10px;background:#0a1325;border:1px solid var(--line);border-radius:999px;overflow:hidden}.progressbar>div{height:100%;background:var(--accent);transition:width .3s ease}.pill{display:inline-block;padding:4px 8px;border-radius:999px;background:#182744;margin:2px}.good{color:var(--good)}.warn{color:var(--warn)}.bad{color:var(--bad)}
section{display:none}section.active{display:block}.detail{white-space:pre-wrap;max-height:460px;overflow:auto;background:#0a1325;padding:12px;border-radius:10px}.split{display:grid;grid-template-columns:1fr 1fr;gap:12px}@media(max-width:900px){.split{grid-template-columns:1fr}}
.small{font-size:12px}.right{text-align:right}.hidden{display:none}.status-dot{font-weight:700}
</style></head><body><div class="wrap">
<div class="top"><div><h1 style="margin:.2em 0">Job Search Admin</h1><div class="muted">Live operational view • refreshes every 10 seconds • all sensitive credentials remain hidden</div></div><div class="toolbar"><a href="/freelance" style="color:var(--accent);text-decoration:none;border:1px solid var(--accent);border-radius:9px;padding:9px 14px;font-weight:600">🚀 Freelance Hub</a><span id="clock" class="muted"></span><button onclick="refreshAll()">Refresh now</button></div></div>
<nav><button data-tab="overview">Overview</button><button data-tab="jobs">Jobs</button><button data-tab="inbox">Inbox</button><button data-tab="outbound">Outreach</button><button data-tab="runs">Pipeline</button><button data-tab="system">System</button></nav>
<section id="overview" class="active"><div id="overviewKpi" class="grid"></div><div class="split"><div class="panel"><h2>Pipeline now</h2><div id="stageGrid" class="grid"></div></div><div class="panel"><h2>AI providers</h2><div id="providers"></div></div></div><div class="panel"><h2>Live scraping & pipeline progress</h2><div id="scrapeProgress"></div><div style="overflow:auto;margin-top:12px"><table><thead><tr><th>Source / Query</th><th>Sites</th><th>Jobs</th><th>Status</th><th>Duration</th><th>Error</th></tr></thead><tbody id="scrapeSources"></tbody></table></div></div><div class="panel"><h2>Current run</h2><div id="latestRun"></div></div></section>
<section id="jobs"><div class="panel"><div class="toolbar"><input id="jobSearch" class="grow" placeholder="Search title, company, location or email"><select id="jobStage"><option value="">All stages</option></select><button onclick="loadJobs()">Search</button></div></div><div class="panel"><div style="overflow:auto"><table><thead><tr><th>ID</th><th>Role</th><th>Company</th><th>Location</th><th>Score</th><th>Visa</th><th>Stage</th><th>Eval</th><th>Application</th></tr></thead><tbody id="jobsTable"></tbody></table></div></div><div id="jobDetail" class="panel hidden"></div></section>
<section id="inbox"><div class="panel"><div class="toolbar"><select id="inboxStatus"><option value="">All statuses</option><option>matched</option><option>unmatched</option><option>duplicate</option></select><button onclick="loadInbox()">Refresh</button></div></div><div class="panel"><div style="overflow:auto"><table><thead><tr><th>Time</th><th>Status</th><th>Sender</th><th>Subject</th><th>Reason</th><th>Message ID</th></tr></thead><tbody id="inboxTable"></tbody></table></div></div></section>
<section id="outbound"><div id="outboundKpi" class="grid"></div><div class="panel"><h2>Outbound ledger</h2><div class="toolbar"><select id="outStatus"><option value="">All</option><option>pending</option><option>sending</option><option>sent</option><option>failed</option><option>unknown</option></select><button onclick="loadOutbound()">Refresh</button></div><div style="overflow:auto;margin-top:10px"><table><thead><tr><th>Time</th><th>Type</th><th>Recipient</th><th>Subject</th><th>Status</th><th>Failure</th><th>Job</th></tr></thead><tbody id="outboundTable"></tbody></table></div></div><div class="panel"><h2>Suppression list</h2><div class="toolbar"><input id="supEmail" placeholder="email"><input id="supDomain" placeholder="domain"><input id="supReason" value="manual" placeholder="reason"><input id="supDetails" class="grow" placeholder="details"><button onclick="addSuppression()">Suppress</button></div><div id="suppressions" style="margin-top:10px"></div></div></section>
<section id="runs"><div class="panel"><h2>Pipeline runs</h2><button onclick="loadRuns()">Refresh</button><div style="overflow:auto;margin-top:10px"><table><thead><tr><th>ID</th><th>Started</th><th>Status</th><th>Scraped</th><th>New</th><th>Evaluated</th><th>AI</th><th>Scrape errors</th><th>Eval errors</th><th>Error</th></tr></thead><tbody id="runsTable"></tbody></table></div></div></section>
<section id="system"><div class="split"><div class="panel"><h2>Configuration</h2><div id="config"></div></div><div class="panel"><h2>AI/provider state</h2><div id="providerState"></div></div></div><div class="panel"><h2>Email safety</h2><div id="emailSafety"></div></div></section>
</div>
<script>
const esc=s=>String(s??'').replace(/[&<>"']/g,c=>({'&':'&amp;','<':'&lt;','>':'&gt;','"':'&quot;',"'":'&#39;'}[c]));
const fmt=t=>t?new Date(t).toLocaleString():"—";
async function get(u,opt){const r=await fetch(u,opt);if(!r.ok)throw new Error(await r.text());return r.json()}
function card(label,value,cls=''){return `<div class="card ${cls}"><div class="muted">${esc(label)}</div><div class="big">${esc(value)}</div></div>`}
function setTab(id){document.querySelectorAll('section').forEach(x=>x.classList.toggle('active',x.id===id));document.querySelectorAll('nav button').forEach(x=>x.style.borderColor=x.dataset.tab===id?'var(--accent)':'var(--line)')}
document.querySelectorAll('nav button').forEach(b=>b.onclick=()=>setTab(b.dataset.tab));
const allStages=['discovered','evaluated_low','evaluated_match','applied','response_received','interview_requested','interview_scheduled','rejected','offer_received','offer_negotiating','accepted','withdrawn'];
allStages.forEach(s=>document.getElementById('jobStage').insertAdjacentHTML('beforeend',`<option value="${s}">${s}</option>`));
async function loadSummary(){const s=await get('/api/summary');document.getElementById('clock').textContent='Server: '+fmt(s.server_utc);const q=s.quota;document.getElementById('overviewKpi').innerHTML=[card('Jobs total',s.jobs.total),card('Evaluated',s.jobs.evaluated),card('Pending AI',s.jobs.pending),card('Good matches',s.jobs.good_matches,'kpi-good'),card('AI calls today',q.ai_calls_today),card('AI remaining',q.ai_calls_remaining),card('Applications today',q.applications_sent_today),card('Applications remaining',q.applications_remaining),card('Follow-ups today',s.follow_ups_today),card('Outbound remaining',q.outbound_sends_remaining),card('Inbox replies today',s.replies_today),card('Suppressed',s.suppression_count) ].join('');
document.getElementById('stageGrid').innerHTML=Object.entries(s.stages).sort().map(([k,v])=>`<div class="card"><div class="muted">${esc(k)}</div><div class="big">${v}</div></div>`).join('');
document.getElementById('providers').innerHTML=s.providers.map(p=>`<div class="card"><b>${esc(p.provider)}</b> <span class="status-dot ${p.status==='ready'?'good':'warn'}">● ${esc(p.status)}</span><div class="small muted">failures: ${p.failure_count} · cooldown: ${fmt(p.cooldown_until)}<br>${esc(p.last_error||'No error')}</div></div>`).join('')||'<div class="muted">No provider state recorded yet.</div>';
const r=s.latest_run;const p=r&&r.progress?r.progress:{};document.getElementById('scrapeProgress').innerHTML=r&&r.status==='running'?`<div class="progressbar"><div style="width:${p.progress_percent||0}%"></div></div><div class="grid" style="margin-top:10px">${card('Stage',p.stage||'running')}${card('Progress',(p.progress_percent||0)+'%')}${card('Discovery tasks',`${p.query_completed??0}/${p.query_total??0}`)}${card('Raw jobs',p.raw_scraped??r.raw_scraped)}${card('Scrape errors',p.scrape_errors??r.scrape_errors)}</div><div class="small muted" style="margin-top:10px">Last source/query: ${esc(p.query_label||'—')}<br>${esc(p.message||'Working…')}</div>`:`<div class="muted">${esc(p.message||'No active discovery run.')}</div>`;const rows=p.source_results||[];document.getElementById('scrapeSources').innerHTML=rows.slice().reverse().map(x=>`<tr><td>${esc(x.term||x.id||'—')}</td><td>${esc((x.sites||[]).join(', '))}</td><td>${x.rows??0}</td><td><span class="pill">${esc(x.status||'')}</span></td><td>${x.duration_seconds??0}s</td><td class="small">${esc(x.error||'')}</td></tr>`).join('')||'<tr><td colspan="6" class="muted">Waiting for the first discovery result…</td></tr>';document.getElementById('latestRun').innerHTML=r?`<div class="grid">${card('Run ID',r.id)}${card('Status',r.status)}${card('Scraped',r.raw_scraped)}${card('New',r.new_postings)}${card('Evaluated',r.evaluated)}${card('AI calls',r.ai_calls)}${card('Scrape errors',r.scrape_errors)}${card('Eval failures',r.evaluation_failures)}</div><div class="small muted" style="margin-top:10px">Started ${fmt(r.started_at)} · finished ${fmt(r.finished_at)}${r.error?'<br>Error: '+esc(r.error):''}</div>`:'<div class="muted">No pipeline runs yet.</div>';
document.getElementById('outboundKpi').innerHTML=[card('Sending',s.outbound_send_enabled?'ENABLED':'DISABLED',s.outbound_send_enabled?'kpi-good':'kpi-warn'),card('Kill switch',s.outbound_kill_switch?'ON':'OFF',s.outbound_kill_switch?'kpi-bad':'kpi-good'),card('Hourly used',s.outbound_hour+'/'+s.limits.outbound_hourly),card('Daily outbound',q.outbound_sends_today+'/'+s.limits.outbound_daily),card('Daily applications',q.applications_sent_today+'/'+s.limits.applications_daily)].join('');
}
async function loadJobs(){const q=encodeURIComponent(document.getElementById('jobSearch').value);const stage=encodeURIComponent(document.getElementById('jobStage').value);const jobs=await get('/api/jobs?limit=250&q='+q+(stage?'&stage='+stage:''));document.getElementById('jobsTable').innerHTML=jobs.map(j=>{const m=(j.application_method||'').toLowerCase();const mLabel=m.includes('email')?'✉️ Email':m.includes('portal')||m.includes('web')?'🌐 Web Portal':(j.application_method||'');return `<tr class="click" onclick="showJob(${j.id})"><td>${j.id}</td><td>${esc(j.title)}</td><td>${esc(j.company)}</td><td>${esc(j.location)}</td><td>${j.score??'—'}</td><td>${j.visa===true?'YES':j.visa===false?'NO':'—'}</td><td><span class="pill">${esc(j.stage)}</span></td><td>${esc(j.evaluation_status)}</td><td><span class="pill">${esc(j.application_status||'—')}</span><br><b style="font-size:11px;color:var(--accent)">${mLabel}</b>${j.application_email?`<br><span class="small muted">${esc(j.application_email)}</span>`:''}</td></tr>`}).join('')||'<tr><td colspan="9" class="muted">No jobs match the filter.</td></tr>'}
async function showJob(id){const j=await get('/api/jobs/'+id);const detail=document.getElementById('jobDetail');detail.classList.remove('hidden');detail.innerHTML=`<div class="top"><div><h2>#${j.id} — ${esc(j.title)}</h2><div class="muted">${esc(j.company)} · ${esc(j.location)} · ${esc(j.source_site||'unknown')} · ${esc(j.stage)}</div></div><div class="toolbar"><select id="newStage">${allStages.map(s=>`<option ${s===j.stage?'selected':''}>${s}</option>`).join('')}</select><button onclick="changeStage(${j.id})">Save stage</button></div></div><div class="grid" style="margin-top:12px">${card('Match score',j.score??'—')}${card('Visa',j.visa===true?'YES':j.visa===false?'NO':'UNKNOWN')}${card('Evaluation',j.evaluation_status)}${card('Follow-ups',j.follow_up_count)}${card('Applied',j.applied_at?fmt(j.applied_at):'No')}${card('Application status',j.application_status||'—')}${card('Recipient',j.application_email||'—')}</div><div class="split"><div><h3>Evaluation</h3><div class="detail">${esc(j.evaluation_reason||'')}</div><h3>Visa notes</h3><div class="detail">${esc(j.visa_status_notes||'')}</div><h3>Skills</h3><div>${(j.skills||[]).map(x=>`<span class="pill">${esc(x)}</span>`).join('')||'—'}</div><h3>English pitch</h3><div class="detail">${esc(j.pitch_en||'')}</div></div><div><h3>Job description</h3><div class="detail">${esc(j.description||'')}</div>${j.job_url?`<p><a href="${esc(j.job_url)}" target="_blank">Open original listing</a></p>`:''}${j.application_url?`<p><a href="${esc(j.application_url)}" target="_blank">Open application link</a></p>`:''}<h3>Email thread</h3>${(j.email_events||[]).map(e=>`<div class="panel"><b>${esc(e.direction)} · ${esc(e.email_type)} · ${esc(e.subject)}</b><div class="small muted">${fmt(e.created_at)} · ${esc(e.sender_email||'')} → ${esc(e.recipient_email||'')}<br>${esc(e.message_id||'')}</div><div class="detail">${esc(e.body||'')}</div></div>`).join('')||'<div class="muted">No email events.</div>'}</div></div>`;detail.scrollIntoView({behavior:'smooth'})}
async function changeStage(id){const stage=document.getElementById('newStage').value;await get('/api/jobs/'+id+'/stage',{method:'PATCH',headers:{'Content-Type':'application/json'},body:JSON.stringify({stage})});await refreshAll();await showJob(id)}
async function loadInbox(){const st=encodeURIComponent(document.getElementById('inboxStatus').value);const rows=await get('/api/inbox?limit=250'+(st?'&status_filter='+st:''));document.getElementById('inboxTable').innerHTML=rows.map(x=>`<tr><td>${fmt(x.created_at)}</td><td><span class="pill">${esc(x.status)}</span></td><td>${esc(x.sender_email||'—')}</td><td>${esc(x.subject||'—')}</td><td>${esc(x.reason||'')}</td><td class="small">${esc(x.message_id||'')}</td></tr>`).join('')||'<tr><td colspan="6" class="muted">No inbox records.</td></tr>'}
async function loadOutbound(){const st=encodeURIComponent(document.getElementById('outStatus').value);const rows=await get('/api/outbound?limit=250'+(st?'&status_filter='+st:''));document.getElementById('outboundTable').innerHTML=rows.map(x=>`<tr><td>${fmt(x.created_at)}</td><td>${esc(x.email_type)}</td><td>${esc(x.recipient_email)}</td><td>${esc(x.subject||'')}</td><td class="${x.status==='sent'?'good':x.status==='unknown'?'warn':x.status==='failed'?'bad':''}">${esc(x.status)}</td><td>${esc(x.failure_reason||'')}</td><td>${x.job_id??'—'}</td></tr>`).join('')||'<tr><td colspan="7" class="muted">No outbound records.</td></tr>';const s=await get('/api/suppressions');document.getElementById('suppressions').innerHTML=s.map(x=>`<div class="card"><b>${esc(x.email||x.domain)}</b> · ${esc(x.reason)}<div class="small muted">${fmt(x.created_at)} · ${esc(x.details||'')}</div></div>`).join('')||'<div class="muted">No suppressions.</div>'}
async function addSuppression(){await get('/api/suppressions',{method:'POST',headers:{'Content-Type':'application/json'},body:JSON.stringify({email:document.getElementById('supEmail').value||null,domain:document.getElementById('supDomain').value||null,reason:document.getElementById('supReason').value||'manual',details:document.getElementById('supDetails').value||null})});document.getElementById('supEmail').value='';document.getElementById('supDomain').value='';document.getElementById('supDetails').value='';await loadOutbound();await loadSummary()}
async function loadRuns(){const rows=await get('/api/runs?limit=100');document.getElementById('runsTable').innerHTML=rows.map(r=>`<tr><td>${r.id}</td><td>${fmt(r.started_at)}</td><td>${esc(r.status)}</td><td>${r.raw_scraped}</td><td>${r.new_postings}</td><td>${r.evaluated}</td><td>${r.ai_calls}</td><td>${r.scrape_errors}</td><td>${r.evaluation_failures}</td><td>${esc(r.error||'')}</td></tr>`).join('')}
async function loadSystem(){const [c,s]=await Promise.all([get('/api/config'),get('/api/summary')]);document.getElementById('config').innerHTML='<pre class="detail">'+esc(JSON.stringify(c,null,2))+'</pre>';document.getElementById('providerState').innerHTML=s.providers.map(p=>`<div class="card"><b>${esc(p.provider)}</b> · ${esc(p.status)}<div class="small">Failures ${p.failure_count} · cooldown ${fmt(p.cooldown_until)}<br>${esc(p.last_error||'No error')}</div></div>`).join('');document.getElementById('emailSafety').innerHTML='<pre class="detail">'+esc(JSON.stringify(c.email_safety,null,2))+'</pre>'}
async function refreshAll(){try{await Promise.all([loadSummary(),loadJobs(),loadInbox(),loadOutbound(),loadRuns(),loadSystem()])}catch(e){console.error(e)}}
loadSummary();loadJobs();loadInbox();loadOutbound();loadRuns();loadSystem();setInterval(refreshAll,10000);
</script></body></html>'''


FREELANCE_HTML = r'''<!doctype html>
<html><head><meta charset="utf-8"><meta name="viewport" content="width=device-width,initial-scale=1">
<title>Freelance Hub — Client Acquisition</title>
<style>
:root{--bg:#08101f;--panel:#0f1a2e;--line:#223252;--text:#edf2ff;--muted:#8fa0be;--accent:#a78bfa;--good:#56d39b;--warn:#ffcc66;--bad:#ff7474;--blue:#76a9ff;--purple:#a78bfa;--pink:#f472b6;--orange:#fb923c}
*{box-sizing:border-box}body{margin:0;font-family:Inter,Segoe UI,system-ui,sans-serif;background:var(--bg);color:var(--text)}
.wrap{max-width:1700px;margin:auto;padding:20px}.top{display:flex;justify-content:space-between;gap:16px;align-items:flex-start;flex-wrap:wrap}.muted{color:var(--muted)}
button,input,select,textarea{background:#101d34;border:1px solid var(--line);color:var(--text);border-radius:9px;padding:9px 11px;font-size:13px}button{cursor:pointer;transition:all .15s}button:hover{border-color:var(--accent);background:#182744}
.btn-primary{background:linear-gradient(135deg,#7c3aed,#a78bfa);border:none;color:#fff;font-weight:600;padding:10px 18px}.btn-primary:hover{opacity:.9}
.btn-sm{padding:5px 10px;font-size:11px;border-radius:6px}
.grid{display:grid;grid-template-columns:repeat(auto-fit,minmax(180px,1fr));gap:12px}.card,.panel{background:var(--panel);border:1px solid var(--line);border-radius:14px;padding:15px;transition:border-color .2s}.card:hover{border-color:#334466}
.big{font-size:25px;font-weight:800;margin-top:7px}.panel{margin-top:14px}
.toolbar{display:flex;gap:8px;flex-wrap:wrap;align-items:center}.toolbar .grow{flex:1;min-width:220px}
.pill{display:inline-block;padding:4px 10px;border-radius:999px;font-size:11px;font-weight:600}.pill-discovered{background:#1e3a5f;color:#76a9ff}.pill-evaluated{background:#1e3a5f;color:#a78bfa}.pill-pitched{background:#2d1b4e;color:#c084fc}.pill-in_discussion{background:#1b3a2e;color:#56d39b}.pill-offer_received{background:#3a2e1b;color:#ffcc66}.pill-deal_won{background:#1b3a2e;color:#34d399}.pill-lost{background:#2e1b1b;color:#ff7474}
.good{color:var(--good)}.warn{color:var(--warn)}.bad{color:var(--bad)}.small{font-size:12px}
table{width:100%;border-collapse:collapse}th,td{padding:9px 8px;border-bottom:1px solid #1b2945;text-align:left;font-size:13px;vertical-align:top}th{color:var(--muted);position:sticky;top:0;background:var(--panel)}tr.click:hover{background:#13213a;cursor:pointer}
.kanban{display:grid;grid-template-columns:repeat(5,1fr);gap:12px;margin-top:14px}@media(max-width:1100px){.kanban{grid-template-columns:1fr 1fr}}
.kanban-col{background:var(--panel);border:1px solid var(--line);border-radius:14px;padding:12px;min-height:300px}
.kanban-col h3{font-size:14px;margin:0 0 10px;display:flex;justify-content:space-between;align-items:center}
.kanban-card{background:#0d1829;border:1px solid var(--line);border-radius:10px;padding:10px;margin-bottom:8px;cursor:pointer;transition:all .15s}
.kanban-card:hover{border-color:var(--accent);transform:translateY(-1px);box-shadow:0 4px 12px rgba(0,0,0,.3)}
.kanban-card .title{font-weight:600;font-size:13px;margin-bottom:4px}.kanban-card .meta{font-size:11px;color:var(--muted)}
.kanban-count{background:#1e293b;padding:2px 8px;border-radius:999px;font-size:12px;font-weight:700}
.modal-overlay{position:fixed;top:0;left:0;right:0;bottom:0;background:rgba(0,0,0,.7);z-index:100;display:flex;align-items:center;justify-content:center;backdrop-filter:blur(4px)}
.modal{background:var(--panel);border:1px solid var(--line);border-radius:18px;padding:24px;max-width:800px;width:95%;max-height:90vh;overflow-y:auto;position:relative}
.modal h2{margin:0 0 16px}.modal .close{position:absolute;top:14px;right:14px;background:none;border:none;color:var(--muted);font-size:20px;cursor:pointer}.modal .close:hover{color:var(--text)}
.detail{white-space:pre-wrap;max-height:350px;overflow:auto;background:#0a1325;padding:12px;border-radius:10px;font-size:13px;line-height:1.5}
.score-bar{height:8px;background:#0a1325;border-radius:999px;overflow:hidden;margin-top:6px}.score-bar>div{height:100%;border-radius:999px;transition:width .3s}
.hidden{display:none}
.loading{text-align:center;padding:40px;color:var(--muted)}
.toast{position:fixed;bottom:20px;right:20px;background:var(--good);color:#000;padding:12px 20px;border-radius:10px;font-weight:600;z-index:200;animation:fadeIn .3s}
@keyframes fadeIn{from{opacity:0;transform:translateY(10px)}to{opacity:1;transform:translateY(0)}}
</style></head><body><div class="wrap">
<div class="top"><div><h1 style="margin:.2em 0">🚀 Freelance & Client Hub</h1><div class="muted">Find clients • Win projects • Track deals</div></div><div class="toolbar"><a href="/" style="color:var(--blue);text-decoration:none;border:1px solid var(--blue);border-radius:9px;padding:9px 14px;font-weight:600">← Job Search</a><span id="clock" class="muted"></span><button class="btn-primary" onclick="runDiscovery()">🔍 Scan for Clients</button><button onclick="batchFollowUp()" style="color:var(--warn);border-color:var(--warn)">⏰ Run Follow-Ups</button><button onclick="refreshAll()">Refresh</button></div></div>

<div id="statsGrid" class="grid" style="margin-top:16px"></div>

<div class="panel" style="margin-top:14px">
<div class="toolbar"><h2 style="margin:0">Deal Pipeline</h2><div class="grow"></div><input id="leadSearch" placeholder="Search leads…" style="width:260px"><button onclick="loadLeads()">Search</button><button id="btnFilterFollowUp" onclick="toggleFollowUpOnly()" style="color:var(--warn);border-color:var(--warn)">⏰ Due Follow-Ups</button></div>
<div id="kanban" class="kanban"></div>
</div>

<div class="panel">
<h2>All Leads</h2>
<div class="toolbar"><select id="stageFilter"><option value="">All stages</option></select><button onclick="loadLeads()">Filter</button></div>
<div style="overflow:auto;margin-top:10px"><table><thead><tr><th>ID</th><th>Project</th><th>Client</th><th>Source</th><th>Score</th><th>Stage</th><th>Pitch</th><th>Contact</th><th>Actions</th></tr></thead><tbody id="leadsTable"></tbody></table></div>
</div>

<div id="leadModal" class="hidden"></div>
<div id="toast" class="hidden"></div>
</div>
<script>
const esc=s=>String(s??'').replace(/[&<>"']/g,c=>({'&':'&amp;','<':'&lt;','>':'&gt;','"':'&quot;',"'":'&#39;'}[c]));
const fmt=t=>t?new Date(t).toLocaleString():'—';
async function get(u,opt){const r=await fetch(u,opt);if(!r.ok)throw new Error(await r.text());return r.json()}
function card(label,value,cls=''){return `<div class="card ${cls}"><div class="muted">${esc(label)}</div><div class="big">${esc(value)}</div></div>`}
function pillStage(s){return `<span class="pill pill-${s}">${esc(s)}</span>`}
function scoreColor(s){if(s>=75)return'var(--good)';if(s>=50)return'var(--warn)';return'var(--bad)'}

const STAGES=['discovered','evaluated','pitched','in_discussion','offer_received','deal_won','lost'];
const STAGE_LABELS={'discovered':'🔍 Discovered','evaluated':'⚡ Evaluated','pitched':'✉️ Pitched','in_discussion':'💬 In Discussion','offer_received':'🎯 Offer Received','deal_won':'🏆 Won','lost':'❌ Lost'};
const KANBAN_STAGES=['evaluated','pitched','in_discussion','offer_received','deal_won'];
STAGES.forEach(s=>document.getElementById('stageFilter').insertAdjacentHTML('beforeend',`<option value="${s}">${STAGE_LABELS[s]||s}</option>`));

let allLeads=[];
let followUpOnly=false;

async function loadStats(){
  const s=await get('/api/freelance/stats');
  document.getElementById('clock').textContent='Updated: '+new Date().toLocaleTimeString();
  document.getElementById('statsGrid').innerHTML=[
    card('Total Leads',s.total_leads),
    card('Avg Score',s.average_score),
    card('Active Pitches',s.active_pitches),
    card('Needs Follow-Up',s.needs_follow_up||0,s.needs_follow_up?'warn':''),
    card('In Discussion',s.in_discussion),
    card('Offers',s.offers_received),
    card('Deals Won',s.deals_won),
  ].join('');
}

async function loadLeads(){
  const q=encodeURIComponent(document.getElementById('leadSearch').value);
  const stage=encodeURIComponent(document.getElementById('stageFilter').value);
  let url='/api/freelance/leads?limit=300&q='+q+(stage?'&stage='+stage:'');
  if(followUpOnly) url+='&needs_follow_up=true';
  allLeads=await get(url);
  renderKanban();
  renderTable();
}

function toggleFollowUpOnly(){
  followUpOnly=!followUpOnly;
  const btn=document.getElementById('btnFilterFollowUp');
  if(btn){
    btn.style.background=followUpOnly?'rgba(245,158,11,0.2)':'';
    btn.textContent=followUpOnly?'✓ Due Follow-Ups Only':'⏰ Due Follow-Ups';
  }
  loadLeads();
}

function renderKanban(){
  const cols={};
  KANBAN_STAGES.forEach(s=>cols[s]=[]);
  allLeads.forEach(l=>{if(cols[l.stage])cols[l.stage].push(l)});
  document.getElementById('kanban').innerHTML=KANBAN_STAGES.map(s=>`
    <div class="kanban-col">
      <h3>${STAGE_LABELS[s]} <span class="kanban-count">${cols[s].length}</span></h3>
      ${cols[s].map(l=>`
        <div class="kanban-card" onclick="showLead(${l.id})">
          <div class="title">${esc(l.title||'Untitled')}</div>
          <div class="meta">${esc(l.client_name||'Unknown')} · ${esc(l.source_platform)}</div>
          ${l.match_score!=null?`<div class="score-bar"><div style="width:${l.match_score}%;background:${scoreColor(l.match_score)}"></div></div>`:''}
          <div class="meta" style="margin-top:4px">${l.budget_estimate?'💰 '+esc(l.budget_estimate):''}${l.contact_email?' · 📧':''}${l.follow_up_count?' · FU#'+l.follow_up_count:''}</div>
        </div>
      `).join('')||'<div class="muted small" style="padding:10px">No leads</div>'}
    </div>
  `).join('');
}

function renderTable(){
  document.getElementById('leadsTable').innerHTML=allLeads.map(l=>`
    <tr class="click" onclick="showLead(${l.id})">
      <td>${l.id}</td>
      <td>${esc((l.title||'').substring(0,60))}</td>
      <td>${esc(l.client_name||'—')}</td>
      <td>${esc(l.source_platform)}</td>
      <td style="color:${l.match_score!=null?scoreColor(l.match_score):'var(--muted)'}">${l.match_score??'—'}</td>
      <td>${pillStage(l.stage)}</td>
      <td>${esc(l.pitch_status||'—')}</td>
      <td>${l.contact_email?'📧 '+esc(l.contact_email):(l.contact_url?'🔗 Link':'—')}</td>
      <td>
        <button class="btn-sm" onclick="event.stopPropagation();genPitch(${l.id})">✍️</button>
        <button class="btn-sm" onclick="event.stopPropagation();genFollowUp(${l.id})" title="Draft Follow-Up">⏰</button>
        <button class="btn-sm" onclick="event.stopPropagation();sendPitch(${l.id})">📤</button>
      </td>
    </tr>
  `).join('')||'<tr><td colspan="9" class="muted">No leads found.</td></tr>';
}

async function showLead(id){
  const l=await get('/api/freelance/leads/'+id);
  document.getElementById('leadModal').className='modal-overlay';
  document.getElementById('leadModal').innerHTML=`<div class="modal">
    <button class="close" onclick="closeModal()">&times;</button>
    <h2>#${l.id} — ${esc(l.title||'Untitled')}</h2>
    <div class="muted">${esc(l.client_name)} · ${esc(l.client_type||'')} · ${esc(l.source_platform)}</div>
    <div class="grid" style="margin-top:14px">
      ${card('Score',l.match_score??'—')}${card('Stage',l.stage)}${card('Budget',l.budget_estimate||'—')}${card('Follow-ups',l.follow_up_count)}${card('Next Due',l.next_follow_up_due?fmt(l.next_follow_up_due):'None')}${card('Contact',l.contact_email||'No email')}
    </div>
    <div class="toolbar" style="margin-top:14px">
      <select id="modalStage">${STAGES.map(s=>`<option ${s===l.stage?'selected':''}>${s}</option>`).join('')}</select>
      <button onclick="changeStage(${l.id})">Save Stage</button>
      <button class="btn-primary" onclick="genPitch(${l.id})">✍️ Generate Pitch</button>
      <button onclick="genFollowUp(${l.id})" style="color:var(--warn);border-color:var(--warn)">⏰ Draft Follow-Up</button>
      <button onclick="sendPitch(${l.id})">📤 Send Pitch</button>
    </div>
    <div style="display:grid;grid-template-columns:1fr 1fr;gap:12px;margin-top:14px">@media(max-width:900px){grid-template-columns:1fr}
      <div>
        <h3>Evaluation</h3><div class="detail">${esc(l.evaluation_reason||'Not evaluated yet')}</div>
        <h3>Project Scope</h3><div class="detail">${esc(l.project_scope||'—')}</div>
        <h3>Description</h3><div class="detail">${esc(l.description||'')}</div>
        ${l.source_url?`<p><a href="${esc(l.source_url)}" target="_blank" style="color:var(--accent)">Open original post →</a></p>`:''}
      </div>
      <div>
        <h3>Pitch</h3>
        <div class="detail" style="min-height:150px">${l.pitch_body?esc(l.pitch_body):'<span class="muted">No pitch generated yet. Click "Generate Pitch" to create one.</span>'}</div>
        ${l.pitch_subject?`<div class="small muted" style="margin-top:6px">Subject: ${esc(l.pitch_subject)}</div>`:''}
        <h3>Messages</h3>
        ${(l.messages||[]).map(m=>`<div class="card" style="margin-bottom:6px"><b>${esc(m.direction)} · ${esc(m.message_type)}</b><div class="small muted">${fmt(m.created_at)}${m.sent_at?' · Sent '+fmt(m.sent_at):''}</div><div class="detail" style="max-height:200px;margin-top:6px">${esc(m.body||'')}</div></div>`).join('')||'<div class="muted">No messages yet.</div>'}
        ${l.offer_terms?`<h3>Offer</h3><div class="detail">${esc(l.offer_terms)}</div>`:''}
      </div>
    </div>
  </div>`;
}

function closeModal(){document.getElementById('leadModal').className='hidden'}

async function changeStage(id){
  const stage=document.getElementById('modalStage').value;
  await get('/api/freelance/leads/'+id+'/stage',{method:'PATCH',headers:{'Content-Type':'application/json'},body:JSON.stringify({stage})});
  toast('Stage updated!');
  await refreshAll();
  await showLead(id);
}

async function genPitch(id){
  toast('Generating pitch…');
  const r=await get('/api/freelance/leads/'+id+'/pitch',{method:'POST'});
  toast(r.ok?'Pitch generated!':'Pitch: '+r.status);
  await refreshAll();
  await showLead(id);
}

async function genFollowUp(id){
  toast('Drafting follow-up…');
  try{
    const r=await get('/api/freelance/leads/'+id+'/follow_up',{method:'POST'});
    toast(r.ok?`Follow-Up #${r.follow_up_number} generated!`: 'Follow-up: '+r.status);
    await refreshAll();
    await showLead(id);
  }catch(e){toast('Error: '+e.message)}
}

async function batchFollowUp(){
  toast('Running automated follow-up cadence…');
  try{
    const r=await get('/api/freelance/batch_follow_up',{method:'POST'});
    const s=r.summary||{};
    toast(`Follow-ups processed: ${s.drafted||0} drafted, ${s.follow_ups_sent||0} sent!`);
    await refreshAll();
  }catch(e){toast('Error: '+e.message)}
}

async function sendPitch(id){
  toast('Sending pitch…');
  const r=await get('/api/freelance/leads/'+id+'/send',{method:'POST'});
  toast(r.ok?'Pitch sent! ✓':'Send: '+r.status);
  await refreshAll();
}

async function runDiscovery(){
  toast('🔍 Scanning for clients… this may take a minute');
  try{
    const r=await get('/api/freelance/discover',{method:'POST'});
    toast(`Found ${r.summary?.new_leads||0} new leads!`);
    await refreshAll();
  }catch(e){toast('Discovery error: '+e.message)}
}

function toast(msg){
  const el=document.getElementById('toast');
  el.className='toast';
  el.textContent=msg;
  setTimeout(()=>el.className='hidden',3500);
}

async function refreshAll(){
  try{await Promise.all([loadStats(),loadLeads()])}catch(e){console.error(e)}
}
refreshAll();
setInterval(refreshAll,15000);
</script></body></html>'''
