"""Dynamic candidate profile manager and search matrix generator.

Allows users to configure keywords, skills, target titles, locations, and custom resumes
per profile, freeing the system from hardcoded static files.
"""
from __future__ import annotations

import json
import logging
import os
import re
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from sqlalchemy import desc, select

from config.settings import settings
from src.storage.db import get_session
from src.storage.models import CandidateProfile

logger = logging.getLogger(__name__)

# Fallback default values if database has not yet been initialized
DEFAULT_STATIC_PROFILE = {
    "id": 0,
    "name": "Hamza Oukhouya",
    "is_active": False,
    "headline": "Fullstack Software Engineer",
    "current_location": "Morocco",
    "target_locations": ["Remote", "France", "Europe"],
    "target_titles": ["Software Engineer", "Backend Engineer", "Java Developer"],
    "core_stack": ["Java", "Spring Boot", "React", "Docker", "PostgreSQL"],
    "keywords": ["Microservices", "REST API", "CI/CD"],
    "negative_keywords": [],
    "experience_years": 3,
    "visa_requirement": "Remote EU/Global",
    "languages": {"French": "Fluent", "English": "Fluent"},
    "resume_text": "Experienced software engineer specializing in Java, Spring Boot microservices.",
    "resume_path": "",
    "freelance_services": ["Backend Engineering", "Fullstack Development"],
    "freelance_hourly_usd": 50,
    "freelance_daily_eur": 400,
    "freelance_currency": "EUR",
    "calendar_url": "https://cal.com/hamza-oukhouya",
    "degree_level": "bachelor",
    "target_experience_level": "entry",
}



def _safe_json_loads(val: Any, default: Any = None) -> Any:
    if val is None:
        return default
    if isinstance(val, (list, dict)):
        return val
    try:
        return json.loads(val)
    except Exception:
        return default


def _profile_to_dict(p: CandidateProfile) -> dict[str, Any]:
    return {
        "id": p.id,
        "user_id": p.user_id,
        "name": p.name,
        "is_active": bool(p.is_active),
        "headline": p.headline or "",
        "current_location": p.current_location or "",
        "target_locations": _safe_json_loads(p.target_locations, []),
        "target_titles": _safe_json_loads(p.target_titles, []),
        "core_stack": _safe_json_loads(p.core_stack, []),
        "keywords": _safe_json_loads(p.keywords, []),
        "negative_keywords": _safe_json_loads(p.negative_keywords, []),
        "experience_years": p.experience_years or 0,
        "visa_requirement": p.visa_requirement or "",
        "languages": _safe_json_loads(p.languages, {}),
        "resume_text": p.resume_text or "",
        "resume_path": p.resume_path or "",
        "freelance_services": _safe_json_loads(p.freelance_services, []),
        "freelance_hourly_usd": p.freelance_hourly_usd or 50,
        "freelance_daily_eur": p.freelance_daily_eur or 400,
        "freelance_hourly_rate": p.freelance_hourly_usd or 50,
        "freelance_daily_rate": p.freelance_daily_eur or 400,
        "freelance_currency": p.freelance_currency or "EUR",
        "calendar_url": getattr(p, "calendar_url", None) or getattr(settings, "calendar_booking_url", "") or "",
        "degree_level": getattr(p, "degree_level", None) or "bachelor",
        "target_experience_level": getattr(p, "target_experience_level", None) or "entry",
        "created_at": p.created_at.isoformat() if p.created_at else None,
        "updated_at": p.updated_at.isoformat() if p.updated_at else None,
    }


def _normalize_args(user_id, session):
    if session is None and user_id is not None and not isinstance(user_id, int):
        return None, user_id
    return user_id, session


