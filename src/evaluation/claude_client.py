"""Hybrid job evaluation client with AI provider orchestration and smart heuristic fallback."""
from __future__ import annotations

import json
import logging
from typing import Any, Optional

from src.ai.service import AIProviderUnavailable, AIQuotaExceeded, AIService
from src.candidate.profile_manager import get_active_profile
from src.evaluation.heuristic_scorer import evaluate_heuristic
from src.evaluation.prompts import build_system_prompt, build_user_prompt

logger = logging.getLogger(__name__)


class EvaluationParseError(Exception):
    pass


REQUIRED_KEYS = {
    "match_score", "visa_sponsorship_detected", "visa_status_notes",
    "key_skills_matched", "recruiter_pitch_fr", "recruiter_pitch_en",
    "application_subject", "application_email_en",
}


def _extract_json(text: str) -> dict[str, Any]:
    cleaned = (text or "").strip()
    if cleaned.startswith("```"):
        cleaned = cleaned.strip("`").strip()
        if cleaned.lower().startswith("json"):
            cleaned = cleaned[4:].strip()
    if not cleaned.startswith("{"):
        start, end = cleaned.find("{"), cleaned.rfind("}")
        if start >= 0 and end > start:
            cleaned = cleaned[start:end + 1]
    try:
        data = json.loads(cleaned)
    except json.JSONDecodeError as exc:
        raise EvaluationParseError(f"Invalid JSON from AI model: {exc}") from exc
    if not isinstance(data, dict):
        raise EvaluationParseError("AI model returned non-object JSON")

    # Unwrap if response is wrapped under a single key (e.g., "evaluation", "result", "job")
    if len(data) == 1 and not (REQUIRED_KEYS & data.keys()):
        val = next(iter(data.values()))
        if isinstance(val, dict):
            data = val

    # Normalize common field aliases
    if "match_score" not in data and "score" in data:
        data["match_score"] = data["score"]
    if "match_score" not in data and "matchScore" in data:
        data["match_score"] = data["matchScore"]

    # Provide safe defaults for secondary fields so that downstream heuristic
    # backfilling (lines 97-106) can sanitize/fill them without failing the whole AI call
    data.setdefault("visa_sponsorship_detected", False)
    data.setdefault("visa_status_notes", "")
    data.setdefault("key_skills_matched", [])
    data.setdefault("recruiter_pitch_fr", "")
    data.setdefault("recruiter_pitch_en", "")
    data.setdefault("application_subject", "")
    data.setdefault("application_email_en", "")

    missing = REQUIRED_KEYS - data.keys()
    if missing:
        raise EvaluationParseError(f"Missing keys in response: {sorted(missing)}")
    return data


def evaluate_job(job: dict[str, Any], *, profile: dict[str, Any] | None = None, session=None) -> Optional[dict[str, Any]]:
    """Evaluate a job using configured AI models with seamless offline heuristic fallback."""
    if profile is None:
        uid = job.get("user_id") if isinstance(job, dict) else getattr(job, "user_id", None)
        profile = get_active_profile(user_id=uid, session=session)

    result: dict[str, Any] | None = None
    evaluation_method = "ai"

    try:
        system_prompt = build_system_prompt(profile)
        user_prompt = build_user_prompt(job)
        result = AIService(session=session).generate(
            system_prompt=system_prompt,
            user_prompt=user_prompt,
            max_tokens=750,
            parse=_extract_json,
        )
    except AIQuotaExceeded:
        raise
    except (AIProviderUnavailable, Exception) as exc:
        logger.info("AI provider unavailable (%s); falling back to smart heuristic evaluation", exc)
        result = evaluate_heuristic(job, profile)
        evaluation_method = "heuristic"

    if result is None:
        result = evaluate_heuristic(job, profile)
        evaluation_method = "heuristic"

    try:
        result["match_score"] = max(0, min(100, int(result["match_score"])))
    except (TypeError, ValueError) as exc:
        raise EvaluationParseError("match_score must be an integer") from exc

    result["visa_sponsorship_detected"] = bool(result["visa_sponsorship_detected"])
    if not isinstance(result["key_skills_matched"], list):
        result["key_skills_matched"] = [str(result["key_skills_matched"])]

    result["visa_status_notes"] = str(result.get("visa_status_notes") or "").strip()

    from src.outreach.safety import is_inverted_perspective, has_unresolved_placeholders
    from src.application.auto_apply import sanitize_application_text, _clean_sender_name

    sender = _clean_sender_name()
    company = str(job.get("company") or "")
    title = str(job.get("title") or "")
    heuristic_res = None

    for key in ("recruiter_pitch_fr", "recruiter_pitch_en", "application_subject", "application_email_en", "application_subject_fr", "application_email_fr"):
        val = str(result.get(key) or "").strip()
        is_fr = "fr" in key
        if val:
            val = sanitize_application_text(val, sender, company, title, is_french=is_fr)
        if not val or is_inverted_perspective(val) or has_unresolved_placeholders(val) or len(val) < 10:
            if heuristic_res is None:
                heuristic_res = evaluate_heuristic(job, profile)
            val = str(heuristic_res.get(key) or "").strip()
        result[key] = val

    # Normalize precision requirement and continent fields
    from src.evaluation.heuristic_scorer import (
        extract_continent,
        extract_degree_requirement,
        extract_experience_requirement,
        extract_visa_relocation,
    )

    desc_text = str(job.get("raw_description") or job.get("description") or "")
    if not result.get("continent"):
        result["continent"] = extract_continent(job.get("location") or "", is_remote=job.get("is_remote", False), full_text=desc_text)

    if not result.get("experience_level") or result.get("experience_level") == "not_specified":
        exp_info = extract_experience_requirement(desc_text, job.get("title") or "")
        result["experience_level"] = exp_info["experience_level"]
        result["experience_years_required"] = exp_info["experience_years_required"]

    if not result.get("degree_required") or result.get("degree_required") == "not_specified":
        deg_info = extract_degree_requirement(desc_text)
        result["degree_required"] = deg_info["degree_required"]

    if "degree_matched" not in result:
        cand_deg = (profile.get("degree_level") or "bachelor").lower()
        DEG_RANKS = {"none": 0, "not_specified": 0, "bachelor": 1, "master": 2, "phd": 3}
        result["degree_matched"] = DEG_RANKS.get(cand_deg, 1) >= DEG_RANKS.get(result["degree_required"], 0)

    if not result.get("visa_category") or result.get("visa_category") == "unspecified":
        vr_info = extract_visa_relocation(desc_text, is_remote=job.get("is_remote", False))
        result["visa_category"] = vr_info["visa_category"]
        result["relocation_detected"] = vr_info["relocation_detected"]
    else:
        result["relocation_detected"] = bool(result.get("relocation_detected", False))

    result["evaluation_method"] = evaluation_method
    return result
