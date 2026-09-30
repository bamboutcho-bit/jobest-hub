"""CLI tool to test and run HR & Talent Acquisition email discovery.

Usage:
  # Test HR discovery for a specific company or domain:
  python scripts/enrich_leads.py --company "Datadog" --domain "datadoghq.com"

  # Scan database leads and enrich them with HR emails:
  python scripts/enrich_leads.py --scan-db --limit 10
"""
import argparse
import sys
import os

# Ensure project root is on sys.path
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from src.ingestion.hr_enrichment import discover_hr_contacts_for_company, enrich_job_hr_contacts, check_domain_has_mx
from src.storage.db import get_session
from src.storage.models import JobPosting, PipelineStage


def main():
    parser = argparse.ArgumentParser(description="HR & Talent Acquisition Email Discovery Tool")
    parser.add_argument("--company", type=str, help="Company name to test")
    parser.add_argument("--domain", type=str, help="Company domain or website URL")
    parser.add_argument("--scan-db", action="store_true", help="Scan and enrich existing database leads")
    parser.add_argument("--limit", type=int, default=10, help="Max leads to enrich from DB")
    args = parser.parse_args()

    if args.company or args.domain:
        print(f"\n=======================================================")
        print(f"Testing HR Email Discovery for Company: '{args.company or ''}', Domain: '{args.domain or ''}'")
        print(f"=======================================================")
        if args.domain:
            has_mx = check_domain_has_mx(args.domain)
            print(f"[*] MX Record Status for {args.domain}: {'ACTIVE (Valid mail server)' if has_mx else 'INACTIVE / Not Found'}")

        contacts = discover_hr_contacts_for_company(
            company_name=args.company or "",
            company_url=f"https://{args.domain}" if args.domain and not args.domain.startswith("http") else args.domain,
        )

        print(f"\n[+] Total Discovered Contacts: {len(contacts)}")
        for i, c in enumerate(contacts, 1):
            print(f"  {i}. {c.email} | Conf: {c.confidence}% | Source: {c.source} | Title: {c.title or 'N/A'}")
        print("=======================================================\n")
        return

    if args.scan_db:
        print(f"\n[*] Scanning database for high-match leads lacking HR emails (Limit: {args.limit})...")
        with get_session() as session:
            leads = (
                session.query(JobPosting)
                .filter(
                    (JobPosting.application_emails == None) | (JobPosting.application_emails == ""),
                    JobPosting.company != "Unknown company",
                    JobPosting.company != None,
                    JobPosting.pipeline_stage.in_([PipelineStage.EVALUATED_MATCH, PipelineStage.DISCOVERED, PipelineStage.APPLIED]),
                )
                .order_by(JobPosting.id.desc())
                .limit(args.limit)
                .all()
            )

            print(f"[*] Found {len(leads)} candidates to inspect.")
            enriched_count = 0
            for lead in leads:
                print(f"\n--- Checking Job #{lead.id}: {lead.title} @ {lead.company} ---")
                top_email = enrich_job_hr_contacts(lead)
                if top_email:
                    print(f"    -> [ENRICHED] Discovered: {lead.application_emails}")
                    enriched_count += 1
                else:
                    print(f"    -> [NO EMAIL FOUND]")
            
            session.commit()
            print(f"\n[+] Database scan complete: Enriched {enriched_count} / {len(leads)} leads with HR emails.")
        return

    parser.print_help()


if __name__ == "__main__":
    main()