def get_active_profile(user_id: int | None = None, session=None) -> dict[str, Any]:
    """Retrieve the currently active candidate profile from DB for a user, or return default."""
    user_id, session = _normalize_args(user_id, session)

    def _fetch(s):
        stmt = select(CandidateProfile).where(CandidateProfile.is_active.is_(True))
        if user_id is not None:
            stmt = stmt.where(CandidateProfile.user_id == user_id)
        row = s.scalar(stmt.limit(1))
        if not row and user_id is not None:
            # Fallback to any profile belonging to this user
            row = s.scalar(select(CandidateProfile).where(CandidateProfile.user_id == user_id).order_by(desc(CandidateProfile.id)).limit(1))
        if not row and user_id is None:
            # Global fallback when no specific user is provided
            row = s.scalar(select(CandidateProfile).order_by(desc(CandidateProfile.is_active), desc(CandidateProfile.id)).limit(1))
        if row:
            return _profile_to_dict(row)
        fallback = dict(DEFAULT_STATIC_PROFILE)
        if user_id is not None:
            try:
                from src.storage.models import User
                u = s.get(User, user_id)
                if u:
                    fallback["name"] = u.full_name or u.email.split("@")[0]
            except Exception:
                pass
        return fallback

    try:
        if session is not None:
            return _fetch(session)
        with get_session() as s:
            return _fetch(s)
    except Exception as exc:
        logger.debug("Active profile fallback: %s", exc)
        return dict(DEFAULT_STATIC_PROFILE)


def list_profiles(user_id: int | None = None, session=None) -> list[dict[str, Any]]:
    """List all candidate profiles, optionally filtered by user_id."""
    user_id, session = _normalize_args(user_id, session)

    def _fetch(s):
        stmt = select(CandidateProfile)
        if user_id is not None:
            stmt = stmt.where(CandidateProfile.user_id == user_id)
        rows = s.scalars(stmt.order_by(desc(CandidateProfile.is_active), CandidateProfile.name)).all()
        return [_profile_to_dict(r) for r in rows]

    try:
        if session is not None:
            return _fetch(session)
        with get_session() as s:
            return _fetch(s)
    except Exception as exc:
        logger.error("Failed to list candidate profiles: %s", exc)
        return []


def _is_authorized(s, user_id: int | None, row_user_id: int | None) -> bool:
    if user_id is None:
        return True
    if row_user_id == user_id:
        return True
    from src.storage.models import User
    caller = s.get(User, user_id)
    return bool(caller and caller.role == "admin")


def get_profile_by_id(profile_id: int, user_id: int | None = None, session=None) -> dict[str, Any] | None:
    user_id, session = _normalize_args(user_id, session)

    def _fetch(s):
        row = s.get(CandidateProfile, profile_id)
        if not row:
            return None
        if not _is_authorized(s, user_id, row.user_id):
            return None
        return _profile_to_dict(row)

    if session is not None:
        return _fetch(session)
    with get_session() as s:
        return _fetch(s)


def create_profile(data: dict[str, Any], user_id: int | None = None, session=None) -> dict[str, Any]:
    """Create a new candidate profile scoped to a user."""
    def _create(s):
        name = (data.get("name") or "").strip()
        if not name:
            raise ValueError("Profile name is required")

        uid = user_id or data.get("user_id")

        # Check unique per user
        stmt = select(CandidateProfile).where(CandidateProfile.name == name)
        if uid is not None:
            stmt = stmt.where(CandidateProfile.user_id == uid)
        existing = s.scalar(stmt)
        if existing:
            raise ValueError(f"A profile named '{name}' already exists")

        is_active = bool(data.get("is_active", False))
        if is_active:
            # deactivate other profiles belonging to THIS user only
            if uid is not None:
                s.query(CandidateProfile).filter(CandidateProfile.user_id == uid).update({"is_active": False})
            else:
                s.query(CandidateProfile).update({"is_active": False})

        profile = CandidateProfile(
            name=name,
            user_id=uid,
            is_active=is_active,
            headline=(data.get("headline") or "").strip(),
            current_location=(data.get("current_location") or "").strip(),
            target_locations=json.dumps(data.get("target_locations") or [], ensure_ascii=False),
            target_titles=json.dumps(data.get("target_titles") or [], ensure_ascii=False),
            core_stack=json.dumps(data.get("core_stack") or [], ensure_ascii=False),
            keywords=json.dumps(data.get("keywords") or [], ensure_ascii=False),
            negative_keywords=json.dumps(data.get("negative_keywords") or [], ensure_ascii=False),
            experience_years=int(data.get("experience_years") or 0),
            visa_requirement=(data.get("visa_requirement") or "").strip(),
            languages=json.dumps(data.get("languages") or {}, ensure_ascii=False),
            resume_text=(data.get("resume_text") or "").strip(),
            resume_path=(data.get("resume_path") or "").strip(),
            freelance_services=json.dumps(data.get("freelance_services") or [], ensure_ascii=False),
            freelance_hourly_usd=int(data.get("freelance_hourly_usd") or 50),
            freelance_daily_eur=int(data.get("freelance_daily_eur") or 400),
            freelance_currency=(data.get("freelance_currency") or "EUR").strip(),
            calendar_url=(data.get("calendar_url") or "").strip(),
            degree_level=(data.get("degree_level") or "bachelor").strip(),
            target_experience_level=(data.get("target_experience_level") or "entry").strip(),
            created_at=datetime.now(timezone.utc),
            updated_at=datetime.now(timezone.utc),
        )
        s.add(profile)
        s.flush()
        return _profile_to_dict(profile)

    if session is not None:
        return _create(session)
    with get_session() as s:
        return _create(s)


