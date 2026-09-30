"""
Email alerting via SMTP (works with Gmail app passwords, SES SMTP creds,
etc). Kept dependency-free (stdlib smtplib) so it never blocks on an
optional package.
"""
import logging
import smtplib
from email.mime.text import MIMEText

from config.settings import settings

logger = logging.getLogger(__name__)


def send_email_alert(job: dict) -> bool:
    if not getattr(settings, "email_alerts_enabled", False):
        logger.debug("Email alerts are disabled (EMAIL_ALERTS_ENABLED=false) — skipping email alert.")
        return False

    target_email = settings.alert_email_to
    if not target_email:
        logger.debug("No alert_email_to configured — skipping email alert.")
        return False

    if not all([settings.smtp_host, settings.smtp_user, settings.smtp_password]):
        logger.debug("SMTP not fully configured — skipping email alert.")
        return False

    subject = f"[Job Alert] {job.get('match_score')}% match — {job.get('title')} @ {job.get('company')}"
    body = (
        f"Title: {job.get('title')}\n"
        f"Company: {job.get('company')}\n"
        f"Location: {job.get('location')}\n"
        f"Match score: {job.get('match_score')}%\n"
        f"Visa sponsorship detected: {job.get('visa_sponsorship_detected')}\n"
        f"Notes: {job.get('visa_status_notes')}\n"
        f"Skills matched: {job.get('key_skills_matched')}\n"
        f"Listing: {job.get('job_url')}\n"
        f"Application status: {job.get('application_status') or 'manual'}\n"
        f"Application method: {job.get('application_method') or '—'}\n"
        f"Application link: {job.get('application_url') or '—'}\n"
    )

    msg = MIMEText(body, "plain", "utf-8")
    msg["Subject"] = subject
    msg["From"] = settings.smtp_user
    msg["To"] = target_email

    try:
        with smtplib.SMTP(settings.smtp_host, settings.smtp_port, timeout=20) as server:
            server.starttls()
            server.login(settings.smtp_user, settings.smtp_password)
            server.sendmail(settings.smtp_user, [target_email], msg.as_string())
        return True
    except Exception as exc:
        logger.error("Email alert failed: %s", exc)
        return False
