"""Main job-search pipeline with live progress, cost controls and application routing."""
from __future__ import annotations

import json
import logging
from datetime import datetime, timezone

from config.candidate_profile import CANDIDATE_PROFILE, SEARCH_MATRIX
from config.settings import settings
from src.ai.service import AIQuotaExceeded
from src.alerting.email_notifier import send_email_alert
from src.alerting.telegram_bot import send_telegram_alert
from src.application.auto_apply import apply_to_job
from src.dedup.deduplicator import filter_new_postings
from src.evaluation.claude_client import evaluate_job
from src.evaluation.prefilter import relevance_score, visa_relocation_prefilter
from src.ingestion.scraper import get_last_ingestion_stats, run_ingestion
from src.outreach.message_generator import generate_outreach_draft
from sqlalchemy import func, select

from src.storage.db import get_session, init_db
from src.storage.models import EmailEvent, JobPosting, PipelineRun, PipelineRunEvent, PipelineStage

logging.basicConfig(level=logging.INFO, format="%(asctime)s [%(levelname)s] %(name)s: %(message)s")
logger = logging.getLogger(__name__)


def _as_email_json(value) -> str | None:
    if value is None:
        return None
    if isinstance(value, float):
        return None
    if isinstance(value, str):
        return value
    try:
        return json.dumps(list(value), ensure_ascii=False)
    except Exception:
        return None


def _normalize_raw_job(raw: dict, user_id: int | None = None) -> dict:
    description = raw.get("description")
    return {
        "user_id": user_id or raw.get("user_id"),
        "dedup_hash": raw.get("dedup_hash"),
        "source_site": raw.get("site") or raw.get("_query_location"),
        "source_query": raw.get("_query_id"),
        "source_sites": _as_email_json(raw.get("_query_sites")) if raw.get("_query_sites") else None,
        "title": raw.get("title"),
        "company": raw.get("company"),
        "location": str(raw.get("location") or raw.get("_query_location") or "Unknown"),
        "is_remote": bool(raw.get("is_remote") or raw.get("_forced_remote_flag")),
        "job_url": raw.get("job_url"),
        "application_url": raw.get("_job_url_direct") or raw.get("job_url_direct"),
        "application_emails": _as_email_json(raw.get("_application_emails") if raw.get("_application_emails") is not None else raw.get("emails")),
        "company_url": raw.get("company_url"),
        "raw_description": description or "",
        "date_posted": str(raw.get("date_posted") or ""),
    }


def _persist_new_jobs(new_jobs: list[dict], user_id: int | None = None) -> list[int]:
    ids: list[int] = []
    with get_session() as session:
        for raw in new_jobs:
            normalized = _normalize_raw_job(raw, user_id=user_id)
            normalized["ai_relevance_score"] = relevance_score(raw)
            record = JobPosting(**normalized)
            session.add(record)
            session.flush()
            ids.append(record.id)
    return ids


def _update_run_progress(run_id: int, *, stage: str, message: str, progress_percent: int, **details) -> None:
    """Persist a durable live snapshot and an event for the admin dashboard."""
    progress_percent = max(0, min(100, int(progress_percent)))
    try:
        with get_session() as session:
            record = session.get(PipelineRun, run_id)
            if not record:
                return
            previous = {}
            if record.summary_json:
                try:
                    previous = json.loads(record.summary_json) or {}
                except Exception:
                    previous = {}
            payload = {**previous, **details, "stage": stage, "message": message, "progress_percent": progress_percent}
            # Keep the complete discovery result list visible while the run is still active.
            record.summary_json = json.dumps(payload, ensure_ascii=False, default=str)
            record.updated_at = datetime.now(timezone.utc)
            record.phase = stage
            record.progress_pct = progress_percent
            record.current_task = message
            for attr, key in (("total_queries", "query_total"), ("completed_queries", "query_completed"),
                              ("raw_scraped_live", "raw_scraped"), ("new_postings_live", "new_postings"),
                              ("evaluated_live", "evaluated")):
                if key in details:
                    setattr(record, attr, int(details[key]))
            event_details = {k: v for k, v in details.items() if k != "source_results"}
            session.add(PipelineRunEvent(
                run_id=run_id, phase=stage, status=str(details.get("status", "progress")),
                message=message, details_json=json.dumps(event_details, ensure_ascii=False, default=str),
            ))
    except Exception:
        logger.exception("Failed to persist live pipeline progress")


