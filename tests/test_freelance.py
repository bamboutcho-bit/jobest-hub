"""Tests for the Freelance & Client Acquisition Engine."""
from __future__ import annotations

import hashlib
from datetime import datetime, timedelta, timezone

try:
    import pytest
except ImportError:
    pytest = None

from src.freelance.discovery import (
    _clean_html,
    _dedup_hash,
    _extract_email,
    _has_hiring_signal,
    _lead,
    _skill_match_count,
)
from src.storage.models import FreelanceLead, FreelanceMessage, FreelanceStage


# ---------------------------------------------------------------------------
# Discovery helpers
# ---------------------------------------------------------------------------

class TestCleanHtml:
    def test_strips_tags(self):
        assert _clean_html("<p>Hello <b>world</b></p>") == "Hello world"

    def test_handles_none(self):
        assert _clean_html(None) == ""

    def test_unescapes_entities(self):
        assert _clean_html("a &amp; b") == "a & b"


class TestDedupHash:
    def test_deterministic(self):
        h1 = _dedup_hash("hackernews", "12345")
        h2 = _dedup_hash("hackernews", "12345")
        assert h1 == h2

    def test_different_sources(self):
        h1 = _dedup_hash("hackernews", "12345")
        h2 = _dedup_hash("reddit", "12345")
        assert h1 != h2

    def test_md5_format(self):
        h = _dedup_hash("test", "abc")
        assert len(h) == 32
        assert all(c in "0123456789abcdef" for c in h)


class TestExtractEmail:
    def test_finds_email(self):
        assert _extract_email("Contact me at john@example.com for details") == "john@example.com"

    def test_skips_noreply(self):
        assert _extract_email("Email noreply@example.com") is None

    def test_returns_none_for_no_email(self):
        assert _extract_email("No email here") is None

    def test_returns_none_for_empty(self):
        assert _extract_email("") is None
        assert _extract_email(None) is None


class TestHiringSignal:
    def test_detects_hiring(self):
        assert _has_hiring_signal("We are looking for a developer to build our MVP")
        assert _has_hiring_signal("[Hiring] React Developer needed")
        assert _has_hiring_signal("Startup seeking freelance backend engineer")

    def test_no_false_positive(self):
        assert not _has_hiring_signal("Just another blog post about cooking")


class TestSkillMatchCount:
    def test_counts_skills(self):
        text = "We need a Java Spring Boot developer with React and Docker experience"
        count = _skill_match_count(text)
        assert count >= 4  # java, spring boot, react, docker

    def test_no_skills(self):
        assert _skill_match_count("Looking for a plumber") == 0


class TestLeadNormalization:
    def test_creates_lead_dict(self):
        lead = _lead(
            title="Build an MVP",
            client_name="John Doe",
            client_type="founder",
            contact_email="john@example.com",
            contact_url="https://example.com",
            source_platform="hackernews",
            source_url="https://news.ycombinator.com/item?id=123",
            raw_description="Need a Java developer to build an MVP",
            dedup_id="123",
        )
        assert lead["title"] == "Build an MVP"
        assert lead["client_name"] == "John Doe"
        assert lead["client_type"] == "founder"
        assert lead["contact_email"] == "john@example.com"
        assert lead["source_platform"] == "hackernews"
        assert lead["dedup_hash"] == _dedup_hash("hackernews", "123")

    def test_truncates_long_title(self):
        lead = _lead(
            title="A" * 600,
            client_name="Test",
            client_type="individual",
            contact_email=None,
            contact_url=None,
            source_platform="test",
            source_url="",
            raw_description="",
            dedup_id="trunc",
        )
        assert len(lead["title"]) <= 500


# ---------------------------------------------------------------------------
# Model tests
# ---------------------------------------------------------------------------

class TestFreelanceModels:
    def test_freelance_stage_values(self):
        assert FreelanceStage.DISCOVERED.value == "discovered"
        assert FreelanceStage.PITCHED.value == "pitched"
        assert FreelanceStage.DEAL_WON.value == "deal_won"
        assert FreelanceStage.LOST.value == "lost"

    def test_all_stages_present(self):
        expected = {"discovered", "evaluated", "pitched", "in_discussion",
                    "offer_received", "deal_won", "lost"}
        actual = {s.value for s in FreelanceStage}
        assert actual == expected


# ---------------------------------------------------------------------------
# Dashboard UI & Template tests
# ---------------------------------------------------------------------------

class TestDashboardUI:
    def test_template_exists(self):
        from pathlib import Path
        template_file = Path("src/dashboard/templates/dashboard.html")
        assert template_file.exists()
        content = template_file.read_text(encoding="utf-8")
        assert "AutoHunt Pro" in content
        assert "kanbanBoard" in content
        assert "leadModal" in content
        assert "jobModal" in content
        assert "drag-and-drop" in content.lower() or "dragover" in content.lower()

    def test_render_template_replaces_default_tab(self):
        from src.dashboard.app import _render_template
        html_jobs = _render_template("jobs")
        assert "AutoHunt Pro" in html_jobs
        html_freelance = _render_template("freelance")
        assert "AutoHunt Pro" in html_freelance
