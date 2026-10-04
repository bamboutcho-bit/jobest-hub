"""Clean and purge dummy, placeholder, Sentry/telemetry, and inactive/non-MX emails from JobPosting records."""
import sys
from pathlib import Path
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

import logging
from sqlalchemy import select, or_
from src.storage.db import get_session
from src.storage.models import JobPosting, OutboundSuppression, InboxMessage
from src.outreach.safety import validate_external_email, clean_email

logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)

def clean_database_emails():
    with get_session() as session:
        # Load all suppressed bounce emails
        bounced_emails = set(
            session.scalars(
                select(OutboundSuppression.recipient_email).where(
                    OutboundSuppression.reason == "bounce",
                    OutboundSuppression.recipient_email.isnot(None)
                )
            ).all()
        )
        logger.info("Found %d suppressed bounce addresses", len(bounced_emails))

        jobs = session.scalars(
            select(JobPosting).where(
                or_(
                    JobPosting.application_emails.isnot(None),
                    JobPosting.application_email.isnot(None),
                )
            )
        ).all()
        logger.info("Scanning %d jobs with email contacts...", len(jobs))

        cleaned_jobs_count = 0
        removed_emails_total = 0
        removed_samples = []

        for job in jobs:
            modified = False

            # 1. Clean application_emails
            if job.application_emails:
                raw_list = [clean_email(e) for e in str(job.application_emails).replace(";", ",").split(",") if e.strip()]
                valid_list = []
                for em in raw_list:
                    if not em:
                        continue
                    if em in bounced_emails:
                        removed_emails_total += 1
                        removed_samples.append((job.id, em, "previously_bounced"))
                        modified = True
                        continue

                    # Validate with safety rules + DNS MX
                    res = validate_external_email(em, allow_personal_domain=True, check_mx=True)
                    if not res.allowed:
                        removed_emails_total += 1
                        removed_samples.append((job.id, em, res.reason))
                        modified = True
                        continue

                    valid_list.append(em)

                new_app_emails = ", ".join(valid_list) if valid_list else None
                if new_app_emails != job.application_emails:
                    job.application_emails = new_app_emails
                    modified = True

            # 2. Clean application_email
            if job.application_email:
                target_em = clean_email(job.application_email)
                if not target_em or target_em in bounced_emails:
                    job.application_email = None
                    modified = True
                else:
                    res = validate_external_email(target_em, allow_personal_domain=True, check_mx=True)
                    if not res.allowed:
                        job.application_email = None
                        modified = True

            # 3. If job had bad email and was blocked or invalid_recipient, allow web fallback if available
            if modified:
                cleaned_jobs_count += 1
                if not job.application_emails and not job.application_email:
                    if job.application_status in ("invalid_recipient", "recruiter_email_blocked"):
                        if job.application_url or job.job_url:
                            job.application_status = None
                            job.application_error = None

        session.commit()
        logger.info("Successfully cleaned %d jobs! Removed %d invalid/dummy/dead emails.", cleaned_jobs_count, removed_emails_total)
        print(f"\n--- Summary ---")
        print(f"Total jobs modified: {cleaned_jobs_count}")
        print(f"Total invalid/inactive emails purged: {removed_emails_total}")
        print(f"\nSample purged emails (first 25):")
        for jid, em, reason in removed_samples[:25]:
            print(f"  Job #{jid}: {em} -> {reason}")

if __name__ == "__main__":
    clean_database_emails()
