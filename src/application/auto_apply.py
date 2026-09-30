"""Professional application router: explicit recruiter email first, public ATS second."""
from __future__ import annotations

import json
import logging
import re
import smtplib
from email.message import EmailMessage
from email.utils import make_msgid
from pathlib import Path
from urllib.parse import urljoin, urlparse

import requests

from config.settings import settings
from src.application.web_apply import apply_via_browser, detect_ats, is_board_url
from src.evaluation.language import is_french_job
from src.outreach.safety import (
    clean_email,
    has_unresolved_placeholders,
    is_inverted_perspective,
    normalize_email,
    validate_external_email,
    validate_message_content,
)
from src.storage.outbound import claim_outbound, mark_failed, mark_sent, mark_unknown
from src.storage.quota import reserve_outbound_send

logger = logging.getLogger(__name__)
EMAIL_RE = re.compile(r"\b[a-zA-Z0-9][a-zA-Z0-9._%+-]*@[a-zA-Z0-9.-]+\.[a-zA-Z]{2,}\b")
GENERIC_LOCAL_SCORES = {
    "recruiter": 45, "recruiting": 45, "talent": 40, "hiring": 38,
    "hr": 32, "jobs": 30, "careers": 28, "apply": 34, "applications": 34,
    "people": 18, "contact": 8,
}
GENERIC_BAD = {
    "noreply", "no-reply", "donotreply", "do-not-reply", "notifications",
    "mailer-daemon", "daemon", "postmaster", "abuse", "privacy", "data.privacy",
    "dataprivacy", "dpo", "gdpr", "legal", "security", "accommodation",
    "_accommodation", "accessibility", "compliance", "copyright", "dmca",
}
PERSONAL_DOMAINS = {"gmail.com", "googlemail.com", "outlook.com", "hotmail.com", "live.com", "yahoo.com", "icloud.com", "proton.me", "protonmail.com", "gmx.com"}


def _clean_sender_name(profile: dict | None = None) -> str:
    name = (profile.get("name") if profile else None) or (settings.sender_display_name or "").strip()
    if not name:
        try:
            from src.candidate.profile_manager import get_active_profile
            p = get_active_profile()
            name = (p.get("name") or "").strip()
        except Exception:
            name = ""
    if " - " in name:
        name = name.split(" - ")[0].strip()
    elif " — " in name:
        name = name.split(" — ")[0].strip()
    return name or getattr(settings, "sender_display_name", "") or "Applicant"



def sanitize_application_text(text: str, sender: str, company: str, title: str, is_french: bool) -> str:
    if not text:
        return ""
    result = text
    recruiter_greeting = "l'équipe recrutement" if is_french else "Hiring Team"

    # 1. Replace greeting placeholders like "Bonjour [Nom]," or "Dear [Name],"
    result = re.sub(r"(Bonjour|Cher|Chère)\s+\[(?:Nom|Nom du recruteur|Prénom|Recruiter Name|Recruiter)\]", rf"\1 {recruiter_greeting}", result, flags=re.I)
    result = re.sub(r"(Dear|Hello|Hi)\s+\[(?:Name|Recruiter Name|Hiring Manager|Recruiter)\]", rf"\1 {recruiter_greeting}", result, flags=re.I)
    result = re.sub(r"\[(?:Nom|Nom du recruteur|Prénom|Recruiter Name|Recruiter|Hiring Manager)\]", recruiter_greeting, result, flags=re.I)

    # 2. Replace company placeholders
    comp_sub = company or ("l'entreprise" if is_french else "your team")
    result = re.sub(r"\[(?:Company|Entreprise|Nom de l'entreprise|Company Name|insert company[^\]]*)\]", comp_sub, result, flags=re.I)

    # 3. Replace title placeholders
    tit_sub = title or ("Poste" if is_french else "Position")
    result = re.sub(r"\[(?:Role|Poste|Titre du poste|Job Title|Titre|insert role[^\]]*)\]", tit_sub, result, flags=re.I)

    # 4. Replace candidate placeholders
    result = re.sub(r"\[(?:Candidate Name|Votre Nom|Nom du candidat|Candidate)\]", sender, result, flags=re.I)
    result = re.sub(r"\bCandidate Name\b", sender, result, flags=re.I)

    # 5. Remove any remaining bracketed placeholders like [City], [Skill], etc.
    result = re.sub(r"\[[A-Za-z0-9\s_/\-–—]+\]", "", result)

    # 6. Normalize spacing
    result = re.sub(r"[ \t]+", " ", result)
    result = re.sub(r" ,", ",", result)
    result = re.sub(r"\n{3,}", "\n\n", result)
    return result.strip()


