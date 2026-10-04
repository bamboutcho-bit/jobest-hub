"""IMAP monitor with RFC Message-ID/In-Reply-To/References first-class matching."""
import email
import imaplib
import logging
import re
from datetime import datetime, timezone
from email.header import decode_header
from email.utils import parseaddr

from sqlalchemy import or_, select

from config.settings import settings
from src.inbox.reply_classifier import classify_reply
from src.storage.db import get_session
from src.storage.models import EmailEvent, InboxMessage, JobPosting, PipelineStage
from src.storage.outbound import suppress_recipient

logger = logging.getLogger(__name__)
MESSAGE_ID_RE = re.compile(r"<[^>\s]+>")


def _decode(value: str) -> str:
    """Decode MIME headers defensively, including malformed unknown-8bit parts."""
    if not value:
        return ""
    decoded_parts: list[str] = []
    for chunk, enc in decode_header(value):
        if isinstance(chunk, str):
            decoded_parts.append(chunk)
            continue
        charset = (enc or "utf-8").lower()
        if charset == "unknown-8bit":
            # Python's email package uses this marker for raw binary-ish header data.
            # Preserve the bytes as best-effort Latin-1 rather than raising LookupError.
            charset = "latin-1"
        try:
            decoded_parts.append(chunk.decode(charset, errors="replace"))
        except (LookupError, UnicodeError):
            decoded_parts.append(chunk.decode("utf-8", errors="replace"))
    return "".join(decoded_parts)


def _normalize_msg_id(value: str | None) -> str:
    return (value or "").strip().lower()


def _header_message_ids(value: str | None) -> list[str]:
    return [_normalize_msg_id(x) for x in MESSAGE_ID_RE.findall(value or "")]


def _extract_body(msg: email.message.Message) -> str:
    if msg.is_multipart():
        for part in msg.walk():
            if part.get_content_type() == "text/plain" and not part.get("Content-Disposition"):
                try:
                    return part.get_payload(decode=True).decode(part.get_content_charset() or "utf-8", errors="ignore")
                except Exception:
                    continue
        return ""
    try:
        payload = msg.get_payload(decode=True)
        return payload.decode(msg.get_content_charset() or "utf-8", errors="ignore") if payload else ""
    except Exception:
        return ""


def _fetch_unseen_messages(
    host: str | None = None,
    port: int | None = None,
    user: str | None = None,
    password: str | None = None,
    folder: str | None = None,
) -> list[dict]:
    i_host = host or settings.imap_host
    i_port = port or settings.imap_port
    i_user = user or settings.imap_user
    i_pass = password or settings.imap_password
    i_folder = folder or settings.imap_folder

    if not all([i_host, i_user, i_pass]):
        logger.debug("IMAP not configured for %s — skipping.", i_user)
        return []

    messages = []
    try:
        with imaplib.IMAP4_SSL(i_host, i_port) as conn:
            conn.login(i_user, i_pass)
            conn.select(i_folder)
            status, data = conn.search(None, "UNSEEN")
            if status != "OK":
                logger.warning("IMAP search failed for %s: %s", i_user, data)
                return []

            for num in data[0].split():
                status, msg_data = conn.fetch(num, "(RFC822)")
                if status != "OK":
                    continue
                raw = next((part[1] for part in msg_data if isinstance(part, tuple) and isinstance(part[1], bytes)), None)
                if not raw:
                    continue
                msg = email.message_from_bytes(raw)
                sender_name, sender_email = parseaddr(_decode(msg.get("From")))
                _, recipient_email = parseaddr(_decode(msg.get("To")))
                messages.append({
                    "imap_uid": num.decode(errors="ignore"),
                    "subject": _decode(msg.get("Subject")),
                    "from": _decode(msg.get("From")),
                    "sender_email": sender_email.lower(),
                    "recipient_email": recipient_email.lower(),
                    "message_id": _normalize_msg_id(msg.get("Message-ID")),
                    "in_reply_to": _normalize_msg_id(msg.get("In-Reply-To")),
                    "references": " ".join(_header_message_ids(msg.get("References"))),
                    "body": _extract_body(msg),
                })
    except Exception as exc:
        logger.warning("Failed to fetch messages for %s at %s: %s", i_user, i_host, exc)
    return messages