def update_profile(profile_id: int, data: dict[str, Any], user_id: int | None = None, session=None) -> dict[str, Any]:
    """Update an existing candidate profile."""
    def _update(s):
        row = s.get(CandidateProfile, profile_id)
        if not row:
            raise ValueError(f"Profile {profile_id} not found")
        if user_id is not None and row.user_id is not None and row.user_id != user_id:
            raise ValueError("Unauthorized to update this profile")

        uid = user_id or row.user_id

        if "name" in data and data["name"]:
            new_name = data["name"].strip()
            if new_name != row.name:
                stmt = select(CandidateProfile).where(CandidateProfile.name == new_name)
                if uid is not None:
                    stmt = stmt.where(CandidateProfile.user_id == uid)
                existing = s.scalar(stmt)
                if existing:
                    raise ValueError(f"A profile named '{new_name}' already exists")
                row.name = new_name
        if "headline" in data:
            row.headline = (data["headline"] or "").strip()
        if "current_location" in data:
            row.current_location = (data["current_location"] or "").strip()
        if "target_locations" in data:
            row.target_locations = json.dumps(data["target_locations"], ensure_ascii=False)
        if "target_titles" in data:
            row.target_titles = json.dumps(data["target_titles"], ensure_ascii=False)
        if "core_stack" in data:
            row.core_stack = json.dumps(data["core_stack"], ensure_ascii=False)
        if "keywords" in data:
            row.keywords = json.dumps(data["keywords"], ensure_ascii=False)
        if "negative_keywords" in data:
            row.negative_keywords = json.dumps(data["negative_keywords"], ensure_ascii=False)
        if "experience_years" in data:
            row.experience_years = int(data["experience_years"] or 0)
        if "visa_requirement" in data:
            row.visa_requirement = (data["visa_requirement"] or "").strip()
        if "languages" in data:
            row.languages = json.dumps(data["languages"], ensure_ascii=False)
        if "resume_text" in data:
            row.resume_text = (data["resume_text"] or "").strip()
        if "resume_path" in data:
            row.resume_path = (data["resume_path"] or "").strip()
        if "freelance_services" in data:
            row.freelance_services = json.dumps(data["freelance_services"], ensure_ascii=False)
        if "freelance_hourly_usd" in data:
            row.freelance_hourly_usd = int(data["freelance_hourly_usd"] or 50)
        if "freelance_daily_eur" in data:
            row.freelance_daily_eur = int(data["freelance_daily_eur"] or 400)
        if "freelance_currency" in data:
            row.freelance_currency = (data["freelance_currency"] or "EUR").strip()
        if "calendar_url" in data:
            row.calendar_url = (data["calendar_url"] or "").strip()
        if "degree_level" in data:
            row.degree_level = (data["degree_level"] or "bachelor").strip()
        if "target_experience_level" in data:
            row.target_experience_level = (data["target_experience_level"] or "entry").strip()

        if data.get("is_active"):
            if uid is not None:
                s.query(CandidateProfile).filter(CandidateProfile.user_id == uid, CandidateProfile.id != profile_id).update({"is_active": False})
            else:
                s.query(CandidateProfile).filter(CandidateProfile.id != profile_id).update({"is_active": False})
            row.is_active = True

        row.updated_at = datetime.now(timezone.utc)
        s.flush()
        return _profile_to_dict(row)

    if session is not None:
        return _update(session)
    with get_session() as s:
        return _update(s)