def _default_french_application(company: str, title: str, sender: str, profile: dict | None = None) -> str:
    company_name = company or "l'entreprise"
    role_name = title or "Poste"
    core_stack = (profile.get("core_stack") if profile else None) or ["Java", "Spring Boot", "React", "PostgreSQL", "Docker"]
    headline = (profile.get("headline") if profile else None) or f"spécialiste {role_name}"
    stack_desc = ", ".join(core_stack[:4]) if core_stack else "les technologies logicielles modernes"
    return (
        f"Bonjour l'équipe recrutement de {company_name},\n\n"
        f"Je me permets de vous soumettre ma candidature pour le poste de {role_name} au sein de {company_name}.\n\n"
        f"Fort d'une solide expertise technique en {headline.lower()} et maîtrisant notamment {stack_desc}, "
        f"je conçois et déploie des solutions fiables, performantes et évolutives. "
        f"La lecture de votre offre confirme que mon profil et ma rigueur correspondent aux enjeux de votre équipe.\n\n"
        f"Vous trouverez ci-joint mon CV détaillé retraçant mes réalisations. Je serais ravi de convenir d'un entretien pour échanger plus en détail sur vos projets.\n\n"
        f"En vous remerciant pour l'attention portée à ma candidature.\n\n"
        f"Bien cordialement,\n{sender}"
    ).strip()


def _default_english_application(company: str, title: str, sender: str, profile: dict | None = None) -> str:
    company_name = company or "your team"
    role_name = title or "Position"
    core_stack = (profile.get("core_stack") if profile else None) or ["Java", "Spring Boot", "React", "PostgreSQL", "Docker"]
    headline = (profile.get("headline") if profile else None) or f"{role_name} Specialist"
    stack_desc = ", ".join(core_stack[:4]) if core_stack else "modern software engineering and scalable architectures"
    return (
        f"Dear {company_name} Hiring Team,\n\n"
        f"I am writing to submit my application for the {role_name} position at {company_name}.\n\n"
        f"With hands-on experience in {headline.lower()} and expertise spanning {stack_desc}, "
        f"I deliver maintainable, high-performance solutions. Having reviewed your job opening, I am confident that my technical skillset and problem-solving approach align closely with your team's goals.\n\n"
        f"Please find attached my resume detailing my past projects and engineering accomplishments. "
        f"I would welcome the opportunity to speak with you regarding how I can contribute to your initiatives.\n\n"
        f"Thank you for your time and consideration.\n\n"
        f"Best regards,\n{sender}"
    ).strip()



def _get_job_val(job: Any, key: str, default: Any = None) -> Any:
    if job is None:
        return default
    if isinstance(job, dict):
        return job.get(key, default)
    return getattr(job, key, default)


def _application_subject(job: Any, profile: dict | None = None) -> str:
    is_fr = is_french_job(job)
    sender = _clean_sender_name(profile)
    title = (_get_job_val(job, "title") or ("Poste" if is_fr else "Position")).strip()
    company = (_get_job_val(job, "company") or "").strip()

    if is_fr:
        default_subj = f"Candidature — {title} — {sender}"
        custom = _get_job_val(job, "application_subject_fr") or _get_job_val(job, "application_subject")
        if custom and isinstance(custom, str):
            sanitized = sanitize_application_text(custom, sender, company, title, is_french=True)
            if sanitized.lower().startswith("application"):
                sanitized = re.sub(r"^Application\b", "Candidature", sanitized, flags=re.I)
            if not has_unresolved_placeholders(sanitized) and not is_inverted_perspective(sanitized) and len(sanitized) >= 10:
                if any(fr in sanitized.lower() for fr in ("candidature", "poste", "développeur", "developpeur", "ingénieur", "ingenieur")):
                    if sender.lower() not in sanitized.lower():
                        sanitized = f"{sanitized} — {sender}"
                    return sanitized
        return default_subj
    else:
        default_subj = f"Application — {title} — {sender}"
        custom = _get_job_val(job, "application_subject") or _get_job_val(job, "application_subject_fr")
        if custom and isinstance(custom, str):
            sanitized = sanitize_application_text(custom, sender, company, title, is_french=False)
            if sanitized.lower().startswith("candidature"):
                sanitized = re.sub(r"^Candidature\b", "Application", sanitized, flags=re.I)
            if not has_unresolved_placeholders(sanitized) and not is_inverted_perspective(sanitized) and len(sanitized) >= 10:
                if sender.lower() not in sanitized.lower():
                    sanitized = f"{sanitized} — {sender}"
                return sanitized
        return default_subj


