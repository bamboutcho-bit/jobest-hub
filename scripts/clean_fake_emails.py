#!/usr/bin/env python3
"""Database cleanup script:
1. Purges generated fake role-pattern emails (recruiting@, talent@, etc.) and compliance emails (privacy@, accommodation@) from existing job records.
2. Removes accidental suppression of 'mailer-daemon@googlemail.com' or mailer-daemon addresses.
3. Resets job pipeline stages that were erroneously marked as 'response_received' by Mailer-Daemon bounce emails.

Usage:
    python scripts/clean_fake_emails.py --dry-run
    python scripts/clean_fake_emails.py --apply
"""
import argparse
import logging
import os
import sys

# Ensure project root is on sys.path
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from sqlalchemy import select

from src.ingestion.hr_enrichment import is_excluded_email
from src.storage.db import get_session
from src.storage.models import EmailEvent, JobPosting, OutboundSuppression, PipelineStage

logging.basicConfig(level=logging.INFO, format="%(asctime)s [%(levelname)s] %(message)s")
logger = logging.getLogger(__name__)

SPECULATIVE_PREFIXES = {"recruiting", "talent", "careers", "jobs", "hiring", "people", "hr"}


def clean_database(dry_run: bool = True) -> dict:
    cleaned_jobs = 0
    removed_suppressions = 0
    fixed_bounce_jobs = 0

    with get_session() as session:
        # 1. Clean accidental mailer-daemon suppressions
        bad_suppressions = session.scalars(
            select(OutboundSuppression).where(
                (OutboundSuppression.recipient_email.like("%mailer-daemon%"))
                | (OutboundSuppression.recipient_email.like("%postmaster%"))
                | (OutboundSuppression.domain.in_(["googlemail.com", "gmail.com"]))
            )
        ).all()
        for sup in bad_suppressions:
            logger.info("Found improper suppression: id=%s, email=%s, domain=%s", sup.id, sup.recipient_email, sup.domain)
            removed_suppressions += 1
            if not dry_run:
                session.delete(sup)

        # 2. Inspect job postings with application_emails
        jobs = session.scalars(
            select(JobPosting).where(JobPosting.application_emails.isnot(None))
        ).all()

        for job in jobs:
            raw_desc = (job.raw_description or "").lower()
            current_emails = [e.strip() for e in str(job.application_emails).split(",") if e.strip()]
            valid_emails = []
            modified = False

            for em in current_emails:
                em_lower = em.lower()
                prefix = em_lower.split("@")[0].strip("._-")

                # 1. If it's a compliance or excluded prefix, strip it regardless of where it appeared
                if is_excluded_email(em):
                    logger.info("Job #%s (%s): stripping compliance/junk email: %s", job.id, job.company, em)
                    modified = True
                    continue

                # 2. If it's a speculative role prefix not found in the description, strip it
                if prefix in SPECULATIVE_PREFIXES and em_lower not in raw_desc:
                    logger.info("Job #%s (%s): stripping speculative unverified email: %s", job.id, job.company, em)
                    modified = True
                    continue

                valid_emails.append(em)

            if modified:
                cleaned_jobs += 1
                new_val = ", ".join(valid_emails) if valid_emails else None
                logger.info("Job #%s (%s): '%s' -> '%s'", job.id, job.company, job.application_emails, new_val)
                if not dry_run:
                    job.application_emails = new_val

        # 3. Check for jobs that were erroneously set to RESPONSE_RECEIVED from a bounce email
        response_jobs = session.scalars(
            select(JobPosting).where(JobPosting.pipeline_stage == PipelineStage.RESPONSE_RECEIVED)
        ).all()

        for job in response_jobs:
            # Check latest inbound email event
            events = [e for e in job.email_events if e.direction == "inbound"]
            if events:
                latest = max(events, key=lambda e: e.created_at or 0)
                sender = (latest.sender_email or "").lower()
                subj = (latest.subject or "").lower()
                if "mailer-daemon" in sender or "postmaster" in sender or "delivery status notification" in subj or "failure" in subj:
                    fixed_bounce_jobs += 1
                    logger.info("Job #%s (%s): falsely marked RESPONSE_RECEIVED by bounce email from %s. Reverting.", job.id, job.company, sender)
                    if not dry_run:
                        job.pipeline_stage = PipelineStage.APPLIED
                        job.application_status = "bounced"
                        job.application_error = f"Delivery failed (Mailer-Daemon): {sender}"

        if not dry_run:
            session.commit()
            logger.info("All changes committed successfully.")
        else:
            logger.info("Dry run complete. No changes were written to the database.")

    summary = {
        "improper_suppressions_removed": removed_suppressions,
        "job_emails_cleaned": cleaned_jobs,
        "bounce_jobs_corrected": fixed_bounce_jobs,
        "dry_run": dry_run,
    }
    return summary


def main():
    parser = argparse.ArgumentParser(description="Clean fake generated emails and fix Mailer-Daemon bounces")
    parser.add_argument("--dry-run", action="store_true", help="Preview changes without modifying DB")
    parser.add_argument("--apply", action="store_true", help="Commit changes to the database")
    args = parser.parse_args()

    if not args.dry_run and not args.apply:
        print("Please specify either --dry-run or --apply")
        return

    summary = clean_database(dry_run=not args.apply)
    print("\n=== Cleanup Summary ===")
    for k, v in summary.items():
        print(f"  {k}: {v}")


if __name__ == "__main__":
    main()
