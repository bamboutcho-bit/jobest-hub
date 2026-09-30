"""Inbound recruiting-email classification through the shared AI provider chain."""
import json
import logging

from src.ai.service import AIService

logger = logging.getLogger(__name__)

CLASSIFIER_SYSTEM_PROMPT = """You classify one inbound recruiting email against a specific job application thread. Return ONLY a JSON object, no commentary:
{
  "intent": "interview_request" | "rejection" | "offer" | "info_request" | "other",
  "summary": "one sentence summary",
  "proposed_times": ["explicit dates/times mentioned, as written, else empty list"],
  "offer_details": "if intent is offer, extract salary/start date/terms, else null",
  "requires_human_review": true
}
Never invent details. Requires human review for offers, rejections, ambiguity, or non-routine content."""


def _parse(text: str) -> dict:
    cleaned = text.strip()
    if cleaned.startswith("```"):
        cleaned = cleaned.strip("`").removeprefix("json").strip()
    return json.loads(cleaned)


def classify_reply(job_title: str, company: str, email_body: str) -> dict:
    user_prompt = f"""Job thread: {job_title} @ {company}

Inbound email body:
{email_body[:6000]}
"""
    try:
        return AIService().generate(
            system_prompt=CLASSIFIER_SYSTEM_PROMPT,
            user_prompt=user_prompt,
            max_tokens=600,
            parse=_parse,
        )
    except Exception as exc:
        logger.error("Reply classification unavailable: %s", exc)
        return {
            "intent": "other", "summary": f"Unclassified: {exc}",
            "proposed_times": [], "offer_details": None, "requires_human_review": True,
        }