def _application_body(job: Any, profile: dict | None = None) -> str:
    is_fr = is_french_job(job)
    sender = _clean_sender_name(profile)
    company = (_get_job_val(job, "company") or "").strip()
    title = (_get_job_val(job, "title") or "").strip()

    if is_fr:
        candidates = [
            _get_job_val(job, "application_email_fr"),
            _get_job_val(job, "application_email_body"),
            _get_job_val(job, "recruiter_pitch_fr"),
        ]
        default_body = _default_french_application(company, title, sender, profile=profile)
    else:
        candidates = [
            _get_job_val(job, "application_email_en"),
            _get_job_val(job, "application_email_body"),
            _get_job_val(job, "recruiter_pitch_en"),
        ]
        default_body = _default_english_application(company, title, sender, profile=profile)


    for cand in candidates:
        if not cand or not isinstance(cand, str):
            continue
        text = cand.strip()
        if len(text) < 40:
            continue
        # Immediately discard if it has employer/recruiter tone
        if is_inverted_perspective(text):
            continue

        sanitized = sanitize_application_text(text, sender, company, title, is_french=is_fr)
        if has_unresolved_placeholders(sanitized):
            continue
        if is_inverted_perspective(sanitized):
            continue
        if len(sanitized) < 40:
            continue

        # Language matching check
        if is_fr:
            if any(sanitized.lower().startswith(en_start) for en_start in ("dear ", "hello ", "hi ", "i am ", "i'm ")):
                continue
            if not any(w in sanitized.lower() for w in ("bonjour", "madame", "monsieur", "candidature", "cordialement", "je me permets", "mon cv", "votre offre", "remerciant")):
                continue
        else:
            if any(sanitized.lower().startswith(fr_start) for fr_start in ("bonjour", "madame", "monsieur", "cher", "chère")):
                continue
            if not any(w in sanitized.lower() for w in ("dear", "hello", "hi", "application", "regards", "sincerely", "writing to submit", "resume")):
                continue

        # Ensure applicant signature is present
        if sender.lower() not in sanitized.lower():
            closing = "\n\nBien cordialement,\n" if is_fr else "\n\nBest regards,\n"
            sanitized = f"{sanitized}{closing}{sender}"
        return sanitized

    return default_body


def _normalize_emails(values) -> list[str]:
    if not values:
        return []
    if isinstance(values, str):
        try:
            parsed = json.loads(values)
            values = parsed if isinstance(parsed, list) else [values]
        except Exception:
            values = [values]
    if not isinstance(values, (list, tuple, set)):
        values = [values]
    result: list[str] = []
    seen: set[str] = set()
    for value in values:
        for addr in EMAIL_RE.findall(str(value or "")):
            key = clean_email(addr)
            if key and key not in seen:
                seen.add(key)
                result.append(key)
    return result


def extract_application_emails(raw_description: str, emails=None) -> list[str]:
    candidates = _normalize_emails(emails) + _normalize_emails(EMAIL_RE.findall(str(raw_description or "")))
    out, seen = [], set()
    for addr in candidates:
        cleaned = clean_email(addr)
        if not cleaned or cleaned in seen:
            continue
        local = cleaned.split("@", 1)[0].lower()
        if local in GENERIC_BAD:
            continue
        if any(local.startswith(p) for p in ("noreply", "no-reply", "donotreply", "do-not-reply", "mailer-daemon")):
            continue
        if any(k in local for k in ("privacy", "accommodation", "accessibility", "gdpr", "compliance", "copyright", "dmca")):
            continue
        seen.add(cleaned)
        out.append(cleaned)
    return out


def rank_application_emails(emails: list[str], raw_description: str = "", company_url: str | None = None) -> list[str]:
    text = str(raw_description or "").lower()
    company_host = (urlparse(company_url).hostname or "").lower().removeprefix("www.") if company_url else ""
    scored = []
    for email_addr in emails:
        local, domain = email_addr.split("@", 1)
        score = 0
        for token, points in GENERIC_LOCAL_SCORES.items():
            if token in local:
                score += points
        if domain and company_host and (domain == company_host or domain.endswith("." + company_host) or company_host.endswith("." + domain)):
            score += 25
        context = text[:2000]
        if local in context and any(k in context for k in ("apply", "application", "recruiter", "recruitment", "hiring", "cv", "resume")):
            score += 25
        if domain in PERSONAL_DOMAINS:
            score -= 20
        scored.append((score, email_addr))
    return [email for _, email in sorted(scored, key=lambda x: (-x[0], x[1]))]


