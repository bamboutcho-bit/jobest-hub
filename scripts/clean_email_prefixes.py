"""Database sanitizer for cleaning leading/trailing dashes, bullets, and symbols from emails."""
import re
import sys
from src.storage.db import get_session
from src.storage.models import JobPosting
from src.outreach.safety import clean_email, validate_external_email
from src.ingestion.hr_enrichment import is_excluded_email

def run_cleanup():
    print("Starting email prefix & punctuation cleanup...")
    with get_session() as session:
        jobs = session.query(JobPosting).filter(
            (JobPosting.application_emails.isnot(None)) | (JobPosting.application_email.isnot(None))
        ).all()
        
        cleaned_count = 0
        purged_count = 0
        
        for j in jobs:
            changed = False
            # 1. Clean application_emails
            if j.application_emails:
                raw_list = [e.strip() for e in str(j.application_emails).split(",") if e.strip()]
                cleaned_list = []
                for raw in raw_list:
                    cl = clean_email(raw)
                    if cl != raw:
                        changed = True
                    if cl and not is_excluded_email(cl):
                        if cl not in cleaned_list:
                            cleaned_list.append(cl)
                    else:
                        changed = True
                        purged_count += 1
                
                new_str = ", ".join(cleaned_list) if cleaned_list else None
                if new_str != j.application_emails:
                    print(f"Job #{j.id} ({j.company}) application_emails: '{j.application_emails}' -> '{new_str}'")
                    j.application_emails = new_str
                    changed = True

            # 2. Clean single application_email
            if j.application_email:
                cl = clean_email(j.application_email)
                if cl != j.application_email:
                    if cl and not is_excluded_email(cl):
                        print(f"Job #{j.id} ({j.company}) application_email: '{j.application_email}' -> '{cl}'")
                        j.application_email = cl
                    else:
                        print(f"Job #{j.id} ({j.company}) application_email purged: '{j.application_email}' -> None")
                        j.application_email = None
                    changed = True

            if changed:
                cleaned_count += 1

        session.commit()
        print(f"\nCleanup complete: updated {cleaned_count} jobs, purged/normalized {purged_count} invalid email fragments.")

if __name__ == "__main__":
    run_cleanup()