def set_active_profile(profile_id: int, user_id: int | None = None, session=None) -> dict[str, Any]:
    """Switch active candidate profile for a user."""
    def _activate(s):
        row = s.get(CandidateProfile, profile_id)
        if not row:
            raise ValueError(f"Profile {profile_id} not found")
        if user_id is not None and row.user_id is not None and row.user_id != user_id:
            raise ValueError("Unauthorized to activate this profile")
        uid = user_id or row.user_id
        if uid is not None:
            s.query(CandidateProfile).filter(CandidateProfile.user_id == uid).update({"is_active": False})
        else:
            s.query(CandidateProfile).update({"is_active": False})
        row.is_active = True
        row.updated_at = datetime.now(timezone.utc)
        s.flush()
        prof_dict = _profile_to_dict(row)

        # Re-score unapplied jobs against the newly activated candidate profile
        try:
            from src.evaluation.heuristic_scorer import evaluate_heuristic
            from src.evaluation.language import is_french_job
            from src.storage.models import JobPosting, PipelineStage

            q_unapplied = select(JobPosting).where(JobPosting.applied_at.is_(None))
            if uid is not None:
                q_unapplied = q_unapplied.where(JobPosting.user_id == uid)
            unapplied_jobs = s.scalars(q_unapplied).all()
            for job in unapplied_jobs:
                eval_res = evaluate_heuristic(job, profile=prof_dict)
                score = eval_res.get("match_score", 0)
                job.match_score = score
                is_fr = is_french_job(job)
                job.recruiter_pitch_en = eval_res.get("recruiter_pitch_en") or job.recruiter_pitch_en
                job.recruiter_pitch_fr = eval_res.get("recruiter_pitch_fr") or job.recruiter_pitch_fr
                job.application_subject = (eval_res.get("application_subject_fr") if is_fr else eval_res.get("application_subject")) or job.application_subject
                job.application_email_body = (eval_res.get("application_email_fr") if is_fr else eval_res.get("application_email_en")) or job.application_email_body
                clears = score >= settings.min_match_score
                job.pipeline_stage = PipelineStage.EVALUATED_MATCH if clears else PipelineStage.EVALUATED_LOW
            s.flush()
            logger.info("Re-scored %d unapplied jobs against active profile '%s'", len(unapplied_jobs), prof_dict.get("headline") or prof_dict.get("name"))
        except Exception as resc_err:
            logger.warning("Could not re-score unapplied jobs on profile activation: %s", resc_err)

        return prof_dict

    if session is not None:
        return _activate(session)
    with get_session() as s:
        return _activate(s)


def delete_profile(profile_id: int, user_id: int | None = None, session=None) -> bool:
    """Delete profile (if not the only one for this user)."""
    def _delete(s):
        row = s.get(CandidateProfile, profile_id)
        if not row:
            return False
        if user_id is not None and row.user_id is not None and row.user_id != user_id:
            raise ValueError("Unauthorized to delete this profile")
        uid = user_id or row.user_id
        
        q = s.query(CandidateProfile)
        if uid is not None:
            q = q.filter(CandidateProfile.user_id == uid)
        count = q.count()
        if count <= 1:
            raise ValueError("Cannot delete the only candidate profile")
            
        was_active = row.is_active
        s.delete(row)
        s.flush()
        if was_active:
            # activate the next available profile for this user
            fb_stmt = select(CandidateProfile)
            if uid is not None:
                fb_stmt = fb_stmt.where(CandidateProfile.user_id == uid)
            fallback = s.scalar(fb_stmt.order_by(desc(CandidateProfile.id)).limit(1))
            if fallback:
                fallback.is_active = True
                s.flush()
        return True

    if session is not None:
        return _delete(session)
    with get_session() as s:
        return _delete(s)


