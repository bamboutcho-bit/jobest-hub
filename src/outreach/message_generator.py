"""
Cold Outreach module — takes the pitches Claude already generated during
evaluation (recruiter_pitch_fr / recruiter_pitch_en) and renders them into
ready-to-send artifacts: a LinkedIn-length DM and a full email with subject
line, saved to disk so Hamza can review/send manually (no auto-sending —
outreach stays human-in-the-loop by design).
"""
import logging
import os
from datetime import datetime

from jinja2 import Template
from src.evaluation.language import is_french_job

logger = logging.getLogger(__name__)

OUTREACH_DIR = "outreach_drafts"

EMAIL_TEMPLATE_EN = Template("""Subject: {{ title }} at {{ company }} — {{ candidate_name }}

{{ pitch_en }}

---
{{ candidate_name }}
{{ current_location }}
Job link: {{ job_url }}
""")

EMAIL_TEMPLATE_FR = Template("""Subject: Candidature — {{ title }} chez {{ company }} — {{ candidate_name }}

{{ pitch_fr }}

---
{{ candidate_name }}
{{ current_location }}
Lien de l'offre: {{ job_url }}
""")

LINKEDIN_TEMPLATE_EN = Template("""{{ pitch_en }}

(FR version below for French-speaking recruiters)
---
{{ pitch_fr }}
""")

LINKEDIN_TEMPLATE_FR = Template("""{{ pitch_fr }}

(EN version below for international teams)
---
{{ pitch_en }}
""")


def generate_outreach_draft(job_record: dict, candidate_name: str, current_location: str) -> str:
    """Writes a .txt draft to outreach_drafts/ and returns its path.
    `job_record` is expected to already contain recruiter_pitch_en/fr,
    title, company, job_url."""
    os.makedirs(OUTREACH_DIR, exist_ok=True)

    is_french = is_french_job(job_record)
    email_template = EMAIL_TEMPLATE_FR if is_french else EMAIL_TEMPLATE_EN
    linkedin_template = LINKEDIN_TEMPLATE_FR if is_french else LINKEDIN_TEMPLATE_EN

    email_body = email_template.render(
        title=job_record.get("title"),
        company=job_record.get("company"),
        candidate_name=candidate_name,
        current_location=current_location,
        pitch_en=job_record.get("recruiter_pitch_en") or job_record.get("application_email_en"),
        pitch_fr=job_record.get("recruiter_pitch_fr") or job_record.get("application_email_fr"),
        job_url=job_record.get("job_url"),
    )
    linkedin_body = linkedin_template.render(
        pitch_en=job_record.get("recruiter_pitch_en"),
        pitch_fr=job_record.get("recruiter_pitch_fr"),
    )

    safe_company = "".join(c for c in (job_record.get("company") or "unknown") if c.isalnum())[:30]
    timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")
    filepath = os.path.join(OUTREACH_DIR, f"{safe_company}_{timestamp}.txt")

    with open(filepath, "w", encoding="utf-8") as f:
        f.write("=== EMAIL DRAFT ===\n")
        f.write(email_body)
        f.write("\n\n=== LINKEDIN DM DRAFT ===\n")
        f.write(linkedin_body)

    logger.info("Outreach draft written: %s", filepath)
    return filepath
