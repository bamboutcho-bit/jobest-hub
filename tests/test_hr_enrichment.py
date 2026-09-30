import pytest
from unittest.mock import MagicMock, patch

from src.ingestion.hr_enrichment import (
    is_excluded_email,
    generate_verified_role_patterns,
    discover_hr_contacts_for_company,
    DiscoveredContact,
)
from src.application.auto_apply import (
    extract_application_emails,
    apply_to_job,
)
from src.inbox.inbox_monitor import (
    _is_bounce_message,
    _extract_bounced_recipient,
)
from src.storage.models import JobPosting, PipelineStage


def test_is_excluded_email_blocks_compliance_and_fake_inboxes():
    # Compliance & non-hiring inboxes
    assert is_excluded_email("_Accommodation@McKesson.com") is True
    assert is_excluded_email("Accessibility@mckesson.ca") is True
    assert is_excluded_email("data.privacy@syone.com") is True
    assert is_excluded_email("dpo@aubay.pt") is True
    assert is_excluded_email("privacy@company.com") is True
    assert is_excluded_email("gdpr-compliance@domain.com") is True
    assert is_excluded_email("legal@domain.com") is True
    assert is_excluded_email("abuse@domain.com") is True
    assert is_excluded_email("mailer-daemon@googlemail.com") is True
    assert is_excluded_email("noreply@company.com") is True
    assert is_excluded_email("no-reply@domain.com") is True

    # Real recruiter & hiring emails should NOT be excluded
    assert is_excluded_email("john.doe@company.com") is False
    assert is_excluded_email("recruiter@company.com") is False
    assert is_excluded_email("sarah.recruiting@startup.io") is False
    assert is_excluded_email("alina.k.ibraimo@aubay.pt") is False


def test_generate_verified_role_patterns_is_disabled():
    """Ensure zero speculative or generated role patterns are produced."""
    patterns = generate_verified_role_patterns("datadoghq.com")
    assert patterns == []


def test_discover_hr_contacts_never_fabricates_role_patterns(monkeypatch):
    """When web crawler and dorking find nothing, no speculative emails are created."""
    monkeypatch.setattr("src.ingestion.hr_enrichment.scrape_website_for_hr_emails", lambda domain: [])
    monkeypatch.setattr("src.ingestion.hr_enrichment.search_dork_hr_contacts", lambda comp, dom: [])
    monkeypatch.setattr("src.ingestion.hr_enrichment.check_domain_has_mx", lambda dom: True)

    contacts = discover_hr_contacts_for_company("NonExistentCompany", "https://nonexistent.com")
    assert contacts == []


def test_extract_application_emails_filters_compliance_and_junk():
    raw_desc = """
    Apply for this role! For questions, contact recruiter@techcorp.com.
    Equal opportunity employer. For accommodation requests, email _Accommodation@techcorp.com.
    For GDPR matters contact data.privacy@techcorp.com.
    Do not reply to noreply@techcorp.com.
    """
    emails = extract_application_emails(raw_desc)
    assert "recruiter@techcorp.com" in emails
    assert "_Accommodation@techcorp.com" not in emails
    assert "data.privacy@techcorp.com" not in emails
    assert "noreply@techcorp.com" not in emails


def test_apply_to_job_selects_single_primary_email(monkeypatch):
    """Ensure apply_to_job uses only the top-ranked email, not all of them."""
    monkeypatch.setattr("config.settings.settings.auto_apply_mode", "draft")
    
    job = {
        "id": 123,
        "title": "Software Engineer",
        "company": "Acme",
        "raw_description": "Contact alice@acme.com or bob@acme.com or charlie@acme.com",
        "recruiter_pitch_en": "I am interested in this position.",
    }
    result = apply_to_job(job)
    assert result["application_method"] == "recruiter_email_draft"
    # Should only be a single email, not comma separated
    assert "," not in result["application_email"]
    assert result["application_email"] in ["alice@acme.com", "bob@acme.com", "charlie@acme.com"]


def test_is_bounce_message_detection():
    # From mailer-daemon
    msg1 = {"sender_email": "mailer-daemon@googlemail.com", "subject": "Delivery Status Notification (Failure)", "body": "550 5.1.1 Address not found"}
    assert _is_bounce_message(msg1) is True

    # From postmaster
    msg2 = {"sender_email": "postmaster@mail.outlook.com", "subject": "Undeliverable: Application", "body": "User unknown"}
    assert _is_bounce_message(msg2) is True

    # Real recruiter email
    msg3 = {"sender_email": "recruiter@company.com", "subject": "Re: Application — Senior Dev", "body": "Hello, we would love to schedule an interview."}
    assert _is_bounce_message(msg3) is False


def test_extract_bounced_recipient():
    # Standard DSN pattern
    body_dsn = """
    This is an automatically generated Delivery Status Notification.
    Final-Recipient: rfc822; fake.recruiter@nonexistentdomain.com
    Action: failed
    Status: 5.1.1
    """
    msg = {"body": body_dsn, "subject": "Delivery Status Notification"}
    assert _extract_bounced_recipient(msg) == "fake.recruiter@nonexistentdomain.com"

    # Gmail text pattern
    body_gmail = """
    Your message wasn't delivered to recruiting@smartasset.com because the address couldn't be found, or is unable to receive mail.
    """
    msg_gmail = {"body": body_gmail, "subject": "Delivery Status Notification (Failure)"}
    assert _extract_bounced_recipient(msg_gmail) == "recruiting@smartasset.com"

    # Matched job fallback
    mock_job = MagicMock()
    mock_job.application_email = "dead.address@test.com"
    body_generic = "The email account that you tried to reach does not exist: dead.address@test.com"
    msg_generic = {"body": body_generic, "subject": "Mail delivery failed"}
    assert _extract_bounced_recipient(msg_generic, mock_job) == "dead.address@test.com"