# Known country code map for Indeed / JobSpy
COUNTRY_CODE_MAP = {
    "france": ("France", "fr"),
    "germany": ("Germany", "de"),
    "netherlands": ("Netherlands", "nl"),
    "belgium": ("Belgium", "be"),
    "portugal": ("Portugal", "pt"),
    "morocco": ("Morocco", "ma"),
    "united kingdom": ("United Kingdom", "uk"),
    "uk": ("United Kingdom", "uk"),
    "united states": ("USA", "us"),
    "usa": ("USA", "us"),
    "us": ("USA", "us"),
    "spain": ("Spain", "es"),
    "switzerland": ("Switzerland", "ch"),
    "canada": ("Canada", "ca"),
    "australia": ("Australia", "au"),
    "ireland": ("Ireland", "ie"),
    "italy": ("Italy", "it"),
    "uae": ("United Arab Emirates", "ae"),
    "dubai": ("United Arab Emirates", "ae"),
    "saudi arabia": ("Saudi Arabia", "sa"),
    "poland": ("Poland", "pl"),
    "latvia": ("Latvia", "lv"),
    "czech republic": ("Czech Republic", "cz"),
    "czechia": ("Czech Republic", "cz"),
    "japan": ("Japan", "jp"),
    "norway": ("Norway", "no"),
    "hungary": ("Hungary", "hu"),
    "luxembourg": ("Luxembourg", "lu"),
    "sweden": ("Sweden", "se"),
    "denmark": ("Denmark", "dk"),
    "finland": ("Finland", "fi"),
    "austria": ("Austria", "at"),
    "singapore": ("Singapore", "sg"),
}


def generate_search_matrix(profile: dict[str, Any] | None = None) -> list[dict[str, Any]]:
    """Dynamically construct the JobSpy discovery query matrix for the given profile.
    
    Uses profile's target titles, target locations, and core keywords.
    """
    if profile is None:
        profile = get_active_profile()

    titles = profile.get("target_titles") or []
    locations = profile.get("target_locations") or []
    if not titles or not locations:
        return []
    core_stack = profile.get("core_stack") or []
    primary_skill = core_stack[0] if core_stack else "Software"

    matrix: list[dict[str, Any]] = []

    # Country-specific queries
    country_targets = []
    has_remote = False

    for loc in locations:
        loc_clean = str(loc).strip().lower()
        if "remote" in loc_clean or "anywhere" in loc_clean or "global" in loc_clean:
            has_remote = True
            continue
        if loc_clean in COUNTRY_CODE_MAP:
            display_name, code = COUNTRY_CODE_MAP[loc_clean]
            country_targets.append((display_name, code))
        else:
            # Custom country / city
            country_targets.append((str(loc).strip(), str(loc).strip()[:3].lower()))

    # Limit titles per country to keep matrix focused and fast
    top_titles = titles[:3]

    for title in top_titles:
        for display_name, code in country_targets:
            slug_title = title.lower().replace(" ", "-")[:18]
            matrix.append({
                "id": f"{code}-{slug_title}",
                "term": f"{title}",
                "google": f"{title} jobs {display_name}",
                "location": display_name,
                "country": display_name,
                "is_remote": False,
            })

    # Remote queries
    if has_remote or not matrix:
        remote_titles = titles[:4]
        for idx, title in enumerate(remote_titles):
            term = f"{title} remote"
            matrix.append({
                "id": f"remote-{idx}-{title.lower().replace(' ', '-')[:15]}",
                "term": term,
                "google": f"{term} jobs",
                "location": "",
                "country": None,
                "is_remote": True,
                "sites": ["linkedin", "google", "bayt"],
            })

    return matrix


def generate_freelance_queries(profile: dict[str, Any] | None = None) -> list[dict[str, Any]]:
    """Dynamically construct freelance client acquisition search queries for the active profile."""
    if profile is None:
        profile = get_active_profile()

    if not profile or not profile.get("core_stack"):
        return []

    stack = profile.get("core_stack") or []
    skills_term = " ".join(str(s).lower() for s in stack[:3])

    return [
        {"id": "hn-freelance", "source": "hackernews", "term": f"freelancer seeking developer {skills_term}"},
        {"id": "hn-mvp", "source": "hackernews", "term": "looking for developer MVP technical cofounder"},
        {"id": "reddit-forhire", "source": "reddit", "term": f"[Hiring] developer {skills_term}"},
        {"id": "reddit-freelance", "source": "reddit", "term": "[Hiring] freelance backend frontend web developer"},
        {"id": "google-freelance", "source": "google", "term": f"looking for freelance {skills_term} developer"},
        {"id": "google-mvp", "source": "google", "term": "looking for developer to build MVP web app"},
        {"id": "remote-contract", "source": "remoteok", "term": f"contract freelance {skills_term}"},
        {"id": "jobicy-contract", "source": "jobicy", "term": "contract freelance developer"},
        {"id": "wwr-contract", "source": "weworkremotely", "term": "contract freelance"},
    ]


