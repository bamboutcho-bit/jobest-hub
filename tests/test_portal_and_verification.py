"""Tests for verification robot handling, strict confirmation, and LinkedIn/Indeed portal automation."""
from pathlib import Path
from unittest.mock import MagicMock

from config.settings import settings
from src.application.auto_apply import _candidate_application_url
from src.application.web_apply import (
    SUCCESS_MARKERS,
    _check_form_errors,
    _fill_and_submit_ats_form,
    _has_active_robot_challenge,
    _is_confirmation_url,
)


def test_success_markers_no_loose_words():
    """Loose single words must not be present to avoid false-positive confirmations."""
    forbidden = {"thank you", "merci", "confirmation", "received", "success", "submitted"}
    for word in forbidden:
        assert word not in SUCCESS_MARKERS, f"Loose word '{word}' found in SUCCESS_MARKERS"


def test_is_confirmation_url():
    assert _is_confirmation_url("https://jobs.lever.co/company/job/thank-you") is True
    assert _is_confirmation_url("https://boards.greenhouse.io/company/jobs/123/confirmation") is True
    assert _is_confirmation_url("https://company.workday.com/submitted") is True
    assert _is_confirmation_url("https://jobs.example.com/apply") is False
    assert _is_confirmation_url("https://jobs.example.com/challenge") is False
    assert _is_confirmation_url(None) is False


def test_candidate_application_url_priorities(monkeypatch):
    monkeypatch.setattr(settings, "auto_apply_web_enabled", True)
    monkeypatch.setattr(settings, "auto_apply_linkedin_enabled", True)
    monkeypatch.setattr(settings, "auto_apply_indeed_enabled", True)

    # 1. Direct ATS URL takes precedence
    ats_url = "https://boards.greenhouse.io/acme/jobs/12345"
    li_url = "https://www.linkedin.com/jobs/view/99999"
    res = _candidate_application_url(
        raw_description=f"Apply at {ats_url} or see {li_url}",
        direct_url=ats_url,
        source_url=li_url,
    )
    assert res == ats_url

    # 2. LinkedIn URL used when no direct ATS link exists and portal automation enabled
    res_li = _candidate_application_url(
        raw_description="",
        direct_url=None,
        source_url=li_url,
    )
    assert res_li == li_url

    # 3. If LinkedIn automation disabled, URL is skipped
    monkeypatch.setattr(settings, "auto_apply_linkedin_enabled", False)
    res_li_disabled = _candidate_application_url(
        raw_description="",
        direct_url=None,
        source_url=li_url,
    )
    assert res_li_disabled is None


def test_has_active_robot_challenge_detected():
    mock_page = MagicMock()
    mock_page.frames = []
    mock_body = MagicMock()
    mock_body.inner_text.return_value = "Please complete the security check to continue."
    mock_page.locator.return_value = mock_body

    active, msg = _has_active_robot_challenge(mock_page)
    assert active is True
    assert "security check" in msg.lower()


def test_has_active_robot_challenge_clean():
    mock_page = MagicMock()
    mock_page.frames = []
    mock_body = MagicMock()
    mock_body.inner_text.return_value = "Welcome to our careers page. Please fill the application below."
    mock_page.locator.return_value = mock_body

    active, msg = _has_active_robot_challenge(mock_page)
    assert active is False
    assert msg == ""


def test_form_errors_detected():
    mock_page = MagicMock()
    mock_loc = MagicMock()
    mock_loc.count.return_value = 1
    mock_el = MagicMock()
    mock_el.is_visible.return_value = True
    mock_el.inner_text.return_value = "This field is required: Email"
    mock_loc.nth.return_value = mock_el
    mock_page.locator.return_value = mock_loc

    err = _check_form_errors(mock_page)
    assert err is not None
    assert "required" in err.lower()


def test_ats_form_blocks_on_robot_challenge(monkeypatch, tmp_path):
    resume_file = tmp_path / "resume.pdf"
    resume_file.write_bytes(b"%PDF-test")

    mock_page = MagicMock()
    mock_page.url = "https://boards.greenhouse.io/acme/jobs/123"
    mock_page.frames = []

    def fake_locator(selector):
        loc = MagicMock()
        loc.count.return_value = 5
        loc.inner_text.return_value = "Verify you are human to continue"
        return loc

    mock_page.locator = fake_locator

    result = _fill_and_submit_ats_form(
        mock_page,
        job_record={"id": 1, "title": "Dev"},
        profile={"resume_path": str(resume_file)},
        resume=resume_file,
        body="Pitch",
    )

    assert result["applied"] is False
    assert result["application_status"] == "manual_security_challenge"
    assert "robot" in result["application_error"].lower() or "security" in result["application_error"].lower()
