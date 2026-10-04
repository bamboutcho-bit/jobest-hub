#!/usr/bin/env python3
"""Debug script to simulate the auto-apply queue and find why it stops early."""
import sys
from pathlib import Path
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from src.storage.db import get_session
from src.storage.models import JobPosting, PipelineStage, UserJobApplication, User
from sqlalchemy import select, func, or_, case, desc
from datetime import datetime, timezone
from config.settings import settings


with get_session() as s:
    today_start = datetime.now(timezone.utc).replace(hour=0, minute=0, second=0, microsecond=0)

    # Get user info
    user = s.scalar(select(User).where(User.role == "admin").order_by(User.id))
    user_apps_today = s.scalar(
        select(func.count(UserJobApplication.id)).where(
            UserJobApplication.user_id == user.id,
            UserJobApplication.applied_at >= today_start,
        )
    ) or 0
    daily_limit = user.daily_apply_limit or 200

    print("=" * 60)
    print(f"User: {user.email}")
    print(f"Plan: {user.current_plan}")
    print(f"Daily limit: {daily_limit}")
    print(f"Applied today (UserJobApplication): {user_apps_today}")
    print(f"Remaining quota: {daily_limit - user_apps_today}")
    print("=" * 60)

    blocked_statuses = [
        "blocked", "invalid_recipient", "recruiter_email_blocked", "bounced",
        "manual_linkedin", "manual_job_board", "manual_security_challenge",
        "manual_auth_required", "manual_non_ats", "manual_submit_button_not_found",
        "manual_apply_button_not_found", "manual_required_fields", "manual_unconfirmed_submission",
        "manual_portal", "no_application_channel", "resume_missing", "content_missing",
    ]

    apply_q = select(JobPosting).where(
        JobPosting.match_score >= settings.auto_apply_min_score,
        JobPosting.applied_at.is_(None),
        JobPosting.pipeline_stage != PipelineStage.APPLIED,
        or_(
            JobPosting.application_status.is_(None),
            JobPosting.application_status.in_(["not_attempted", "draft", "daily_quota_reached", ""]),
        ),
        ~JobPosting.application_status.in_(blocked_statuses)
    ).order_by(
        case((JobPosting.application_emails.isnot(None) & (JobPosting.application_emails != ""), 0), else_=1),
        desc(JobPosting.match_score)
    ).limit(500)

    pending = s.scalars(apply_q).all()

    has_email_count = 0
    has_web_count = 0
    no_channel_count = 0
    consecutive_unactionable = 0
    max_consecutive = 0

    for job in pending:
        has_email = bool(job.application_emails)
        has_web = bool(job.application_url or job.job_url) and settings.auto_apply_web_enabled
        if has_email:
            has_email_count += 1
            consecutive_unactionable = 0
        elif has_web:
            has_web_count += 1
            consecutive_unactionable = 0
        else:
            no_channel_count += 1
            consecutive_unactionable += 1
            if consecutive_unactionable > max_consecutive:
                max_consecutive = consecutive_unactionable

    print()
    print("=== AUTO-APPLY PENDING QUEUE SIMULATION ===")
    print(f"auto_apply_web_enabled: {settings.auto_apply_web_enabled}")
    print(f"auto_apply_mode: {settings.auto_apply_mode}")
    print(f"auto_apply_min_score: {settings.auto_apply_min_score}")
    print(f"Total pending matching jobs: {len(pending)}")
    print(f"  - With recruiter email:     {has_email_count}")
    print(f"  - With web URL (web apply): {has_web_count}")
    print(f"  - No channel at all:        {no_channel_count}")
    print(f"Max consecutive unactionable: {max_consecutive}")
    print()

    # Show the first 40 in queue order
    print("FIRST 40 IN QUEUE:")
    for i, job in enumerate(pending[:40]):
        has_email = bool(job.application_emails)
        has_web = bool(job.application_url or job.job_url) and settings.auto_apply_web_enabled
        channel = "EMAIL" if has_email else ("WEB" if has_web else "NONE")
        company = (job.company or "?")[:25]
        title = (job.title or "?")[:40]
        status = job.application_status or "none"
        print(f"  #{i+1:3d} score={job.match_score:3d} ch={channel:5s} st={status:20s} | {company}: {title}")