# ---------------------------------------------------------------------------
# Multi-User CV Resolution, Dynamic Generation & Upload Management
# ---------------------------------------------------------------------------

def resolve_user_resume_path(
    user_id: int | None = None,
    profile: dict[str, Any] | None = None,
    auto_generate: bool = True,
    session=None
) -> Path | None:
    """Resolve the specific resume PDF file for a given user or profile.
    
    Ensures that every registered user utilizes their own dashboard CV,
    dynamically generating an ATS-ready PDF from their profile if none exists.
    """
    if profile is None:
        profile = get_active_profile(user_id=user_id, session=session)

    # 1. Check if profile specifies a custom resume path that exists
    if profile and profile.get("resume_path"):
        cand_p = Path(profile["resume_path"])
        if cand_p.is_file():
            return cand_p

    effective_uid = user_id or (profile.get("user_id") if profile else None)

    # 2. Check if user has an existing uploaded or generated resume file on disk
    if effective_uid:
        user_dir = Path("uploads") / "resumes" / f"user_{effective_uid}"
        if user_dir.is_dir():
            pdfs = sorted(user_dir.glob("*.pdf"), key=os.path.getmtime, reverse=True)
            if pdfs and pdfs[0].is_file():
                # Self-heal profile.resume_path if it was lost
                try:
                    if profile and profile.get("id"):
                        update_profile(profile["id"], {"resume_path": str(pdfs[0].resolve())}, user_id=effective_uid, session=session)
                except Exception:
                    pass
                return pdfs[0]

    # 3. Dynamically generate standard PDF CV from the candidate's dashboard profile
    if auto_generate and effective_uid and profile:
        try:
            from src.candidate.cv_generator import save_user_resume_pdf
            gen_path = save_user_resume_pdf(effective_uid, profile)
            if profile.get("id"):
                update_profile(profile["id"], {"resume_path": gen_path}, user_id=effective_uid, session=session)
            return Path(gen_path)
        except Exception as gen_err:
            logger.warning("Failed to auto-generate resume PDF for user %s: %s", effective_uid, gen_err)

    # 4. Strict multi-user boundary: Only fallback to static file if user is admin or unspecified
    is_admin = False
    if effective_uid:
        try:
            from src.storage.models import User
            def _check_admin(s):
                u = s.get(User, effective_uid)
                return bool(u and u.role == "admin")
            if session:
                is_admin = _check_admin(session)
            else:
                with get_session() as s:
                    is_admin = _check_admin(s)
        except Exception:
            pass

    if effective_uid is None or is_admin:
        fallback = Path(settings.candidate_resume_path)
        if fallback.is_file():
            return fallback
        alt_fallback = Path("candidate_data/resume.pdf")
        if alt_fallback.is_file():
            return alt_fallback

    return None


def generate_and_save_profile_cv(
    profile_id: int | None = None,
    user_id: int | None = None,
    session=None
) -> dict[str, Any]:
    """Generate a clean ATS PDF resume from profile data and store it for the user."""
    from src.candidate.cv_generator import save_user_resume_pdf
    from src.candidate.cv_ml_engine import extract_text_from_pdf

    def _generate(s):
        target_prof: CandidateProfile | None = None
        if profile_id:
            target_prof = s.get(CandidateProfile, profile_id)
        if not target_prof and user_id:
            target_prof = s.scalar(
                select(CandidateProfile)
                .where(CandidateProfile.user_id == user_id, CandidateProfile.is_active.is_(True))
                .order_by(desc(CandidateProfile.id))
                .limit(1)
            ) or s.scalar(
                select(CandidateProfile)
                .where(CandidateProfile.user_id == user_id)
                .order_by(desc(CandidateProfile.id))
                .limit(1)
            )

        if not target_prof:
            raise ValueError("No candidate profile found to generate CV for")

        uid = user_id or target_prof.user_id or 1
        prof_data = _profile_to_dict(target_prof)

        # Get user email
        user_email = None
        try:
            from src.storage.models import User
            u = s.get(User, uid)
            if u:
                user_email = u.email
        except Exception:
            pass

        saved_path = save_user_resume_pdf(uid, prof_data, user_email=user_email)
        target_prof.resume_path = saved_path

        # If resume_text was empty, extract text from the generated PDF
        if not target_prof.resume_text or len(target_prof.resume_text.strip()) < 20:
            extracted_txt = extract_text_from_pdf(saved_path)
            if extracted_txt:
                target_prof.resume_text = extracted_txt

        target_prof.updated_at = datetime.now(timezone.utc)
        s.flush()

        return {
            "ok": True,
            "resume_path": saved_path,
            "filename": Path(saved_path).name,
            "profile": _profile_to_dict(target_prof),
        }

    if session is not None:
        return _generate(session)
    with get_session() as s:
        return _generate(s)


