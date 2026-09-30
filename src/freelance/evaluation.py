"""Dynamic evaluation of freelance leads with multi-provider AI and offline heuristic fallback.

Scores each discovered lead for legitimacy, budget viability, and technical
fit against the candidate's active freelance profile.
"""
from __future__ import annotations

import json
import logging
import re
from datetime import datetime, timezone
from typing import Any

from src.ai.service import AIService, AIProviderUnavailable, AIQuotaExceeded
from src.candidate.profile_manager import get_active_profile
from src.storage.db import get_session
from src.storage.models import FreelanceLead, FreelanceStage

logger = logging.getLogger(__name__)

SPAM_SIGNALS = [
    "[for hire]", "for hire", "i am a developer", "my portfolio",
    "hire me", "looking for work", "crypto giveaway", "airdrop",
    "whatsapp only", "telegram @", "dm me on telegram",
    "unpaid", "equity only", "equity-only", "for equity", "rev share only", "no budget",
]


def _build_evaluation_prompt(profile: dict[str, Any] | None = None) -> str:
    """Build dynamic evaluation prompt from the active candidate profile."""
    if profile is None:
        profile = get_active_profile()

    name = profile.get("name") or "Developer"
    headline = profile.get("headline") or "Software Engineer"
    stack = profile.get("core_stack") or ["Java", "Spring Boot", "React", "PostgreSQL"]
    services = profile.get("freelance_services") or [
        "Custom MVP & Prototype Development",
        "Backend API Architecture & Implementation",
        "Full-Stack Web Application Engineering",
    ]
    daily_rate = profile.get("freelance_daily_eur") or profile.get("freelance_daily_rate") or 400
    hourly_rate = profile.get("freelance_hourly_usd") or profile.get("freelance_hourly_rate") or 50
    currency = profile.get("freelance_currency") or "EUR"

    return f"""You evaluate freelance project leads for a software engineer.

FREELANCER PROFILE:
Name: {name}
Headline: {headline}
Services: {', '.join(services)}
Core Stack: {', '.join(stack)}
Standard Rates: {daily_rate} {currency}/day, {hourly_rate} USD/hour

For each project lead, evaluate:
1. Is this a LEGITIMATE project from a real client looking to hire? (not spam, not another freelancer advertising themselves)
2. Does the project match the freelancer's skills?
3. Is the budget reasonable (if mentioned)?
4. What is the estimated project scope?

Return ONLY valid JSON:
{{
  "is_spam": false,
  "match_score": 80,
  "budget_estimate": "$2000-5000",
  "project_scope": "2-3 week MVP: Backend API + UI flow",
  "client_type": "founder",
  "reason": "Strong match: project requires {stack[0] if stack else 'API'} and aligns with rates"
}}

match_score: 0-100 where 100 = perfect fit.
client_type: one of "individual", "founder", "agency", "company".
"""


def _parse_evaluation(text: str) -> dict:
    """Parse the AI evaluation JSON output."""
    cleaned = text.strip()
    if cleaned.startswith("```"):
        cleaned = cleaned.strip("`").removeprefix("json").strip()
    return json.loads(cleaned)


def evaluate_lead_heuristic(lead: FreelanceLead, profile: dict[str, Any] | None = None) -> dict[str, Any]:
    """Offline heuristic evaluation for freelance leads when LLMs are offline."""
    if profile is None:
        profile = get_active_profile()

    desc = getattr(lead, "raw_description", None) or getattr(lead, "description", None) or ""
    text = f"{lead.title or ''} {desc}".lower()

    # 1. Spam detection
    is_spam = False
    for spam_kw in SPAM_SIGNALS:
        if spam_kw in text:
            is_spam = True
            break

    # 2. Skill matching
    core_stack = profile.get("core_stack") or []
    keywords = profile.get("keywords") or []
    all_skills = list(dict.fromkeys(core_stack + keywords))

    matched_skills = []
    for skill in all_skills:
        pattern = r"\b" + re.escape(skill.lower()) + r"\b"
        if re.search(pattern, text):
            matched_skills.append(skill)

    # 3. Budget indicators
    budget_estimate = "Flexible / Discussion"
    budget_matches = re.findall(r"(\$\s*[\d,]+|\€\s*[\d,]+|[\d,]+\s*(?:usd|eur|dollars|k))", text)
    if budget_matches:
        budget_estimate = f"Est: {', '.join(budget_matches[:2])}"

    # 4. Scope and Client type
    client_type = lead.client_type or "individual"
    if any(k in text for k in ("startup", "cofounder", "founder", "mvp", "pre-seed")):
        client_type = "founder"
        scope = "1–3 week MVP Development"
    elif any(k in text for k in ("agency", "client project", "white label", "subcontract")):
        client_type = "agency"
        scope = "Contract Staff Augmentation"
    elif any(k in text for k in ("enterprise", "company", "inc", "ltd", "corp")):
        client_type = "company"
        scope = "System Integration & Architecture"
    else:
        scope = "Custom Feature & Bug Fixing"

    # Score calculation
    if is_spam:
        match_score = 0
    else:
        base_score = 40
        skill_bonus = min(50, len(matched_skills) * 12)
        has_contact = 10 if (getattr(lead, "contact_email", None) or getattr(lead, "contact_url", None)) else 0
        match_score = max(0, min(100, base_score + skill_bonus + has_contact))

    skills_str = ", ".join(matched_skills[:3]) if matched_skills else "general development"
    reason = f"Heuristic evaluation: matched {skills_str}." if not is_spam else "Filtered as spam or unpaid inquiry."

    return {
        "is_spam": is_spam,
        "spam": is_spam,
        "match_score": match_score,
        "action": "ignore" if is_spam or match_score < 40 else "pitch",
        "budget_estimate": budget_estimate,
        "project_scope": scope,
        "client_type": client_type,
        "reason": reason,
    }


