"""Interview reply helpers preserving the RFC thread headers."""
import logging
import uuid
from datetime import datetime, timedelta, timezone
from email.mime.multipart import MIMEMultipart
from email.mime.text import MIMEText
from email.utils import make_msgid

from icalendar import Calendar, Event

from config.settings import settings

logger = logging.getLogger(__name__)


def build_ics_invite(job_title: str, company: str, start_dt_utc: datetime, duration_minutes: int = 45) -> bytes:
    cal = Calendar()
    cal.add("prodid", "-//job-search-automation//interview-scheduler//")
    cal.add("version", "2.0")
    event = Event()
    event.add("uid", str(uuid.uuid4()))
    event.add("summary", f"Interview: {job_title} @ {company}")
    event.add("dtstart", start_dt_utc)
    event.add("dtend", start_dt_utc + timedelta(minutes=duration_minutes))
    event.add("dtstamp", datetime.now(timezone.utc))
    event.add("description", f"Interview for {job_title} at {company}.")
    cal.add_component(event)
    return cal.to_ical()


def propose_default_slots(count: int = 3) -> list[datetime]:
    now = datetime.now(timezone.utc)
    return [now + timedelta(days=i + 1, hours=14) for i in range(count)]


def draft_scheduling_reply(job_title: str, company: str, proposed_times: list[str]) -> tuple[str, bytes | None]:
    if proposed_times:
        return (
            f"Thank you for the interview invitation for the {job_title} role at {company}. "
            f"The proposed time(s) — {', '.join(proposed_times)} — work well on my end. "
            f"Please confirm and I'll block the calendar accordingly.\n\nBest regards,\n{settings.sender_display_name}",
            None,
        )
    slots = propose_default_slots()
    slot_lines = "\n".join(f"- {s.strftime('%A %d %B, %H:%M UTC')}" for s in slots)
    body = (
        f"Thank you for reaching out about the {job_title} role at {company}. I would be glad to schedule an interview.\n\n"
        f"A few options that work on my end ({settings.availability_notes}):\n\n{slot_lines}\n\n"
        f"Happy to adjust if none fit your schedule.\n\nBest regards,\n{settings.sender_display_name}"
    )
    return body, build_ics_invite(job_title, company, slots[0])


def build_reply_mime(to_addr: str, subject: str, body: str, ics_bytes: bytes | None, in_reply_to: str | None = None, references: str | None = None) -> MIMEMultipart:
    msg = MIMEMultipart()
    msg["Message-ID"] = make_msgid()
    msg["Subject"] = f"Re: {subject}"
    msg["From"] = f"{settings.sender_display_name} <{settings.sender_email}>"
    msg["To"] = to_addr
    if in_reply_to:
        msg["In-Reply-To"] = in_reply_to
        msg["References"] = " ".join(x for x in [references or "", in_reply_to] if x)
    msg.attach(MIMEText(body, "plain", "utf-8"))
    if ics_bytes:
        ics_part = MIMEText(ics_bytes.decode("utf-8"), "calendar;method=REQUEST")
        ics_part.add_header("Content-Disposition", "attachment", filename="interview_invite.ics")
        msg.attach(ics_part)
    return msg