def _evaluate_and_act(job_id: int, *, run_id: int | None = None, index: int = 0, total: int = 0) -> str:
    with get_session() as session:
        job = session.get(JobPosting, job_id)
        if job is None or job.evaluated:
            return "already_done"

        if run_id:
            _update_run_progress(
                run_id,
                stage="evaluating",
                message=f"Evaluating job {index}/{total}: {job.title}",
                progress_percent=int((index - 1) * 100 / max(1, total)),
                current_job_index=index,
                current_job_total=total,
            )

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

        should_call, signals = visa_relocation_prefilter(job_dict)
        job.evaluation_attempted_at = datetime.now(timezone.utc)
        if not should_call:
            job.evaluation_status = "prefilter_skipped"
            job.evaluation_reason = "Held by prefilter."
            job.evaluated = False
            if run_id:
                _update_run_progress(
                    run_id, stage="evaluating", message=f"Skipped {index}/{total}: deterministic prefilter",
                    progress_percent=int(index * 100 / max(1, total)), current_job_index=index,
                    current_job_total=total,
                )
            logger.info("Job %s skipped before AI by deterministic prefilter", job.id)
            return "prefilter_skipped"

        try:
            from src.candidate.profile_manager import get_active_profile
            job_profile = get_active_profile(user_id=getattr(job, "user_id", None), session=session)
            result = evaluate_job(job_dict, profile=job_profile, session=session)
        except AIQuotaExceeded:
            job.evaluation_status = "budget_skipped"
            job.evaluation_reason = "Daily AI quota exhausted."
            logger.warning("Job %s held for later: daily AI quota exhausted", job.id)
            return "budget_skipped"
        except Exception as exc:
            job.evaluation_status = "failed"
            job.evaluation_reason = str(exc)
            logger.error("Evaluation failed for job %s (%s): %s", job.id, job.title, exc)
            return "failed"

        if result is None:
            job.evaluation_status = "failed"
            job.evaluation_reason = "No configured AI provider completed evaluation."
            return "failed"

        job.evaluated = True
        job.evaluation_status = "evaluated"
        job.evaluation_method = result.get("evaluation_method") or "ai"
        job.evaluation_reason = ", ".join(signals)
        job.match_score = result["match_score"]
        job.visa_sponsorship_detected = result["visa_sponsorship_detected"]
        job.visa_status_notes = result["visa_status_notes"]
        job.key_skills_matched = json.dumps(result["key_skills_matched"], ensure_ascii=False)
        from src.evaluation.language import is_french_job
        is_fr = is_french_job({
            "title": job.title,
            "company": job.company,
            "location": job.location,
            "country": getattr(job, "country", None),
            "raw_description": job.raw_description,
            "source_site": job.source_site,
        })
        job.recruiter_pitch_fr = result["recruiter_pitch_fr"]
        job.recruiter_pitch_en = result["recruiter_pitch_en"]
        job.application_subject = (result.get("application_subject_fr") if is_fr else result.get("application_subject")) or result.get("application_subject")
        job.application_email_body = (result.get("application_email_fr") if is_fr else result.get("application_email_en")) or result.get("application_email_en")

        clears_threshold = job.match_score >= settings.min_match_score and (
            job.visa_sponsorship_detected or not settings.min_visa_confidence
        )
        job.pipeline_stage = PipelineStage.EVALUATED_MATCH if clears_threshold else PipelineStage.EVALUATED_LOW

        if clears_threshold and run_id:
            session.add(
                PipelineRunEvent(
                    run_id=run_id,
                    phase="evaluating",
                    status="matched",
                    message=f"Strong fit ({job.match_score}%): {job.title} at {job.company}",
                    details_json=json.dumps({
                        "job_id": job.id, "company": job.company, "title": job.title,
                        "match_score": job.match_score, "is_remote": job.is_remote,
                        "location": job.location
                    }, ensure_ascii=False)
                )
            )

        if clears_threshold:
            # Check user plan tier and application quota
            from src.storage.models import User, UserJobApplication
            target_uid = getattr(job, "user_id", None)
            user = session.get(User, target_uid) if target_uid else (session.scalar(select(User).where(User.role == "user").order_by(User.id)) or session.scalar(select(User).order_by(User.id)))
            is_pro = bool(user and (user.role == "admin" or getattr(user, "current_plan", None) in ("pro", "pro_499", "ultra")))

            # HR & Talent Acquisition Email Enrichment (Pro feature)
            if is_pro and settings.enable_hr_enrichment and not job.application_emails:
                try:
                    from src.ingestion.hr_enrichment import enrich_job_hr_contacts
                    enrich_job_hr_contacts(job)
                    if job.application_emails and run_id:
                        session.add(
                            PipelineRunEvent(
                                run_id=run_id,
                                phase="enriching",
                                status="success",
                                message=f"Discovered HR contact for {job.company}: {job.application_emails}",
                                details_json=json.dumps({"job_id": job.id, "company": job.company, "emails": job.application_emails}, ensure_ascii=False)
                            )
                        )
                except Exception as enrich_err:
                    logger.debug("HR enrichment skipped for job %s: %s", job.id, enrich_err)

            auto_apply_eligible = job.match_score >= settings.auto_apply_min_score and (
                not settings.auto_apply_require_visa_or_remote or job.visa_sponsorship_detected or job.is_remote
            )
            can_auto_apply = True
            sent_today = 0
            if user:
                today_start = datetime.now(timezone.utc).replace(hour=0, minute=0, second=0, microsecond=0)
                user_apps_today = session.scalar(
                    select(func.count(UserJobApplication.id)).where(
                        UserJobApplication.user_id == user.id,
                        UserJobApplication.applied_at >= today_start,
                    )
                ) or 0
                total_platform_today = session.scalar(
                    select(func.count(JobPosting.id)).where(
                        JobPosting.applied_at >= today_start,
                    )
                ) or 0
                sent_today = max(user_apps_today, total_platform_today) if user.role == "admin" else user_apps_today
                effective_limit = user.daily_apply_limit or (200 if user.role == "admin" else 50)
                if sent_today >= effective_limit:
                    can_auto_apply = False
                    logger.info("Daily auto-apply limit of %s reached for user %s (%s sent today). Skipping auto-apply for job %s.", effective_limit, user.email, sent_today, job.id)

            # Apply first so the alert email can tell the user whether an application
            # was actually sent or must be handled manually.
            try:
                if auto_apply_eligible and can_auto_apply:
                    apply_result = apply_to_job(
                        {
                            "id": job.id,
                            "user_id": getattr(job, "user_id", None),
                            "title": job.title,
                            "company": job.company,
                            "location": job.location,
                            "continent": job.continent,
                            "country": getattr(job, "country", None),
                            "source_site": job.source_site,
                            "raw_description": job.raw_description,
                            "application_emails": job.application_emails,
                            "company_url": job.company_url,
                            "application_url": job.application_url,
                            "recruiter_pitch_en": job.recruiter_pitch_en,
                            "recruiter_pitch_fr": job.recruiter_pitch_fr,
                            "application_subject": job.application_subject,
                            "application_subject_fr": result.get("application_subject_fr"),
                            "application_email_body": job.application_email_body,
                            "application_email_fr": result.get("application_email_fr"),
                            "application_email_en": result.get("application_email_en"),
                            "job_url": job.job_url,
                        },
                        session=session,
                        profile=job_profile,
                    )
                elif auto_apply_eligible and not can_auto_apply:
                    apply_result = {
                        "application_method": "daily_quota_reached",
                        "application_email": None,
                        "application_url": job.application_url or job.job_url,
                        "application_status": "daily_quota_reached",
                        "application_error": f"Daily application quota of {user.daily_apply_limit}/day reached ({sent_today} sent today).",
                        "resume_attached": False,
                        "applied": False,
                        "thread_subject": None,
                        "message_id": None,
                    }
                else:
                    apply_result = {
                        "application_method": "manual_review_threshold",
                        "application_email": None,
                        "application_url": job.application_url or job.job_url,
                        "application_status": "manual_review_threshold",
                        "application_error": "Match is good but did not meet the automatic-application score/visa-or-remote gate.",
                        "resume_attached": False,
                        "applied": False,
                        "thread_subject": None,
                        "message_id": None,
                    }
                job.application_method = apply_result["application_method"]
                job.application_email = apply_result["application_email"]
                job.application_url = apply_result.get("application_url") or job.application_url
                job.application_status = apply_result.get("application_status")
                job.application_error = apply_result.get("application_error")
                job.application_attempted_at = datetime.now(timezone.utc)
                job.resume_attached = bool(apply_result.get("resume_attached"))
                job.application_screenshot_path = apply_result.get("application_screenshot_path")
                job.thread_subject = apply_result.get("thread_subject")
                if apply_result["applied"]:
                    job.pipeline_stage = PipelineStage.APPLIED
                    job.applied_at = apply_result.get("applied_at") or datetime.now(timezone.utc)
                    target_user = user or (session.get(User, getattr(job, "user_id", None)) if getattr(job, "user_id", None) else session.scalar(select(User).order_by(User.id)))
                    if target_user:
                        session.add(
                            UserJobApplication(
                                user_id=target_user.id,
                                job_id=job.id,
                                application_method=job.application_method,
                                applied_at=job.applied_at,
                                status=job.application_status or "sent",
                            )
                        )
                    if apply_result.get("message_id"):
                        session.add(
                            EmailEvent(
                                job_id=job.id,
                                direction="outbound",
                                message_id=apply_result["message_id"],
                                sender_email=settings.sender_email,
                                recipient_email=job.application_email,
                                subject=job.thread_subject,
                                body=apply_result.get("application_body") or job.application_email_body or job.recruiter_pitch_fr or job.recruiter_pitch_en,
                                email_type="application",
                            )
                        )
                    if run_id:
                        session.add(
                            PipelineRunEvent(
                                run_id=run_id,
                                phase="applying",
                                status="sent",
                                message=f"Application dispatched to {job.company} ({job.title})",
                                details_json=json.dumps({
                                    "job_id": job.id, "company": job.company, "title": job.title,
                                    "email": job.application_email, "match_score": job.match_score
                                }, ensure_ascii=False)
                            )
                        )
                        run_rec = session.get(PipelineRun, run_id)
                        if run_rec:
                            run_rec.applications_sent = (run_rec.applications_sent or 0) + 1
            except Exception as exc:
                job.application_status = "error"
                logger.error("Application step failed for job %s: %s", job.id, exc)
                if run_id:
                    session.add(
                        PipelineRunEvent(
                            run_id=run_id,
                            phase="applying",
                            status="error",
                            message=f"Application failed for {job.company}: {str(exc)[:100]}",
                            details_json=json.dumps({"job_id": job.id, "error": str(exc)}, ensure_ascii=False)
                        )
                    )


            alert_payload = {
                "title": job.title,
                "company": job.company,
                "location": job.location,
                "match_score": job.match_score,
                "visa_sponsorship_detected": job.visa_sponsorship_detected,
                "visa_status_notes": job.visa_status_notes,
                "key_skills_matched": json.loads(job.key_skills_matched),
                "job_url": job.job_url,
                "application_url": job.application_url,
                "application_status": job.application_status,
                "application_method": job.application_method,
                "application_error": job.application_error,
            }
            tg_ok = send_telegram_alert(alert_payload)
            email_ok = send_email_alert(alert_payload)
            job.alerted = tg_ok or email_ok

            try:
                from src.notifications.publisher import publish_notification
                target_job_uid = getattr(job, "user_id", None)
                publish_notification(
                    user_id=target_job_uid,
                    event_type="job_match",
                    title=f"High Match ({job.match_score}%): {job.title}",
                    message=f"{job.company} • {job.location} | Status: {job.application_status or 'Discovered'}",
                    link="#postings",
                    data={"job_id": job.id, "match_score": job.match_score, "title": job.title, "company": job.company}
                )
                if apply_result.get("applied"):
                    publish_notification(
                        user_id=target_job_uid,
                        event_type="app_sent",
                        title=f"Application Sent ✉️: {job.title}",
                        message=f"Applied to {job.company} via {job.application_method or 'email'}.",
                        link="#postings",
                        data={"job_id": job.id, "company": job.company}
                    )
            except Exception as notif_err:
                logger.debug("Failed to publish match/apply notification: %s", notif_err)

            try:
                generate_outreach_draft(
                    job_record={
                        **alert_payload,
                        "recruiter_pitch_en": job.recruiter_pitch_en,
                        "recruiter_pitch_fr": job.recruiter_pitch_fr,
                    },
                    candidate_name=CANDIDATE_PROFILE.get("name", "Candidate"),
                    current_location=CANDIDATE_PROFILE.get("current_location", ""),
                )
                job.outreach_generated = True
            except Exception as exc:
                logger.error("Outreach draft failed for job %s: %s", job.id, exc)

        if run_id:
            _update_run_progress(
                run_id,
                stage="evaluating",
                message=f"Completed job {index}/{total}: {job.title}",
                progress_percent=int(index * 100 / max(1, total)),
                current_job_index=index,
                current_job_total=total,
            )
        logger.info(
            "Evaluated job %s: score=%s visa=%s stage=%s application=%s",
            job.id,
            job.match_score,
            job.visa_sponsorship_detected,
            job.pipeline_stage.value,
            job.application_status,
        )
        session.commit()
        return "evaluated"


