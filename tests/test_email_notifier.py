from unittest.mock import MagicMock, patch
import pytest

from src.alerting.email_notifier import send_email_alert
from config.settings import settings


def test_email_alert_disabled_by_default():
    job = {
        "title": "Backend Engineer",
        "company": "Acme Inc",
        "location": "Paris, France",
        "match_score": 85,
        "visa_sponsorship_detected": True,
        "visa_status_notes": "Sponsorship offered",
        "key_skills_matched": ["Python", "Docker"],
        "job_url": "https://example.com/job/1",
    }
    with patch("config.settings.settings.email_alerts_enabled", False), \
         patch("config.settings.settings.smtp_host", "smtp.gmail.com"), \
         patch("config.settings.settings.smtp_user", "test@example.com"), \
         patch("config.settings.settings.smtp_password", "secret"), \
         patch("smtplib.SMTP") as mock_smtp:
        result = send_email_alert(job)
        assert result is False
        mock_smtp.assert_not_called()


def test_email_alert_never_defaults_to_smtp_user_when_alert_to_empty():
    job = {
        "title": "Backend Engineer",
        "company": "Acme Inc",
        "match_score": 90,
    }
    with patch("config.settings.settings.email_alerts_enabled", True), \
         patch("config.settings.settings.alert_email_to", ""), \
         patch("config.settings.settings.smtp_host", "smtp.gmail.com"), \
         patch("config.settings.settings.smtp_user", "myuser@gmail.com"), \
         patch("config.settings.settings.smtp_password", "secret"), \
         patch("smtplib.SMTP") as mock_smtp:
        result = send_email_alert(job)
        assert result is False
        mock_smtp.assert_not_called()


def test_email_alert_sends_only_when_explicitly_enabled_and_recipient_set():
    job = {
        "title": "Backend Engineer",
        "company": "Acme Inc",
        "match_score": 90,
    }
    with patch("config.settings.settings.email_alerts_enabled", True), \
         patch("config.settings.settings.alert_email_to", "alerts@target.com"), \
         patch("config.settings.settings.smtp_host", "smtp.gmail.com"), \
         patch("config.settings.settings.smtp_user", "myuser@gmail.com"), \
         patch("config.settings.settings.smtp_password", "secret"), \
         patch("smtplib.SMTP") as mock_smtp:
        server_instance = MagicMock()
        mock_smtp.return_value.__enter__.return_value = server_instance
        result = send_email_alert(job)
        assert result is True
        server_instance.sendmail.assert_called_once()
        args, kwargs = server_instance.sendmail.call_args
        assert args[1] == ["alerts@target.com"]