def _discover_public_contact_emails(job: dict) -> list[str]:
    """Find hiring/contact mailboxes only on pages already associated with the posting."""
    seed_urls = []
    for value in (job.get("company_url"), job.get("application_url"), job.get("job_url")):
        if isinstance(value, str) and value.startswith(("http://", "https://")):
            seed_urls.append(value)
    if not seed_urls:
        return []

    queue: list[str] = []
    seen_urls: set[str] = set()
    for base in seed_urls[:3]:
        if base not in seen_urls:
            queue.append(base); seen_urls.add(base)
        try:
            parsed = urlparse(base)
            if parsed.hostname and not is_board_url(base):
                for suffix in ("/careers", "/jobs", "/contact", "/about/careers"):
                    candidate = f"{parsed.scheme}://{parsed.netloc}{suffix}"
                    if candidate not in seen_urls:
                        queue.append(candidate); seen_urls.add(candidate)
        except Exception:
            continue

    found: list[str] = []
    seen_emails: set[str] = set()
    allowed_hosts = set()
    for seed in seed_urls[:3]:
        host = (urlparse(seed).hostname or "").lower().removeprefix("www.")
        if host and not is_board_url(seed):
            allowed_hosts.add(host)

    for url in queue[:8]:
        try:
            response = requests.get(url, headers={"User-Agent": "Mozilla/5.0 JobSearchAutomation/2.0"}, timeout=6, allow_redirects=True)
            if response.status_code >= 400 or "text/html" not in response.headers.get("content-type", "text/html").lower():
                continue
            content = response.text[:1_000_000]
            parsed = urlparse(response.url)
            response_host = (parsed.hostname or "").lower().removeprefix("www.")
            if allowed_hosts and not any(response_host == h or response_host.endswith("." + h) or h.endswith("." + response_host) for h in allowed_hosts):
                continue
            for addr in EMAIL_RE.findall(content):
                addr = normalize_email(addr)
                local = addr.split("@", 1)[0]
                if addr in seen_emails or local in GENERIC_BAD or local.startswith(("noreply", "no-reply")):
                    continue
                # Only keep addresses that look like professional/hiring mailboxes here.
                if local not in GENERIC_LOCAL_SCORES and not any(t in local for t in ("recruit", "talent", "hiring", "career", "hr", "job")):
                    continue
                seen_emails.add(addr); found.append(addr)
        except Exception:
            continue

    if not found and getattr(settings, "enable_hr_enrichment", True) and (job.get("company") or job.get("company_url")):
        try:
            from src.ingestion.hr_enrichment import discover_hr_contacts_for_company
            hr_contacts = discover_hr_contacts_for_company(
                company_name=job.get("company", ""),
                company_url=job.get("company_url"),
                job_url=job.get("job_url"),
            )
            for c in hr_contacts:
                if c.confidence >= 65 and c.email not in seen_emails:
                    seen_emails.add(c.email)
                    found.append(c.email)
        except Exception as err:
            logger.debug("HR discovery fallback skipped: %s", err)

    return found


def _save_eml_draft(msg: EmailMessage, job_id, company, title) -> str | None:
    try:
        directory = Path(settings.candidate_resume_path).parent.parent / "outreach_drafts"
        directory.mkdir(parents=True, exist_ok=True)
        safe_company = re.sub(r"[^A-Za-z0-9]+", "_", str(company or "company"))[:35].strip("_")
        safe_title = re.sub(r"[^A-Za-z0-9]+", "_", str(title or "job"))[:45].strip("_")
        path = directory / f"application_{job_id or 'new'}_{safe_company}_{safe_title}.eml"
        path.write_bytes(bytes(msg))
        return str(path)
    except Exception:
        logger.exception("Could not write application draft")
        return None


def _attach_documents(msg: EmailMessage, profile: dict | None = None) -> bool:
    resume_target = None
    if profile and profile.get("resume_path"):
        cand_p = Path(profile["resume_path"])
        if cand_p.is_file():
            resume_target = cand_p
    if not resume_target:
        fallback = Path(settings.candidate_resume_path)
        if fallback.is_file():
            resume_target = fallback
    if not resume_target or not resume_target.is_file():
        return False
    msg.add_attachment(resume_target.read_bytes(), maintype="application", subtype="pdf", filename=resume_target.name)
    return True


