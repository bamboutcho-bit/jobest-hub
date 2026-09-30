"""Pure email-to-job thread matching logic; safe to unit test without Claude/IMAP SDKs."""
import re

from sqlalchemy import select

from src.storage.models import EmailEvent, JobPosting, PipelineStage

MESSAGE_ID_RE = re.compile(r"<[^>\s]+>")


def _normalize_msg_id(value: str | None) -> str:
    return (value or "").strip().lower()


def _sender_domain(sender_email: str) -> str:
    return sender_email.rsplit("@", 1)[-1].lower() if "@" in sender_email else ""


def _normalized_subject(value: str) -> str:
    subject = (value or "").strip().lower()
    while True:
        updated = re.sub(r"^(?:re|fw|fwd)\s*:\s*", "", subject).strip()
        if updated == subject:
            return subject
        subject = updated


def match_job_for_message(session, msg: dict) -> JobPosting | None:
    """Primary RFC threading match; scored heuristics are only a non-ambiguous fallback."""
    message_id = _normalize_msg_id(msg.get("message_id"))
    in_reply_to = _normalize_msg_id(msg.get("in_reply_to"))
    references = {_normalize_msg_id(x) for x in (msg.get("references") or "").split() if x}
    related_ids = {x for x in {in_reply_to, *references} if x}

    candidates = session.scalars(select(JobPosting).where(JobPosting.pipeline_stage.in_([
        PipelineStage.APPLIED, PipelineStage.RESPONSE_RECEIVED,
        PipelineStage.INTERVIEW_REQUESTED, PipelineStage.INTERVIEW_SCHEDULED,
    ]))).all()
    if not candidates:
        return None

    if message_id:
        existing = session.scalar(select(EmailEvent).where(EmailEvent.message_id == message_id))
        if existing and existing.job_id:
            return session.get(JobPosting, existing.job_id)

    if related_ids:
        for job in candidates:
            event_ids = {_normalize_msg_id(e.message_id) for e in job.email_events if e.message_id}
            if event_ids.intersection(related_ids):
                return job

    incoming_subject = _normalized_subject(msg.get("subject"))
    if incoming_subject:
        subject_matches = [
            job for job in candidates
            if _normalized_subject(job.thread_subject or "") == incoming_subject
        ]
        if len(subject_matches) == 1:
            return subject_matches[0]

    sender = (msg.get("sender_email") or "").lower()
    sender_domain = _sender_domain(sender)
    scored: list[tuple[int, JobPosting]] = []
    for job in candidates:
        score = 0
        app_email = (job.application_email or "").lower()
        company = re.sub(r"[^a-z0-9]", "", (job.company or "").lower())
        if app_email and sender == app_email:
            score += 100
        if sender_domain and company and company in sender_domain.replace("-", ""):
            score += 40
        if incoming_subject and company and company in incoming_subject.replace(" ", ""):
            score += 10
        if score:
            scored.append((score, job))
    if not scored:
        return None
    scored.sort(key=lambda x: x[0], reverse=True)
    if len(scored) == 1 or scored[0][0] > scored[1][0]:
        return scored[0][1]
    return None
