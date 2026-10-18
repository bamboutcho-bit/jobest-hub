"""Tests for enhanced Inbox & Replies, unified Outreach Ledger, and auto-apply threshold handling."""
import pytest
import uuid
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
    record_job_application,
    record_external_outreach,
    RecordApplicationPayload,
    RecordExternalOutreachPayload,
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
    u_id = uuid.uuid4().hex[:8]
    with get_session() as session:
        # Create test user
        user = User(email=f"inbox_{u_id}@example.com", full_name="Inbox Tester", role="user", is_active=True)
        session.add(user)
        session.flush()

        # Create test job
        job = JobPosting(
            dedup_hash=f"stripe_backend_{u_id}",
            title="Senior Backend Engineer",
            company="Stripe",
            location="Paris, France",
            source_site="linkedin",
            job_url=f"https://stripe.com/jobs/{u_id}",
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
    u_id = uuid.uuid4().hex[:8]
    with get_session() as session:
        user = User(email=f"outreach_{u_id}@example.com", full_name="Outreach Tester", role="user", is_active=True)
        session.add(user)
        session.flush()

        # 1. Job with email outbound
        job1 = JobPosting(
            dedup_hash=f"doctolib_arch_{u_id}",
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
            idempotency_key=f"test:out:{job1.id}:{u_id}",
            email_type="application",
            recipient_email="talent@doctolib.com",
            subject="Application for Lead Python Architect",
            status="sent",
        )
        session.add(out_msg)

        # 2. Job with Web Portal application
        job2 = JobPosting(
            dedup_hash=f"datadog_fs_{u_id}",
            title="Full Stack Developer",
            company="Datadog",
            location="Paris",
            source_site="indeed",
            application_url=f"https://careers.datadog.com/apply/{u_id}",
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
    u_id = uuid.uuid4().hex[:8]
    with get_session() as session:
        user = User(email=f"threshold_{u_id}@example.com", full_name="Threshold Tester", role="user", is_active=True)
        session.add(user)
        session.flush()

        # User customized threshold is 60%
        setting = UserSetting(user_id=user.id, min_match_score=60)
        session.add(setting)

        # Job scoring 68 (previously blocked by 75 gate)
        job = JobPosting(
            dedup_hash=f"techcorp_se_{u_id}",
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


def test_specify_application_and_external_outreach_tracking():
    """Verify that jobs can be specified as applied via LinkedIn or Indeed and tracked in Outreach Ledger."""
    u_id = uuid.uuid4().hex[:8]
    with get_session() as session:
        user = User(email=f"app_channel_{u_id}@example.com", full_name="Channel Tester", role="user", is_active=True)
        session.add(user)
        session.flush()

        # 1. Existing scraped job specified as applied via LinkedIn
        job1 = JobPosting(
            dedup_hash=f"spotify_backend_{u_id}",
            title="Senior Systems Engineer",
            company="Spotify",
            location="Stockholm",
            source_site="linkedin",
            user_id=user.id,
            match_score=88,
            pipeline_stage=PipelineStage.DISCOVERED,
        )
        session.add(job1)
        session.commit()

        req = _make_dummy_request(path=f"/api/jobs/{job1.id}/record_application")
        req.state.current_user = user

        payload_linkedin = RecordApplicationPayload(
            channel="linkedin",
            status="submitted",
            notes="Applied using Easy Apply with tailored CV",
            application_url="https://linkedin.com/jobs/view/999888",
        )
        res1 = record_job_application(job1.id, payload_linkedin, req, _=True)
        assert res1["ok"] is True
        assert res1["channel"] == "linkedin"

        # Check job updated
        with get_session() as s2:
            j_updated = s2.get(JobPosting, job1.id)
            assert j_updated.pipeline_stage == PipelineStage.APPLIED
            assert j_updated.application_method == "linkedin"
            assert j_updated.application_status == "submitted"
            assert "Easy Apply" in (j_updated.interview_notes or "")

        # 2. Record external application made via Indeed
        req_ext = _make_dummy_request(path="/api/outbound/record_external")
        req_ext.state.current_user = user

        payload_indeed = RecordExternalOutreachPayload(
            company="Revolut",
            job_title="Lead Data Engineer",
            channel="indeed",
            job_url="https://indeed.com/viewjob?jk=123456",
            status="submitted",
            notes="Applied through Indeed apply button",
        )
        res2 = record_external_outreach(payload_indeed, req_ext, _=True)
        assert res2["ok"] is True
        assert res2["channel"] == "indeed"
        indeed_job_id = res2["job_id"]
        assert indeed_job_id is not None

        # 3. Query outbound ledger with channel filters
        req_outbound_li = _make_dummy_request(path="/api/outbound", query_string="status_filter=linkedin&all_users=1")
        records_li = api_outbound(req_outbound_li, limit=50)
        found_li = next((r for r in records_li if r["job_id"] == job1.id), None)
        assert found_li is not None
        assert found_li["channel"] == "linkedin"
        assert "LinkedIn" in found_li["channel_label"]

        req_outbound_in = _make_dummy_request(path="/api/outbound", query_string="status_filter=indeed&all_users=1")
        records_in = api_outbound(req_outbound_in, limit=50)
        found_in = next((r for r in records_in if r["job_id"] == indeed_job_id), None)
        assert found_in is not None
        assert found_in["channel"] == "indeed"
        assert "Indeed" in found_in["channel_label"]

        # 4. Outbound stats
        stats = api_outbound_stats(req_outbound_li)
        assert stats["linkedin_applications"] >= 1
        assert stats["indeed_applications"] >= 1


def test_linkedin_and_indeed_filters_isolation():
    """Verify LinkedIn and Indeed filters never mix applications, even when scraped from opposing sources."""
    u_id = uuid.uuid4().hex[:8]
    with get_session() as session:
        user = User(email=f"isolate_{u_id}@example.com", full_name="Isolation Tester", role="user", is_active=True)
        session.add(user)
        session.flush()

        # Job scraped from LinkedIn, but applied via Indeed
        job_li_src = JobPosting(
            dedup_hash=f"scraped_li_but_applied_indeed_{u_id}",
            title="Senior Backend Engineer",
            company="IndeedAppliedCo",
            source_site="linkedin",
            job_url="https://linkedin.com/jobs/view/111",
            application_method="indeed",
            application_status="submitted",
            pipeline_stage=PipelineStage.APPLIED,
            user_id=user.id,
        )
        # Job scraped from Indeed, but applied via LinkedIn
        job_in_src = JobPosting(
            dedup_hash=f"scraped_indeed_but_applied_li_{u_id}",
            title="Senior Frontend Engineer",
            company="LinkedInAppliedCo",
            source_site="indeed",
            job_url="https://indeed.com/viewjob?jk=222",
            application_method="linkedin",
            application_status="submitted",
            pipeline_stage=PipelineStage.APPLIED,
            user_id=user.id,
        )
        # Job discovered on LinkedIn but NEVER applied
        job_unapplied = JobPosting(
            dedup_hash=f"unapplied_li_{u_id}",
            title="Discovered Only",
            company="NeverAppliedCo",
            source_site="linkedin",
            pipeline_stage=PipelineStage.DISCOVERED,
            application_status="not_attempted",
            user_id=user.id,
        )
        session.add_all([job_li_src, job_in_src, job_unapplied])
        session.commit()

        # Query LinkedIn filter
        req_li = _make_dummy_request(path="/api/outbound", query_string="status_filter=linkedin")
        req_li.state.current_user = user
        records_li = api_outbound(req_li, limit=50)

        li_ids = [r["job_id"] for r in records_li]
        assert job_in_src.id in li_ids, "LinkedIn applied job must appear under LinkedIn filter"
        assert job_li_src.id not in li_ids, "Indeed applied job must NOT appear under LinkedIn filter even if scraped from LinkedIn"
        assert job_unapplied.id not in li_ids, "Unapplied discovered job must NOT appear in Outreach Ledger"

        # Query Indeed filter
        req_in = _make_dummy_request(path="/api/outbound", query_string="status_filter=indeed")
        req_in.state.current_user = user
        records_in = api_outbound(req_in, limit=50)

        in_ids = [r["job_id"] for r in records_in]
        assert job_li_src.id in in_ids, "Indeed applied job must appear under Indeed filter"
        assert job_in_src.id not in in_ids, "LinkedIn applied job must NOT appear under Indeed filter even if scraped from Indeed"
        assert job_unapplied.id not in in_ids, "Unapplied discovered job must NOT appear in Outreach Ledger"

