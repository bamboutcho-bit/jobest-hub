"""Tests for enhanced Inbox & Replies, unified Outreach Ledger, and auto-apply threshold handling."""
import pytest
from datetime import datetime, timezone
from starlette.datastructures import Headers
from starlette.requests import Request
from sqlalchemy import select

from src.storage.db import get_session
from src.storage.models import (
    User,
    UserSetting,
    JobPosting,
    InboxMessage,
    OutboundMessage,
    UserJobApplication,
    EmailEvent,
    PipelineStage,
)
from src.dashboard.app import (
    api_inbox,
    api_inbox_stats,
    api_outbound,
    api_outbound_stats,
    api_resend_application,
)
from src.application.auto_apply import apply_to_job


def _make_dummy_request(path: str = "/api/inbox", query_string: str = "", user_id: int | None = None) -> Request:
    scope = {
        "type": "http",
        "method": "GET",
        "path": path,
        "query_string": query_string.encode(),
        "headers": [(b"host", b"testserver")],
    }
    req = Request(scope)
    if user_id:
        req.state.current_user = None  # mock if needed
    return req


def test_inbox_enriched_and_stats():
    with get_session() as session:
        # Create test user
        user = User(email="inbox_tester@example.com", full_name="Inbox Tester", role="user", is_active=True)
        session.add(user)
        session.flush()

        # Create test job
        job = JobPosting(
            dedup_hash="stripe_backend_test_hash",
            title="Senior Backend Engineer",
            company="Stripe",
            location="Paris, France",
            source_site="linkedin",
            job_url="https://stripe.com/jobs/123",
            user_id=user.id,
            match_score=85,
            pipeline_stage=PipelineStage.INTERVIEW_REQUESTED,
        )
        session.add(job)
        session.flush()

        # Create EmailEvent with classified intent
        event = EmailEvent(
            job_id=job.id,
            direction="inbound",
            message_id="msg-12345@stripe.com",
            sender_email="recruiter@stripe.com",
            subject="Interview Invitation for Senior Backend Engineer",
            body="Hello, we were very impressed by your background and would love to schedule a 30-minute interview.",
            classified_intent="interview_request",
        )
        session.add(event)

        # Create InboxMessage
        msg = InboxMessage(
            user_id=user.id,
            job_id=job.id,
            message_id="msg-12345@stripe.com",
            status="matched",
            subject="Interview Invitation for Senior Backend Engineer",
            sender_email="recruiter@stripe.com",
            reason="Matched job",
        )
        session.add(msg)
        session.commit()

        # Call api_inbox
        req = _make_dummy_request(path="/api/inbox", query_string="all_users=1")
        rows = api_inbox(req, limit=50)

        found = next((r for r in rows if r["message_id"] == "msg-12345@stripe.com"), None)
        assert found is not None
        assert found["company"] == "Stripe"
        assert found["job_title"] == "Senior Backend Engineer"
        assert found["intent"] == "interview_request"
        assert "impressed by your background" in found["body"]
        assert found["match_score"] == 85

        # Call api_inbox_stats
        stats = api_inbox_stats(req)
        assert stats["total_replies"] >= 1
        assert stats["interviews"] >= 1


def test_outbound_unified_ledger_and_stats():
    with get_session() as session:
        user = User(email="outreach_tester@example.com", full_name="Outreach Tester", role="user", is_active=True)
        session.add(user)
        session.flush()

        # 1. Job with email outbound
        job1 = JobPosting(
            dedup_hash="doctolib_arch_test_hash",
            title="Lead Python Architect",
            company="Doctolib",
            location="Remote",
            source_site="wttj",
            user_id=user.id,
            match_score=82,
            pipeline_stage=PipelineStage.APPLIED,
            application_email="talent@doctolib.com",
            application_status="sent",
            resume_attached=True,
        )
        session.add(job1)
        session.flush()

        out_msg = OutboundMessage(
            user_id=user.id,
            job_id=job1.id,
            idempotency_key=f"test:out:{job1.id}",
            email_type="application",
            recipient_email="talent@doctolib.com",
            subject="Application for Lead Python Architect",
            status="sent",
        )
        session.add(out_msg)

        # 2. Job with Web Portal application
        job2 = JobPosting(
            dedup_hash="datadog_fs_test_hash",
            title="Full Stack Developer",
            company="Datadog",
            location="Paris",
            source_site="indeed",
            application_url="https://careers.datadog.com/apply/456",
            user_id=user.id,
            match_score=78,
            pipeline_stage=PipelineStage.APPLIED,
            application_status="submitted",
            resume_attached=True,
        )
        session.add(job2)
        session.flush()

        user_app = UserJobApplication(
            user_id=user.id,
            job_id=job2.id,
            application_method="web_portal",
            status="submitted",
        )
        session.add(user_app)
        session.commit()

        # Call api_outbound
        req = _make_dummy_request(path="/api/outbound", query_string="all_users=1")
        records = api_outbound(req, limit=50)

        email_entry = next((r for r in records if r["job_id"] == job1.id), None)
        assert email_entry is not None
        assert email_entry["company"] == "Doctolib"
        assert email_entry["channel"] == "email"
        assert email_entry["status"] == "sent"
        assert email_entry["resume_attached"] is True
        assert email_entry["motivation_letter_attached"] is True

        portal_entry = next((r for r in records if r["job_id"] == job2.id), None)
        assert portal_entry is not None
        assert portal_entry["company"] == "Datadog"
        assert portal_entry["status"] == "submitted"

        # Check outbound stats
        stats = api_outbound_stats(req)
        assert stats["total_applications"] >= 2
        assert stats["sent_emails"] >= 1
        assert stats["web_submitted"] >= 1


def test_auto_apply_min_score_respects_custom_threshold():
    """Verify that candidate profile customized match threshold is respected without hardcoding 75."""
    with get_session() as session:
        user = User(email="threshold_user@example.com", full_name="Threshold Tester", role="user", is_active=True)
        session.add(user)
        session.flush()

        # User customized threshold is 60%
        setting = UserSetting(user_id=user.id, min_match_score=60)
        session.add(setting)

        # Job scoring 68 (previously blocked by 75 gate)
        job = JobPosting(
            dedup_hash="techcorp_se_test_hash",
            title="Software Engineer",
            company="TechCorp",
            user_id=user.id,
            match_score=68,
            application_emails="jobs@techcorp.com",
            raw_description="Python, FastAPI, Docker",
        )
        session.add(job)
        session.commit()

        job_dict = {
            "id": job.id,
            "user_id": user.id,
            "title": job.title,
            "company": job.company,
            "application_emails": job.application_emails,
            "raw_description": job.raw_description,
        }

        # Apply to job should generate a draft ready to send rather than rejecting
        res = apply_to_job(job_dict, session=session)
        assert res is not None
        assert res["application_status"] in ("draft", "sent")
        assert res["application_email"] == "jobs@techcorp.com"