from src.inbox.thread_matcher import match_job_for_message

def _apply_classification(session, job: JobPosting, msg: dict, classification: dict) -> None:
    event = EmailEvent(
        job_id=job.id,
        direction="inbound",
        message_id=msg.get("message_id"),
        in_reply_to=msg.get("in_reply_to"),
        references=msg.get("references"),
        sender_email=msg.get("sender_email"),
        recipient_email=msg.get("recipient_email"),
        subject=msg.get("subject"),
        body=msg.get("body"),
        classified_intent=classification.get("intent"),
        imap_uid=msg.get("imap_uid"),
    )
    session.add(event)
    job.last_contact_at = datetime.now(timezone.utc)
    intent = classification.get("intent")
    if intent == "interview_request":
        job.pipeline_stage = PipelineStage.INTERVIEW_REQUESTED
        job.interview_notes = classification.get("summary")
    elif intent == "rejection":
        job.pipeline_stage = PipelineStage.REJECTED
    elif intent == "offer":
        job.pipeline_stage = PipelineStage.OFFER_RECEIVED
        job.offer_details = classification.get("offer_details") or classification.get("summary")
    else:
        job.pipeline_stage = PipelineStage.RESPONSE_RECEIVED
    logger.info("Job %s (%s @ %s): classified as %s -> stage=%s", job.id, job.title, job.company, intent, job.pipeline_stage.value)

    # Publish notification strictly to the user who owns this job application
    try:
        from src.notifications.publisher import publish_notification
        summary = classification.get("summary") or msg.get("subject") or "New recruiter message"
        if intent == "interview_request":
            title = f"🎉 Interview Request: {job.company}"
        elif intent == "offer":
            title = f"🏆 Job Offer: {job.company}!"
        elif intent == "rejection":
            title = f"Update: {job.company} ({job.title})"
        else:
            title = f"📬 Recruiter Reply: {job.company}"

        publish_notification(
            user_id=job.user_id,
            event_type="inbox_reply",
            title=title,
            message=f"Received a reply for {job.title} at {job.company}: {summary}",
            link="#inbox",
            data={
                "job_id": job.id,
                "company": job.company,
                "title": job.title,
                "intent": intent,
                "sender_email": msg.get("sender_email"),
            },
        )
    except Exception as notif_err:
        logger.debug("Failed to dispatch inbox_reply notification: %s", notif_err)


EMAIL_RE = re.compile(r"\b[A-Za-z0-9._%+-]+@[A-Za-z0-9.-]+\.[A-Za-z]{2,}\b")


def _is_bounce_message(msg: dict) -> bool:
    """Detect whether an incoming message is a Mailer-Daemon or DSN bounce notification."""
    sender = (msg.get("sender_email") or "").lower()
    subject = (msg.get("subject") or "").lower()
    body = (msg.get("body") or "").lower()

    if any(k in sender for k in ("mailer-daemon", "mail-daemon", "postmaster", "dsn@", "bounce@", "bounces@")):
        return True

    bounce_subjects = (
        "delivery status notification", "failure notice", "undelivered mail",
        "delivery failure", "mail delivery failed", "returned mail",
        "undeliverable:", "message not delivered", "could not be delivered",
        "delivery error", "address not found",
        # French Gmail / Mailer-Daemon bounce subjects
        "message non distribué", "message non distribue", "non distribué", "non distribue",
        "notification d'état de la distribution", "notification d'etat de la distribution",
        "échec de distribution", "echec de distribution", "adresse introuvable",
        "impossible de distribuer", "retour à l'expéditeur", "retour a l'expediteur",
    )
    if any(k in subject for k in bounce_subjects):
        return True

    bounce_body_signals = (
        "550 5.1.1", "550-5.1.1", "user unknown", "mailbox unavailable",
        "recipient address rejected", "address couldn't be found",
        "was unable to deliver your message", "message was not delivered",
        "your message wasn't delivered", "action: failed",
        # French bounce body signals
        "adresse introuvable", "n'a pas pu être distribué", "n'a pas pu etre distribue",
        "impossible de distribuer le message", "le compte n'existe pas",
        "adresse de messagerie introuvable", "impossible d'acheminer le message",
        "adresse non valide", "utilisateur inconnu", "message non distribué",
    )
    if any(k in body for k in bounce_body_signals):
        return True

    return False