def _build_application_message(to_addr: str, subject: str, body: str, profile: dict | None = None, user_id: int | None = None) -> tuple[EmailMessage, bool]:
    msg = EmailMessage()
    msg["Message-ID"] = make_msgid()
    msg["Subject"] = subject
    from_name = _clean_sender_name(profile)
    user_cfg = {}
    try:
        from src.storage.user_settings import get_user_effective_settings
        uid = user_id or (profile.get("user_id") if profile else None)
        user_cfg = get_user_effective_settings(user_id=uid)
    except Exception:
        pass
    from_email = (profile.get("email") if profile else None) or user_cfg.get("sender_email") or settings.sender_email
    msg["From"] = f"{from_name} <{from_email}>"
    msg["To"] = to_addr
    msg["Reply-To"] = from_email
    msg.set_content(body)
    return msg, _attach_documents(msg, profile=profile)



def _send_email(msg: EmailMessage, to_addr: str, session, *, job_id: int | None, idempotency_key: str, allow_personal_domain: bool = False, user_id: int | None = None) -> tuple[bool, str | None]:
    if not settings.outbound_send_enabled or settings.outbound_send_kill_switch:
        return False, "Outbound email sending is disabled or kill-switched."

    user_cfg = {}
    try:
        from src.storage.user_settings import get_user_effective_settings
        user_cfg = get_user_effective_settings(user_id=user_id)
    except Exception:
        pass

    s_email = user_cfg.get("sender_email") or settings.sender_email
    s_host = user_cfg.get("smtp_host") or settings.sender_smtp_host
    s_port = user_cfg.get("smtp_port") or settings.sender_smtp_port
    s_pass = user_cfg.get("smtp_password") or settings.sender_smtp_password

    if not all([s_email, s_host, s_pass]):
        return False, "Sender SMTP configuration is incomplete."
    recipient_check = validate_external_email(to_addr, allow_personal_domain=allow_personal_domain)
    if not recipient_check.allowed:
        return False, recipient_check.reason
    body = msg.get_body(preferencelist=("plain",))
    body_text = body.get_content() if body else ""
    content_check = validate_message_content(msg.get("Subject", ""), body_text)
    if not content_check.allowed:
        return False, content_check.reason

    try:
        ledger = claim_outbound(session, idempotency_key=idempotency_key, job_id=job_id, email_type="application", recipient=to_addr, subject=msg.get("Subject", ""), message_id=msg.get("Message-ID", ""))
        reserve_outbound_send(session, email_type="application")
    except RuntimeError as exc:
        try:
            mark_failed(session, ledger, str(exc))
        except Exception:
            pass
        return False, str(exc)

    try:
        with smtplib.SMTP(s_host, s_port, timeout=30) as server:
            server.ehlo(); server.starttls(); server.ehlo(); server.login(s_email, s_pass); server.send_message(msg)
        mark_sent(session, ledger)
        return True, None
    except Exception as exc:
        mark_unknown(session, ledger, str(exc))
        logger.error("Application delivery state unknown for %s: %s", to_addr, exc)
        return False, f"SMTP delivery state unknown: {exc}"


def _candidate_application_url(raw_description: str, direct_url: str | None, source_url: str | None) -> str | None:
    urls = []
    for value in (direct_url, source_url):
        if isinstance(value, str) and value.startswith(("http://", "https://")):
            urls.append(value.rstrip(".,);]>'\""))
    urls.extend(x.rstrip(".,);]>'\"") for x in re.findall(r"https?://[^\s<>\"']+", str(raw_description or "")))
    for url in urls:
        if is_board_url(url):
            continue
        # Any public non-job-board URL can be a valid employer application link.
        # Known ATS/careers hosts are preferred, but we don't discard custom employer portals.
        return url
    return None