def save_user_uploaded_cv(
    user_id: int,
    file_bytes: bytes,
    original_filename: str,
    session=None
) -> dict[str, Any]:
    """Save user-uploaded CV file, extract readable text and features, and update active profile."""
    from src.candidate.cv_ml_engine import extract_text_from_pdf, analyze_cv_content

    if not file_bytes:
        raise ValueError("Uploaded file is empty")

    upload_dir = Path("uploads") / "resumes" / f"user_{user_id}"
    upload_dir.mkdir(parents=True, exist_ok=True)

    safe_name = re.sub(r'[^a-zA-Z0-9_.-]', '_', original_filename or "resume.pdf")
    if not safe_name.lower().endswith(".pdf"):
        safe_name = f"{Path(safe_name).stem}.pdf"
    file_path = upload_dir / safe_name
    file_path.write_bytes(file_bytes)

    # Extract text from uploaded document
    extracted_text = extract_text_from_pdf(file_path)
    if not extracted_text:
        # Try raw text decoding if it was plain text or markdown
        try:
            extracted_text = file_bytes.decode("utf-8", errors="ignore").strip()
        except Exception:
            pass

    analysis = analyze_cv_content(extracted_text) if extracted_text else {}

    def _persist(s):
        prof = s.scalar(
            select(CandidateProfile)
            .where(CandidateProfile.user_id == user_id, CandidateProfile.is_active.is_(True))
            .limit(1)
        ) or s.scalar(
            select(CandidateProfile)
            .where(CandidateProfile.user_id == user_id)
            .order_by(desc(CandidateProfile.id))
            .limit(1)
        )

        if not prof:
            # Create a brand new active profile for this user
            from src.storage.models import User
            u = s.get(User, user_id)
            full_name = (analysis.get("name") or (u.full_name if u else None) or "Candidate").strip()
            prof = CandidateProfile(
                user_id=user_id,
                name=full_name,
                is_active=True,
                resume_path=str(file_path.resolve()),
                resume_text=extracted_text,
                current_location=analysis.get("location") or "",
                headline=f"Software Engineer ({', '.join(analysis.get('top_skills', [])[:3])})" if analysis.get("top_skills") else "Software Engineer",
                experience_years=analysis.get("experience_years", 3),
                core_stack=json.dumps(analysis.get("top_skills", []), ensure_ascii=False),
                keywords=json.dumps(analysis.get("keywords", []), ensure_ascii=False),
            )
            s.add(prof)
        else:
            prof.resume_path = str(file_path.resolve())
            if extracted_text:
                prof.resume_text = extracted_text
            if analysis.get("name") and (not prof.name or prof.name in ("Candidate", "My Profile")):
                prof.name = analysis["name"]
            if analysis.get("top_skills") and (not prof.core_stack or prof.core_stack in ("[]", "")):
                prof.core_stack = json.dumps(analysis["top_skills"], ensure_ascii=False)
            if analysis.get("location") and not prof.current_location:
                prof.current_location = analysis["location"]
            if analysis.get("experience_years") and not prof.experience_years:
                prof.experience_years = analysis["experience_years"]
            prof.updated_at = datetime.now(timezone.utc)

        s.flush()
        return {
            "ok": True,
            "message": "CV uploaded and processed successfully",
            "resume_path": str(file_path.resolve()),
            "filename": safe_name,
            "text_length": len(extracted_text),
            "analysis": analysis,
            "profile": _profile_to_dict(prof),
        }

    if session is not None:
        return _persist(session)
    with get_session() as s:
        return _persist(s)