def _extract_bounced_recipient(msg: dict, matched_job: JobPosting | None = None) -> str | None:
    """Extract the actual recipient address that bounced from a Mailer-Daemon error message."""
    body = msg.get("body") or ""
    subject = msg.get("subject") or ""

    if matched_job and matched_job.application_email:
        app_em = matched_job.application_email.lower().strip()
        if app_em in body.lower() or app_em in subject.lower():
            return app_em

    # 1. DSN header pattern
    m = re.search(r"Final-Recipient:\s*(?:rfc822;)?\s*<*([a-zA-Z0-9._%+-]+@[a-zA-Z0-9.-]+\.[a-zA-Z]{2,})>*", body, re.I)
    if m:
        return m.group(1).lower().strip()

    # 2. English & French bounce text patterns
    m = re.search(r"(?:wasn't delivered to|failed to deliver to|delivery to|address couldn't be found for|tried to reach does not exist:?|n'a pas pu être distribué à|n'a pas pu etre distribue a|impossible de distribuer à|impossible de distribuer a|adresse introuvable pour|adresse suivante:?)\s*<*([a-zA-Z0-9._%+-]+@[a-zA-Z0-9.-]+\.[a-zA-Z]{2,})>*", body, re.I)
    if m:
        return m.group(1).lower().strip()

    # 3. "To: <foo@bar.com>" inside the bounce notification
    m = re.search(r"\bTo:\s*<*([a-zA-Z0-9._%+-]+@[a-zA-Z0-9.-]+\.[a-zA-Z]{2,})>*", body, re.I)
    if m:
        cand = m.group(1).lower().strip()
        if cand != (settings.sender_email or "").lower() and "mailer-daemon" not in cand:
            return cand

    # 4. Fallback search for any email in body excluding sender and system domains
    for found in EMAIL_RE.findall(body):
        f_lower = found.lower().strip()
        if f_lower != (settings.sender_email or "").lower() and not any(k in f_lower for k in ("mailer-daemon", "postmaster", "google.com", "microsoft.com")):
            return f_lower

    if matched_job and matched_job.application_email:
        return matched_job.application_email.lower().strip()

    return None