def apply_to_job(job_record: dict, session=None, profile: dict | None = None) -> dict:
    if profile is None:
        user_id = job_record.get("user_id")
        try:
            from src.candidate.profile_manager import get_active_profile
            profile = get_active_profile(user_id=user_id, session=session)
        except Exception:
            profile = None

    raw_description = job_record.get("raw_description") or ""
    explicit_emails = _normalize_emails(job_record.get("application_emails"))
    emails = extract_application_emails(raw_description, explicit_emails)
    discovered = False
    if not emails:
        emails = extract_application_emails(raw_description, _discover_public_contact_emails(job_record))
        discovered = bool(emails)
    ranked = rank_application_emails(emails, raw_description, job_record.get("company_url"))
    app_email = clean_email(ranked[0]) if ranked else None
    app_url = _candidate_application_url(raw_description, job_record.get("application_url"), job_record.get("job_url"))

    if ranked:
        primary_email = clean_email(ranked[0])
        if not primary_email:
            return {"application_method": "recruiter_email", "application_email": None, "application_url": app_url, "application_status": "invalid_recipient", "application_error": f"Invalid email format: {ranked[0]}", "resume_attached": False, "applied": False, "thread_subject": None, "message_id": None}
        body = _application_body(job_record, profile=profile)
        subject = _application_subject(job_record, profile=profile)
        if not body:
            return {"application_method": "recruiter_email", "application_email": primary_email, "application_url": app_url, "application_status": "content_missing", "application_error": "No generated application message was available.", "resume_attached": False, "applied": False, "thread_subject": subject, "message_id": None}
        
        # Save a single draft
        uid = job_record.get("user_id")
        msg, resume_attached = _build_application_message(primary_email, subject, body, profile=profile, user_id=uid)
        draft_path = _save_eml_draft(msg, job_record.get("id"), job_record.get("company"), job_record.get("title"))
        
        if settings.auto_apply_mode == "send" and settings.outbound_send_enabled and not resume_attached and not settings.auto_apply_allow_missing_resume:
            resume_display_path = (profile.get("resume_path") if profile else None) or settings.candidate_resume_path
            return {"application_method": "recruiter_email", "application_email": primary_email, "application_url": app_url, "application_status": "resume_missing", "application_error": f"Resume not found at {resume_display_path}", "resume_attached": False, "draft_path": draft_path, "applied": False, "thread_subject": subject, "message_id": None}
        
        if settings.auto_apply_mode == "send" and session is not None and settings.outbound_send_enabled:
            allow_personal = primary_email in explicit_emails and settings.allow_personal_application_recipient_domains
            idempotency_key = f"application:{job_record.get('id')}:{primary_email}:{job_record.get('title')}"
            sent, error = _send_email(msg, primary_email, session, job_id=job_record.get("id"), idempotency_key=idempotency_key, allow_personal_domain=allow_personal, user_id=uid)
            
            return {
                "application_method": "recruiter_email" if sent else "recruiter_email_blocked",
                "application_email": primary_email,
                "application_url": app_url,
                "application_status": "sent" if sent else "blocked",
                "application_error": error if not sent else None,
                "resume_attached": resume_attached,
                "draft_path": draft_path,
                "applied": sent,
                "thread_subject": subject,
                "message_id": msg.get("Message-ID") if sent else None,
                "applied_at": __import__("datetime").datetime.now(__import__("datetime").timezone.utc) if sent else None,
                "application_body": body,
            }
        return {"application_method": "recruiter_email_draft", "application_email": primary_email, "application_url": app_url, "application_status": "draft", "resume_attached": resume_attached, "draft_path": draft_path, "applied": False, "thread_subject": subject, "application_body": body, "message_id": None}

    if session is not None and app_url and settings.auto_apply_mode == "send" and settings.auto_apply_web_enabled:
        body = _application_body(job_record, profile=profile)
        subject = _application_subject(job_record, profile=profile)
        result = apply_via_browser({**job_record, "application_url": app_url, "application_body": body, "application_subject": subject}, session, profile=profile)
        result.setdefault("application_email", None); result.setdefault("thread_subject", None); result.setdefault("message_id", None)
        has_resume = (profile and profile.get("resume_path") and Path(profile["resume_path"]).is_file()) or Path(settings.candidate_resume_path).is_file()
        result.setdefault("resume_attached", has_resume)
        return result


    listing_url = job_record.get("job_url")
    if listing_url and is_board_url(listing_url):
        host = (urlparse(listing_url).hostname or "").lower()
        status = "manual_linkedin" if "linkedin.com" in host else "manual_job_board"
        method = "linkedin_manual" if "linkedin.com" in host else "job_board_manual"
        return {"application_method": method, "application_email": None, "application_url": listing_url, "application_status": status, "applied": False, "thread_subject": None, "message_id": None}
    if app_url:
        return {"application_method": "manual_portal", "application_email": None, "application_url": app_url, "application_status": "manual_portal", "applied": False, "thread_subject": None, "message_id": None}
    return {"application_method": "no_application_channel", "application_email": None, "application_url": None, "application_status": "no_application_channel", "applied": False, "thread_subject": None, "message_id": None}
