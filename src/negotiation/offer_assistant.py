"""Offer negotiation drafting through the shared AI provider chain."""
import json
import logging
import os
from datetime import datetime

from config.candidate_profile import CANDIDATE_PROFILE
from src.ai.service import AIService

logger = logging.getLogger(__name__)

NEGOTIATION_SYSTEM_PROMPT = f"""You help a candidate think through and draft a response to a job offer.
You are NOT authorized to accept, decline, or make any binding commitment.
CANDIDATE PROFILE:
{CANDIDATE_PROFILE}

Return ONLY JSON:
{{
  "assessment": "2-3 sentences",
  "open_questions": ["clarifying questions"],
  "counter_offer_draft": "professional email draft"
}}
"""


def _parse(text: str) -> dict:
    cleaned = text.strip()
    if cleaned.startswith("```"):
        cleaned = cleaned.strip("`").removeprefix("json").strip()
    return json.loads(cleaned)


def draft_counter_offer(job_title: str, company: str, offer_details: str) -> dict:
    user_prompt = f"Job: {job_title} @ {company}\n\nOffer details as received:\n{offer_details}"
    try:
        result = AIService().generate(
            system_prompt=NEGOTIATION_SYSTEM_PROMPT,
            user_prompt=user_prompt,
            max_tokens=900,
            parse=_parse,
        )
    except Exception as exc:
        logger.error("Negotiation assistant unavailable: %s", exc)
        result = {"assessment": f"Unavailable: {exc}", "open_questions": [], "counter_offer_draft": ""}

    os.makedirs("negotiation_drafts", exist_ok=True)
    safe_company = "".join(c for c in company if c.isalnum())[:30]
    path = f"negotiation_drafts/{safe_company}_{datetime.now().strftime('%Y%m%d_%H%M%S')}.txt"
    with open(path, "w", encoding="utf-8") as f:
        f.write(f"Assessment:\n{result.get('assessment')}\n\n")
        f.write("Open questions:\n" + "\n".join(f"- {q}" for q in result.get("open_questions", [])) + "\n\n")
        f.write("Draft response:\n" + result.get("counter_offer_draft", ""))
    logger.info("Negotiation draft written: %s (REVIEW BEFORE SENDING)", path)
    return result
