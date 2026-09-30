from src.application.auto_apply import apply_to_job


def test_draft_application_has_no_external_message_id(monkeypatch):
    monkeypatch.setattr("config.settings.settings.auto_apply_mode", "draft")
    result = apply_to_job({
        "title": "Backend Engineer",
        "raw_description": "Send your CV to hiring@acme.com",
        "recruiter_pitch_en": "Hello, I am interested.",
        "job_url": "https://example.com/job",
    })
    assert result["application_method"] == "recruiter_email_draft"
    assert result["thread_subject"]
    assert result["message_id"] is None


def test_linkedin_job_is_marked_manual(monkeypatch):
    monkeypatch.setattr("config.settings.settings.auto_apply_mode", "send")
    result = apply_to_job({
        "title": "Backend Engineer",
        "raw_description": "Apply via LinkedIn.",
        "recruiter_pitch_en": "Hello, I am interested.",
        "job_url": "https://www.linkedin.com/jobs/view/123456789",
    })
    assert result["application_method"] == "linkedin_manual"
    assert result["application_status"] == "manual_linkedin"
    assert result["application_url"].startswith("https://www.linkedin.com/")
    assert result["applied"] is False


def test_direct_email_is_sendable_path(monkeypatch):
    monkeypatch.setattr("config.settings.settings.auto_apply_mode", "send")
    monkeypatch.setattr("config.settings.settings.outbound_send_enabled", False)
    result = apply_to_job({
        "title": "Backend Engineer",
        "raw_description": "Send your CV to hiring@acme.com",
        "recruiter_pitch_en": "Hello, I am interested.",
        "job_url": "https://example.com/job",
    })
    assert result["application_method"] == "recruiter_email_draft"
    assert result["application_email"] == "hiring@acme.com"
    assert result["application_status"] == "draft"
