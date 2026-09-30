"""Dynamic prompts for job matching and tailored applications."""
from __future__ import annotations

import json
from typing import Any

def get_schema_example(candidate_name: str = "Candidate") -> dict[str, Any]:
    return {
        "match_score": 88,
        "continent": "europe",
        "experience_level": "entry",
        "experience_years_required": 1,
        "degree_required": "bachelor",
        "degree_matched": True,
        "visa_category": "sponsored",
        "relocation_detected": True,
        "visa_sponsorship_detected": True,
        "visa_status_notes": "The posting explicitly mentions visa sponsorship or relocation support.",
        "key_skills_matched": ["Java", "Spring Boot", "Docker"],
        "recruiter_pitch_fr": f"Bonjour, je me permets de vous contacter car j'ai découvert votre offre pour le poste. Disposant d'une solide expertise technique sur Java et Spring Boot, je serais ravi d'échanger avec vous sur vos projets. Bien cordialement, {candidate_name}",
        "recruiter_pitch_en": f"Hello, I came across your opening and wanted to reach out directly. With a strong background in Java, Spring Boot, and scalable systems, I would welcome the opportunity to discuss how my experience aligns with your team's roadmap. Best regards, {candidate_name}",
        "application_subject": f"Application — Backend Engineer — {candidate_name}",
        "application_email_en": f"Dear Hiring Team,\n\nI am writing to submit my application for this role. With practical experience in building robust backend services, APIs, and distributed architectures using Java and Spring Boot, I deliver reliable, maintainable code.\n\nPlease find attached my resume detailing my technical achievements. I look forward to speaking with you.\n\nBest regards,\n{candidate_name}",
        "application_subject_fr": f"Candidature — Développeur Java — {candidate_name}",
        "application_email_fr": f"Bonjour l'équipe recrutement,\n\nJe me permets de vous soumettre ma candidature pour ce poste. Fort d'une solide expertise en développement backend Java, Spring Boot et microservices, je conçois et déploie des solutions performantes et fiables.\n\nVous trouverez mon CV ci-joint retraçant mon parcours. Je serais ravi d'échanger lors d'un entretien pour détailler ma motivation.\n\nBien cordialement,\n{candidate_name}"
    }


VISA_KEYWORDS = [
    "visa sponsorship", "visa sponsor", "sponsor visa", "relocation package",
    "relocation support", "relocation assistance", "work permit", "work authorization",
    "eu blue card", "skilled worker", "global mobility", "immigration support",
]


def build_system_prompt(profile: dict[str, Any] | None = None) -> str:
    """Construct system prompt dynamically based on the active candidate profile."""
    if profile is None:
        try:
            from src.candidate.profile_manager import get_active_profile
            profile = get_active_profile()
        except Exception:
            from config.candidate_profile import CANDIDATE_PROFILE
            profile = dict(CANDIDATE_PROFILE)

    candidate_name = profile.get("name") or "Candidate"
    location = profile.get("current_location", "Remote")
    resume_section = ""
    if profile.get("resume_text"):
        resume_section = f"\nCANDIDATE RESUME / CV:\n{profile['resume_text']}\n"

    schema_example = get_schema_example(candidate_name)
    profile_data = {k: v for k, v in profile.items() if k != "resume_text"}

    return f"""You are a private job-application assistant for one real candidate: {candidate_name}.
Return ONLY one valid JSON object; no markdown, no commentary.

CANDIDATE PROFILE:
{json.dumps(profile_data, indent=2, ensure_ascii=False)}
{resume_section}
CRITICAL PERSPECTIVE RULES:
1. YOU ARE THE JOB APPLICANT ({candidate_name}) APPLYING FOR THE JOB.
2. NEVER write from the perspective of an employer or recruiter.
   FORBIDDEN: Never write "nous sommes ravis de recevoir votre candidature", "nous recrutons", "merci pour votre candidature", "we are pleased to receive your application", or "thank you for applying".
3. ABSOLUTELY NO PLACEHOLDERS:
   FORBIDDEN: Never output bracketed text like [Nom], [Name], [Company], [Role], [Entreprise], or "Candidate Name".
   Always write "{candidate_name}" for the sender's name in subjects and signatures.
   If recruiter name is unknown, use "Bonjour l'équipe recrutement," or "Dear Hiring Team,".
4. Match the candidate honestly to the exact role, company, location and requirements.
5. Do not invent employers, projects, years, certifications, salary, work authorization,
   achievements, technologies or contact details.
6. Candidate is based in {location}. Never claim existing work authorization in another country.
7. application_email_en: 110-170 words, polished cover letter from {candidate_name} to the employer, referencing attached CV.
8. application_email_fr: 110-170 words, polished French motivation letter from {candidate_name} to the employer, referencing attached CV.
9. application_subject: Must be in format "Application — [Role Title] — {candidate_name}".
10. application_subject_fr: Must be in format "Candidature — [Titre du Poste] — {candidate_name}".

OUTPUT SCHEMA:
{json.dumps(schema_example, indent=2, ensure_ascii=False)}
"""


class _DynamicSystemPrompt(str):
    def __str__(self):
        return build_system_prompt()

    def __repr__(self):
        return build_system_prompt()


SYSTEM_PROMPT = _DynamicSystemPrompt()


def build_user_prompt(job: dict) -> str:
    description = (job.get("raw_description") or "")[:4000]
    return f"""JOB POSTING

Title: {job.get('title')}
Company: {job.get('company')}
Location: {job.get('location')}
Source: {job.get('source_site')}
Remote flag: {job.get('is_remote')}
Application email(s): {job.get('application_emails')}
Application URL: {job.get('application_url')}
Company URL: {job.get('company_url')}

DESCRIPTION / REQUIREMENTS
{description}

TASK
Assess this exact role and generate the personalized application copy that would be sent to this exact employer.
Return the JSON object only."""