def evaluate_lead(lead_id: int) -> str:
    """Evaluate a single freelance lead using AI with offline heuristic fallback.

    Returns a status string: 'evaluated', 'spam', 'error', 'quota_exceeded'.
    """
    with get_session() as session:
        lead = session.get(FreelanceLead, lead_id)
        if lead is None or lead.evaluated:
            return "already_done"

        profile = get_active_profile(user_id=lead.user_id, session=session)
        description = (lead.raw_description or "")[:3000]
        user_prompt = (
            f"PROJECT LEAD:\n"
            f"Title: {lead.title}\n"
            f"Client: {lead.client_name}\n"
            f"Source: {lead.source_platform}\n"
            f"URL: {lead.source_url}\n\n"
            f"Description:\n{description}\n"
        )

        system_prompt = _build_evaluation_prompt(profile)
        result = None

        try:
            result = AIService(session=session).generate(
                system_prompt=system_prompt,
                user_prompt=user_prompt,
                max_tokens=500,
                parse=_parse_evaluation,
            )
        except AIQuotaExceeded:
            logger.info("AI quota exceeded for freelance lead %d; using heuristic evaluator", lead_id)
            result = evaluate_lead_heuristic(lead, profile)
        except (AIProviderUnavailable, Exception) as exc:
            logger.info("AI unavailable for freelance lead %d (%s); using heuristic evaluator", lead_id, exc)
            result = evaluate_lead_heuristic(lead, profile)

        if not result or not isinstance(result, dict):
            result = evaluate_lead_heuristic(lead, profile)

        # Apply the evaluation results
        lead.evaluated = True
        lead.match_score = max(0, min(100, int(result.get("match_score", 0))))
        lead.is_spam = bool(result.get("is_spam", False))
        lead.budget_estimate = str(result.get("budget_estimate", ""))[:120]
        lead.project_scope = str(result.get("project_scope", ""))
        lead.evaluation_reason = str(result.get("reason", ""))
        lead.updated_at = datetime.now(timezone.utc)

        # Update client type
        ai_client_type = result.get("client_type")
        if ai_client_type in ("individual", "founder", "agency", "company"):
            lead.client_type = ai_client_type

        # Advance stage based on evaluation
        if lead.is_spam:
            lead.stage = FreelanceStage.LOST
            return "spam"
        elif lead.match_score >= 50:
            lead.stage = FreelanceStage.EVALUATED
            return "evaluated"
        else:
            lead.stage = FreelanceStage.LOST
            return "low_score"


def evaluate_pending_leads(*, limit: int = 50, user_id: int | None = None) -> dict:
    """Evaluate all pending freelance leads up to the given limit.

    Returns a summary dict with counts.
    """
    with get_session() as session:
        q = session.query(FreelanceLead).filter(
            FreelanceLead.evaluated.is_(False),
            FreelanceLead.stage == FreelanceStage.DISCOVERED,
        )
        if user_id is not None:
            q = q.filter(FreelanceLead.user_id == user_id)
        pending = q.order_by(FreelanceLead.discovered_at.desc()).limit(limit).all()
        lead_ids = [lead.id for lead in pending]

    results = {"total": len(lead_ids), "evaluated": 0, "spam": 0, "errors": 0, "quota_hit": False}

    for lead_id in lead_ids:
        status = evaluate_lead(lead_id)
        if status in ("evaluated", "low_score"):
            results["evaluated"] += 1
        elif status == "spam":
            results["spam"] += 1
        elif status == "quota_exceeded":
            results["quota_hit"] = True
            break
        elif status in ("error", "ai_unavailable"):
            results["errors"] += 1

    logger.info("Freelance evaluation complete: %s", results)
    return results
