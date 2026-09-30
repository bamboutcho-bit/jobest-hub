"""
Real-time alerting via Telegram Bot API. Uses plain `requests` against the
HTTP API (not the async python-telegram-bot client) since we only ever need
a fire-and-forget sendMessage from a batch job — no need for a running bot
process or webhook.
"""
import logging

import requests

from config.settings import settings

logger = logging.getLogger(__name__)

TELEGRAM_API = "https://api.telegram.org/bot{token}/sendMessage"


def format_alert(job: dict) -> str:
    skills = job.get("key_skills_matched") or []
    if isinstance(skills, str):
        skills = [skills]

    return (
        f"🚨 *New Visa-Sponsor Match: {job.get('match_score')}%*\n\n"
        f"*{job.get('title')}* @ {job.get('company')}\n"
        f"📍 {job.get('location')}\n"
        f"🛂 Visa: {'✅ ' + (job.get('visa_status_notes') or 'Detected') if job.get('visa_sponsorship_detected') else '❌ Not detected'}\n"
        f"🧩 Skills matched: {', '.join(skills)}\n"
        f"🔗 Listing: {job.get('job_url')}\n"
        f"📝 Application: {job.get('application_status') or 'manual'} ({job.get('application_method') or '—'})\n"
        f"🚀 Apply: {job.get('application_url') or job.get('job_url')}"
    )


def send_telegram_alert(job: dict) -> bool:
    if getattr(settings, "telegram_notify_only_interviews", True):
        logger.debug("Telegram routine alert skipped: telegram_notify_only_interviews is enabled.")
        return False

    if not settings.telegram_bot_token or not settings.telegram_chat_id:
        logger.warning("Telegram not configured — skipping alert.")
        return False

    url = TELEGRAM_API.format(token=settings.telegram_bot_token)
    payload = {
        "chat_id": settings.telegram_chat_id,
        "text": format_alert(job),
        "parse_mode": "Markdown",
        "disable_web_page_preview": False,
    }

    try:
        resp = requests.post(url, json=payload, timeout=15)
        resp.raise_for_status()
        return True
    except requests.RequestException as exc:
        logger.error("Telegram alert failed: %s", exc)
        return False


def format_interview_alert(data: dict) -> str:
    company = data.get("company", "Company")
    title = data.get("title", "Position")
    times = data.get("proposed_times") or data.get("scheduled_time") or []
    summary = data.get("summary") or data.get("notes") or "Interview invitation or confirmation received."
    link = data.get("url") or "http://localhost:8080/app#inbox"

    time_text = ""
    if isinstance(times, list) and times:
        time_text = "\n⏰ *Proposed Times:*\n" + "\n".join(f"  • {t}" for t in times) + "\n"
    elif isinstance(times, str) and times.strip():
        time_text = f"\n⏰ *Time:* {times}\n"

    return (
        f"🎉 *INTERVIEW SCHEDULED / REQUESTED!* 🚀\n\n"
        f"💼 *Company:* {company}\n"
        f"🎯 *Role:* {title}\n"
        f"{time_text}\n"
        f"📝 *Details:* {summary}\n\n"
        f"🔗 *Open Cockpit Inbox:* {link}"
    )


def send_telegram_interview_alert(interview_data: dict, bot_token: str | None = None, chat_id: str | None = None) -> bool:
    token = bot_token or settings.telegram_bot_token
    cid = chat_id or settings.telegram_chat_id
    if not token or not cid:
        logger.warning("Telegram not configured — skipping interview alert.")
        return False

    url = TELEGRAM_API.format(token=token)
    payload = {
        "chat_id": cid,
        "text": format_interview_alert(interview_data),
        "parse_mode": "Markdown",
        "disable_web_page_preview": False,
    }

    try:
        resp = requests.post(url, json=payload, timeout=15)
        resp.raise_for_status()
        logger.info("Telegram interview alert sent successfully for %s", interview_data.get("company"))
        return True
    except requests.RequestException as exc:
        logger.error("Telegram interview alert failed: %s", exc)
        return False

