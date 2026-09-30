from datetime import datetime, timezone

from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker

from src.inbox.thread_matcher import match_job_for_message
from src.storage.models import Base, EmailEvent, JobPosting, PipelineStage


def test_rfc_thread_headers_win_over_subject_or_sender():
    engine = create_engine("sqlite:///:memory:")
    Base.metadata.create_all(engine)
    Session = sessionmaker(bind=engine)
    session = Session()
    job = JobPosting(
        dedup_hash="a" * 32,
        title="Backend Engineer",
        company="Acme",
        location="Berlin",
        pipeline_stage=PipelineStage.APPLIED,
        application_email="recruiter@acme.com",
        thread_subject="Application: Backend Engineer — Candidate",
    )
    session.add(job)
    session.flush()
    session.add(EmailEvent(
        job_id=job.id,
        direction="outbound",
        message_id="<original-123@local>",
        subject=job.thread_subject,
        created_at=datetime.now(timezone.utc),
    ))
    session.commit()

    msg = {
        "message_id": "<reply-456@recruiter>",
        "in_reply_to": "<original-123@local>",
        "references": "<older@recruiter> <original-123@local>",
        "subject": "Completely unrelated visible subject",
        "sender_email": "someone@other-domain.example",
    }
    assert match_job_for_message(session, msg).id == job.id


def test_ambiguous_fallback_is_not_guessed():
    engine = create_engine("sqlite:///:memory:")
    Base.metadata.create_all(engine)
    Session = sessionmaker(bind=engine)
    session = Session()
    for idx in range(2):
        session.add(JobPosting(
            dedup_hash=f"{idx+1:032d}",
            title="Backend Engineer",
            company="Acme",
            location="Berlin",
            pipeline_stage=PipelineStage.APPLIED,
            application_email="recruiter@acme.com",
            thread_subject="Application: Backend Engineer",
        ))
    session.commit()
    msg = {"subject": "No matching subject", "sender_email": "recruiter@acme.com"}
    assert match_job_for_message(session, msg) is None