def _send_pipeline_anomaly(summary: dict) -> bool:
    if not settings.alert_on_zero_scrape or summary["raw_scraped"] >= settings.min_expected_scrape_results:
        return False
    payload = {
        "title": "Pipeline anomaly",
        "company": "Job Search Automation",
        "location": "",
        "match_score": 0,
        "visa_sponsorship_detected": False,
        "visa_status_notes": f"Ingestion returned {summary['raw_scraped']} jobs across {summary['queries']} queries; scrape_errors={summary['scrape_errors']}.",
        "key_skills_matched": [],
        "job_url": "",
        "application_url": None,
        "application_status": "n/a",
        "application_method": "n/a",
    }
    return send_telegram_alert(payload) or send_email_alert(payload)


def auto_apply_pending_matches(user_id: int | None = None, run_id: int | None = None, session=None) -> dict:
    """Drain pending unapplied matching jobs for a user up to their daily quota limit."""
    from src.application.auto_apply import apply_to_job
    from src.ingestion.hr_enrichment import enrich_job_hr_contacts
    from src.storage.models import User, UserJobApplication, EmailEvent
    from src.candidate.profile_manager import get_active_profile
    from src.storage.plans import get_effective_daily_limit
    from sqlalchemy import case, desc, func, or_, select

    def _execute(s):
        target_user = s.get(User, user_id) if user_id else (
            s.scalar(select(User).where(User.role == "user").order_by(User.id))
            or s.scalar(select(User).order_by(User.id))
        )
        if not target_user:
            return {"ok": False, "applied": 0, "message": "No target user found"}

        effective_uid = target_user.id
        active_profile = get_active_profile(user_id=effective_uid, session=s)

        daily_limit = target_user.daily_apply_limit
        if not daily_limit or daily_limit <= 0:
            daily_limit = get_effective_daily_limit(s, target_user.current_plan, fallback_limit=(200 if target_user.role == "admin" else 50))

        today_start = datetime.now(timezone.utc).replace(hour=0, minute=0, second=0, microsecond=0)
        user_apps_today = s.scalar(
            select(func.count(UserJobApplication.id)).where(
                UserJobApplication.user_id == effective_uid,
                UserJobApplication.applied_at >= today_start,
            )
        ) or 0
        total_platform_today = s.scalar(
            select(func.count(JobPosting.id)).where(
                JobPosting.applied_at >= today_start,
            )
        ) or 0
        sent_today = max(user_apps_today, total_platform_today) if target_user.role == "admin" else user_apps_today

        remaining_quota = max(0, daily_limit - sent_today)
        if remaining_quota <= 0:
            logger.info("Daily auto-apply quota (%s/%s) already met today for user %s.", sent_today, daily_limit, target_user.email)
            return {"ok": True, "applied": 0, "sent_today": sent_today, "daily_limit": daily_limit, "message": f"Daily limit reached ({sent_today}/{daily_limit})"}

        # Exclude job types that cannot be automatically dispatched without email contact
        blocked_statuses = [
            "manual_linkedin",
            "manual_job_board",
            "manual_security_challenge",
            "blocked",
            "invalid_recipient",
        ]
        apply_q = select(JobPosting).where(
            JobPosting.match_score >= settings.auto_apply_min_score,
            JobPosting.applied_at.is_(None),
            JobPosting.pipeline_stage != PipelineStage.APPLIED,
            or_(
                JobPosting.application_status.is_(None),
                ~JobPosting.application_status.in_(blocked_statuses),
                (JobPosting.application_emails.isnot(None) & (JobPosting.application_emails != ""))
            )
        )
        if target_user.role != "admin":
            apply_q = apply_q.where(or_(JobPosting.user_id == effective_uid, JobPosting.user_id.is_(None)))

        # Prioritize actionable applications: valid recruiter email first, then highest match score
        apply_q = apply_q.order_by(
            case((JobPosting.application_emails.isnot(None) & (JobPosting.application_emails != ""), 0), else_=1),
            desc(JobPosting.match_score)
        )
        # Bounded query limit to prevent long-running hanging loops
        batch_candidate_limit = min(max(remaining_quota * 3, 15), 60)
        pending_matches = s.scalars(apply_q.limit(batch_candidate_limit)).all()

        applied_count = 0
        consecutive_unactionable = 0
        for job in pending_matches:
            if sent_today >= daily_limit:
                logger.info("Daily auto-apply limit of %s reached for user %s. Stopping batch.", daily_limit, target_user.email)
                break

            # Attempt HR email enrichment if email is missing
            if settings.enable_hr_enrichment and not job.application_emails:
                try:
                    enrich_job_hr_contacts(job)
                except Exception as enrich_err:
                    logger.debug("HR enrichment skipped for job %s: %s", job.id, enrich_err)

            # Check if job has an actionable channel
            has_email = bool(job.application_emails)
            has_web = bool(job.application_url or job.job_url) and settings.auto_apply_web_enabled
            if not has_email and not has_web:
                consecutive_unactionable += 1
                if consecutive_unactionable >= 15:
                    logger.warning("Breaking batch early: %s consecutive jobs without actionable application channels.", consecutive_unactionable)
                    break
                continue

            job_dict = {
                "id": job.id,
                "user_id": effective_uid,
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

            try:
                res = apply_to_job(job_dict, session=s, profile=active_profile)
                job.user_id = effective_uid
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
                    s.add(
                        UserJobApplication(
                            user_id=effective_uid,
                            job_id=job.id,
                            application_method=job.application_method,
                            applied_at=job.applied_at,
                            status=job.application_status or "sent",
                        )
                    )
                    if res.get("message_id"):
                        s.add(
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
                    sent_today += 1
                    applied_count += 1
                    if run_id:
                        s.add(
                            PipelineRunEvent(
                                run_id=run_id,
                                phase="applying",
                                status="sent",
                                message=f"Application dispatched to {job.company} ({job.title})",
                                details_json=json.dumps({
                                    "job_id": job.id, "company": job.company, "title": job.title,
                                    "email": job.application_email, "match_score": job.match_score
                                }, ensure_ascii=False)
                            )
                        )
                        run_rec = s.get(PipelineRun, run_id)
                        if run_rec:
                            run_rec.applications_sent = (run_rec.applications_sent or 0) + 1
                s.commit()
            except Exception as err:
                logger.error("Auto-apply error for job %s: %s", job.id, err)
                s.rollback()

        if applied_count > 0:
            try:
                from src.notifications.publisher import publish_notification
                publish_notification(
                    user_id=effective_uid,
                    event_type="applications_done",
                    title="Applications Dispatched 🎯",
                    message=f"Sent {applied_count} application{'s' if applied_count != 1 else ''}. Daily progress: {sent_today}/{daily_limit}.",
                    link="#outbound",
                    data={"applied_batch": applied_count, "sent_today": sent_today, "daily_limit": daily_limit},
                )
            except Exception as nerr:
                logger.debug("Failed publishing notification: %s", nerr)

        return {
            "ok": True,
            "applied": applied_count,
            "sent_today": sent_today,
            "daily_limit": daily_limit,
            "message": f"Applied to {applied_count} jobs ({sent_today}/{daily_limit} today)"
        }

    if session is not None:
        return _execute(session)
    with get_session() as session_ctx:
        return _execute(session_ctx)


def run_pipeline(user_id: int | None = None) -> dict:
    init_db()

    # Dynamic Candidate Profile & Matrix Discovery for this user
    from src.candidate.profile_manager import get_active_profile, generate_search_matrix
    active_profile = get_active_profile(user_id=user_id)
    if user_id is None:
        if active_profile and active_profile.get("user_id"):
            user_id = active_profile.get("user_id")
        else:
            with get_session() as session:
                from sqlalchemy import text
                user_id = session.execute(text("SELECT id FROM users WHERE role = 'admin' ORDER BY id ASC LIMIT 1")).scalar()
                if not user_id:
                    user_id = session.execute(text("SELECT id FROM users ORDER BY id ASC LIMIT 1")).scalar()

    active_matrix = generate_search_matrix(active_profile) if active_profile else []
    if not active_matrix:
        active_matrix = list(SEARCH_MATRIX)

    # Gate: require at least one active profile with a search matrix before scraping
    if not active_matrix or len(active_matrix) == 0:
        logger.error(
            "Pipeline aborted: No search matrix available for user %s. "
            "Please create and activate a candidate profile with target titles, core stack, and keywords.",
            user_id
        )
        return {"error": "No active profile configured. Pipeline not started."}

    started = datetime.now(timezone.utc)
    with get_session() as session:
        run_record = PipelineRun(user_id=user_id, started_at=started, status="running")
        session.add(run_record)
        session.flush()
        run_record_id = run_record.id

    try:
        try:
            from src.notifications.publisher import publish_notification
            publish_notification(
                event_type="pipeline_started",
                title="Pipeline Started 🚀",
                message=f"Job discovery initiated across {len(active_matrix)} search queries.",
                link="#pipeline",
                data={"run_id": run_record_id, "user_id": user_id}
            )
        except Exception:
            pass

        _update_run_progress(
            run_record_id, stage="scraping", message="Starting broad parallel job discovery…",
            progress_percent=0, query_completed=0, query_total=len(active_matrix), raw_scraped=0, scrape_errors=0, source_results=[]
        )
        def on_scrape_progress(p):
            _update_run_progress(
                run_record_id, stage=p.get("stage", "scraping"),
                message=p.get("message") or f"Scraping {p.get('query_label', '')}",
                progress_percent=p.get("progress_percent", 0),
                query_completed=p.get("query_completed", 0),
                query_total=p.get("query_total", len(active_matrix)),
                raw_scraped=p.get("raw_scraped", 0), scrape_errors=p.get("scrape_errors", 0),
                query_label=p.get("query_label", ""), query_id=p.get("query_id", ""),
                query_sites=p.get("query_sites", []), query_rows=p.get("query_rows", 0),
                query_duration_seconds=p.get("query_duration_seconds", 0), query_error=p.get("query_error"),
                source_results=p.get("source_results", []), status=p.get("status", "progress"),
            )
        raw_jobs = run_ingestion(progress_callback=on_scrape_progress, queries=active_matrix)
        ingestion_stats = get_last_ingestion_stats()

        _update_run_progress(
            run_record_id, stage="deduplicating", message="Deduplicating and ranking discovered jobs…",
            progress_percent=100, raw_scraped=len(raw_jobs), scrape_errors=ingestion_stats["scrape_errors"],
            source_results=ingestion_stats.get("source_results", []),
        )
        new_jobs = filter_new_postings(raw_jobs, user_id=user_id)
        # Preserve every new posting in the database, but send only the strongest
        # candidates to Ollama this run. This prevents a large scrape from exhausting
        # the daily local-model budget before the best jobs are evaluated.
        ranked = sorted(new_jobs, key=relevance_score, reverse=True)
        selected_jobs = ranked[: max(0, settings.evaluation_backlog_limit_per_run)]
        deferred = ranked[len(selected_jobs):]
        for job in deferred:
            job.setdefault("evaluation_status", "queued")
        new_ids = _persist_new_jobs(new_jobs, user_id=user_id)
        selected_hashes = {j.get("dedup_hash") for j in selected_jobs}
        selected_ids = [job_id for job_id, job in zip(new_ids, new_jobs) if job.get("dedup_hash") in selected_hashes]

        counts = {k: 0 for k in ("evaluated", "prefilter_skipped", "budget_skipped", "failed", "already_done")}
        _update_run_progress(
            run_record_id, stage="evaluating", message=f"Evaluating top {len(selected_ids)} of {len(new_ids)} new postings with Ollama…",
            progress_percent=0 if selected_ids else 100, raw_scraped=len(raw_jobs),
            scrape_errors=ingestion_stats["scrape_errors"], new_postings=len(new_ids),
            evaluated=0, current_job_index=0, current_job_total=len(selected_ids),
        )

        for index, job_id in enumerate(selected_ids, start=1):
            outcome = _evaluate_and_act(job_id, run_id=run_record_id, index=index, total=len(selected_ids))
            counts[outcome] = counts.get(outcome, 0) + 1
            _update_run_progress(
                run_record_id, stage="evaluating",
                message=f"Processed {index}/{len(selected_ids)} selected postings…",
                progress_percent=int(index * 100 / max(1, len(selected_ids))),
                raw_scraped=len(raw_jobs), scrape_errors=ingestion_stats["scrape_errors"],
                new_postings=len(new_ids), evaluated=counts["evaluated"],
                evaluation_failures=counts["failed"], current_job_index=index,
                current_job_total=len(selected_ids), selected_this_run=len(selected_ids), deferred_for_next_run=len(deferred),
            )

        # ── Phase: evaluate backlog of previously-discovered, un-evaluated jobs ──
        backlog_counts = {k: 0 for k in ("evaluated", "prefilter_skipped", "budget_skipped", "failed", "already_done")}
        with get_session() as session:
            backlog_query = session.query(JobPosting.id).filter(
                JobPosting.pipeline_stage == PipelineStage.DISCOVERED,
                JobPosting.evaluated.is_(False),
            )
            if user_id is not None:
                backlog_query = backlog_query.filter(JobPosting.user_id == user_id)
            backlog_ids = [
                row[0]
                for row in backlog_query.order_by(JobPosting.id.desc()).limit(
                    max(0, settings.evaluation_backlog_limit_per_run)
                ).all()
                if row[0] not in set(selected_ids)
            ]

        if backlog_ids:
            _update_run_progress(
                run_record_id, stage="evaluating_backlog",
                message=f"Evaluating {len(backlog_ids)} backlog jobs from previous runs…",
                progress_percent=0,
            )
            for b_idx, b_id in enumerate(backlog_ids, start=1):
                outcome = _evaluate_and_act(b_id, run_id=run_record_id, index=b_idx, total=len(backlog_ids))
                backlog_counts[outcome] = backlog_counts.get(outcome, 0) + 1
            logger.info("Backlog evaluation: %s", backlog_counts)

        # ── Phase: Auto-apply to pending eligible matches up to user's daily quota ──
        auto_applied_count = 0
        if settings.auto_apply_mode == "send":
            _update_run_progress(
                run_record_id, stage="applying_matches",
                message="Checking and auto-applying to eligible matching jobs up to daily quota…",
                progress_percent=85,
            )
            try:
                auto_applied_res = auto_apply_pending_matches(user_id=user_id, run_id=run_record_id)
                auto_applied_count = auto_applied_res.get("applied", 0)
                logger.info(
                    "Auto-apply step for user %s complete: %s sent (total today: %s/%s)",
                    user_id, auto_applied_count, auto_applied_res.get("sent_today"), auto_applied_res.get("daily_limit")
                )
            except Exception as auto_apply_err:
                logger.error("Auto-apply step error for user %s: %s", user_id, auto_apply_err)

        summary = {
            "raw_scraped": len(raw_jobs), "new_postings": len(new_jobs),
            "selected_for_ai": len(selected_ids), "deferred": len(deferred),
            "evaluated": counts["evaluated"] + backlog_counts["evaluated"],
            "prefilter_skipped": counts["prefilter_skipped"] + backlog_counts["prefilter_skipped"],
            "budget_skipped": counts["budget_skipped"] + backlog_counts["budget_skipped"],
            "evaluation_failures": counts["failed"] + backlog_counts["failed"],
            "ai_calls": counts["evaluated"] + counts["failed"] + backlog_counts["evaluated"] + backlog_counts["failed"],
            "queries": ingestion_stats["queries"],
            "scrape_errors": ingestion_stats["scrape_errors"],
            "backlog_evaluated": backlog_counts["evaluated"],
        }
        anomaly_alerted = _send_pipeline_anomaly(summary)
        with get_session() as session:
            record = session.get(PipelineRun, run_record_id)
            record.finished_at = datetime.now(timezone.utc)
            record.updated_at = datetime.now(timezone.utc)
            record.status = "success"
            record.phase = "complete"
            record.progress_pct = 100
            record.raw_scraped = summary["raw_scraped"]
            record.raw_scraped_live = summary["raw_scraped"]
            record.scrape_errors = summary["scrape_errors"]
            record.new_postings = summary["new_postings"]
            record.new_postings_live = summary["new_postings"]
            record.evaluated = summary["evaluated"]
            record.evaluated_live = summary["evaluated"]
            record.claude_calls = summary["ai_calls"]
            record.claude_skipped_prefilter = summary["prefilter_skipped"]
            record.claude_skipped_budget = summary["budget_skipped"]
            record.evaluation_failures = summary["evaluation_failures"]
            record.anomaly_alerted = anomaly_alerted
            record.summary_json = json.dumps({**summary, "stage": "complete", "message": "Pipeline run complete", "progress_percent": 100, "source_results": ingestion_stats.get("source_results", [])}, ensure_ascii=False)
            session.add(PipelineRunEvent(run_id=run_record_id, phase="complete", status="success", message="Pipeline run complete", details_json=json.dumps(summary, ensure_ascii=False)))
        try:
            from src.notifications.publisher import publish_notification
            publish_notification(
                user_id=user_id,
                event_type="applications_done",
                title="Applications Complete for Today 🎯",
                message=f"Daily pipeline cycle complete. Discovered {len(new_ids)} jobs, evaluated {summary.get('claude_evaluated', 0)} with AI.",
                link="#outbound",
                data={"run_id": run_record_id, "new_postings": len(new_ids), "evaluated": summary.get("claude_evaluated", 0)}
            )
        except Exception as notif_err:
            logger.debug("Failed to publish pipeline complete notification: %s", notif_err)
        logger.info("Pipeline run summary: %s", summary)
        return summary
    except Exception as exc:
        logger.exception("Pipeline run failed")
        with get_session() as session:
            record = session.get(PipelineRun, run_record_id)
            if record:
                record.finished_at = datetime.now(timezone.utc)
                record.updated_at = datetime.now(timezone.utc)
                record.status = "failed"
                record.phase = "failed"
                record.progress_pct = 100
                record.error = str(exc)
                record.summary_json = json.dumps({"stage": "failed", "message": str(exc), "progress_percent": 100}, ensure_ascii=False)
                session.add(PipelineRunEvent(run_id=run_record_id, phase="failed", status="error", message=str(exc), details_json=None))

        # Alert administrators of critical maintenance issue
        try:
            from src.notifications.publisher import publish_admin_alert
            publish_admin_alert(
                title="⚠️ Critical Maintenance Alert: Job Pipeline Failed",
                message=f"Pipeline run #{run_record_id} encountered an error: {str(exc)[:150]}",
                level="critical",
                link="#admin",
                data={"run_id": run_record_id, "error": str(exc)},
            )
        except Exception as alert_err:
            logger.debug("Failed to dispatch admin maintenance alert: %s", alert_err)

        raise