def check_inbox_once() -> dict:
    messages = list(_fetch_unseen_messages())

    # Check any user-specific mailboxes configured with IMAP credentials
    try:
        from src.storage.models import UserSetting
        with get_session() as s:
            user_settings = s.scalars(select(UserSetting).where(UserSetting.imap_password.isnot(None))).all()
            seen_mailboxes = {(settings.imap_host, settings.imap_user)}
            for us in user_settings:
                user_email = us.sender_email
                user_pass = us.imap_password
                user_host = us.imap_host or "imap.gmail.com"
                user_port = us.imap_port or 993
                if user_email and user_pass and (user_host, user_email) not in seen_mailboxes:
                    seen_mailboxes.add((user_host, user_email))
                    user_msgs = _fetch_unseen_messages(host=user_host, port=user_port, user=user_email, password=user_pass)
                    messages.extend(user_msgs)
    except Exception as exc:
        logger.debug("User mailboxes scan skipped: %s", exc)

    matched = unmatched = classified = duplicates = bounces = 0
    with get_session() as session:
        for msg in messages:
            if msg.get("message_id") and session.scalar(select(InboxMessage.id).where(InboxMessage.message_id == msg["message_id"])):
                duplicates += 1
                continue
            if msg.get("message_id") and session.scalar(select(EmailEvent.id).where(EmailEvent.message_id == msg["message_id"])):
                session.add(InboxMessage(message_id=msg.get("message_id"), imap_uid=msg.get("imap_uid"), status="duplicate", subject=msg.get("subject"), sender_email=msg.get("sender_email"), reason="Message-ID already exists in email_events"))
                duplicates += 1
                continue

            # 1. Handle Mailer-Daemon & Delivery Status Failure (Bounce) Emails
            if _is_bounce_message(msg):
                bounces += 1
                job = match_job_for_message(session, msg)
                failed_recipient = _extract_bounced_recipient(msg, job)

                # Suppress the failed recipient address (NEVER suppress mailer-daemon)
                if failed_recipient and "mailer-daemon" not in failed_recipient and failed_recipient != (settings.sender_email or "").lower():
                    suppress_recipient(
                        session,
                        address=failed_recipient,
                        reason="bounce",
                        details=f"Bounce: {msg.get('subject')}",
                        user_id=job.user_id if job else None,
                    )
                    logger.warning("Mailer-Daemon bounce detected. Suppressed failed recipient: %s", failed_recipient)

                if job:
                    job.application_status = "bounced"
                    job.application_error = f"Delivery failed (Mailer-Daemon): {failed_recipient or 'Recipient address rejected'}"

                    # Clean invalid email from job.application_emails
                    if failed_recipient and job.application_emails:
                        remaining = [e.strip() for e in job.application_emails.split(",") if e.strip().lower() != failed_recipient]
                        job.application_emails = ", ".join(remaining) if remaining else None
                    if failed_recipient and job.application_email and job.application_email.strip().lower() == failed_recipient:
                        job.application_email = None

                    # Record bounce event for audit (direction inbound, intent bounce)
                    event = EmailEvent(
                        job_id=job.id,
                        direction="inbound",
                        message_id=msg.get("message_id"),
                        in_reply_to=msg.get("in_reply_to"),
                        references=msg.get("references"),
                        sender_email=msg.get("sender_email"),
                        recipient_email=failed_recipient or msg.get("recipient_email"),
                        subject=msg.get("subject"),
                        body=msg.get("body"),
                        classified_intent="bounce",
                        imap_uid=msg.get("imap_uid"),
                    )
                    session.add(event)
                    session.add(InboxMessage(
                        user_id=job.user_id,
                        job_id=job.id,
                        message_id=msg.get("message_id"),
                        imap_uid=msg.get("imap_uid"),
                        status="bounced",
                        subject=msg.get("subject"),
                        sender_email=msg.get("sender_email"),
                        reason=f"Delivery failure for job {job.id} ({failed_recipient or 'unknown'})",
                    ))
                    logger.warning("Job %s (%s @ %s): marked application_status=bounced due to Mailer-Daemon error for %s",
                                   job.id, job.title, job.company, failed_recipient)
                    matched += 1
                else:
                    session.add(InboxMessage(
                        user_id=None,
                        job_id=None,
                        message_id=msg.get("message_id"),
                        imap_uid=msg.get("imap_uid"),
                        status="bounced_unmatched",
                        subject=msg.get("subject"),
                        sender_email=msg.get("sender_email"),
                        reason=f"Bounce notification for {failed_recipient or 'unknown recipient'} (no job matched)",
                    ))
                    unmatched += 1

                # CRITICAL: Skip classify_reply and do not treat bounce as recruiter response!
                continue

            # 2. Match legitimate recruiter reply to job thread
            job = match_job_for_message(session, msg)
            body_lower = (msg.get("body") or "").lower()
            optout_signals = ("unsubscribe", "do not contact", "remove me", "stop contacting")
            if any(signal in body_lower for signal in optout_signals):
                suppress_recipient(
                    session,
                    address=msg.get("sender_email"),
                    reason="opt_out",
                    details=msg.get("subject"),
                    user_id=job.user_id if job else None,
                )

            if job is None:
                session.add(InboxMessage(
                    user_id=None,
                    job_id=None,
                    message_id=msg.get("message_id"),
                    imap_uid=msg.get("imap_uid"),
                    status="unmatched",
                    subject=msg.get("subject"),
                    sender_email=msg.get("sender_email"),
                    reason="No unique RFC-thread or fallback match",
                ))
                unmatched += 1
                continue
            matched += 1
            classification = classify_reply(job.title, job.company, msg.get("body", ""))
            _apply_classification(session, job, msg, classification)
            session.add(InboxMessage(
                user_id=job.user_id,
                job_id=job.id,
                message_id=msg.get("message_id"),
                imap_uid=msg.get("imap_uid"),
                status="matched",
                subject=msg.get("subject"),
                sender_email=msg.get("sender_email"),
                reason=f"Matched job {job.id}",
            ))
            classified += 1

    summary = {"fetched": len(messages), "matched": matched, "unmatched": unmatched, "classified": classified, "bounces": bounces, "duplicates": duplicates}
    logger.info("Inbox check summary: %s", summary)
    return summary
