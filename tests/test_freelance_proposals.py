"""Comprehensive unit tests for Freelance Proposal Generation and Heuristic Lead Evaluation.

Tests multi-angle proposal drafting (MVP Speed, Robust Architecture, ROI Advisory),
offline high-converting proposal generation, dynamic profile injection, and
heuristic evaluation cascading.
"""
from __future__ import annotations

import unittest
from unittest.mock import MagicMock, patch

from src.candidate.profile_manager import DEFAULT_STATIC_PROFILE
from src.freelance.evaluation import (
    _build_evaluation_prompt,
    evaluate_lead,
    evaluate_lead_heuristic,
)
from src.freelance.pitch_generator import (
    PROPOSAL_ANGLES,
    _build_followup_prompt,
    _build_pitch_system_prompt,
    generate_chat_dm,
    generate_linkedin_note,
    generate_offline_pitch,
    generate_pitch,
    get_all_pitch_formats,
)
from src.storage.models import FreelanceLead, FreelanceStage

TEST_CANDIDATE_PROFILE = {
    "id": 1,
    "name": "Hamza Oukhouya",
    "headline": "Fullstack Software Engineer",
    "is_active": True,
    "current_location": "Remote",
    "target_locations": ["Remote", "France", "Europe"],
    "target_titles": ["Software Engineer", "Backend Engineer", "Java Developer"],
    "core_stack": ["Java", "Spring Boot", "React", "Docker", "PostgreSQL"],
    "keywords": ["Microservices", "REST API", "CI/CD"],
    "negative_keywords": [],
    "experience_years": 3,
    "visa_requirement": "Remote EU/Global",
    "languages": {"French": "Fluent", "English": "Fluent"},
    "resume_text": "Experienced software engineer specializing in Java, Spring Boot microservices.",
    "resume_path": "",
    "freelance_services": ["Backend Engineering", "Fullstack Development"],
    "freelance_hourly_usd": 50,
    "freelance_daily_eur": 400,
    "freelance_currency": "EUR",
    "calendar_url": "https://cal.com/hamza-oukhouya",
    "degree_level": "bachelor",
    "target_experience_level": "entry",
}


class TestPitchSystemPrompt(unittest.TestCase):
    def setUp(self):
        self.profile = {
            "name": "Alex Tech",
            "headline": "Senior Full-Stack Architect",
            "core_stack": ["Python", "FastAPI", "React", "PostgreSQL"],
            "languages": {"English": "Fluent", "German": "Conversational"},
            "freelance_services": ["Full-Stack Web App Development", "High-Performance APIs"],
            "freelance_daily_eur": 450,
            "freelance_hourly_usd": 65,
            "freelance_currency": "EUR",
            "visa_requirement": "Remote EU/Global",
            "resume_text": "Built fintech platforms with 99.99% uptime.",
        }

    def test_prompt_includes_profile_metadata(self):
        prompt = _build_pitch_system_prompt(self.profile, angle="mvp_speed")
        self.assertIn("Alex Tech", prompt)
        self.assertIn("Senior Full-Stack Architect", prompt)
        self.assertIn("Python, FastAPI, React, PostgreSQL", prompt)
        self.assertIn("450 EUR", prompt)
        self.assertIn("Full-Stack Web App Development", prompt)

    def test_prompt_adapts_to_angles(self):
        p_speed = _build_pitch_system_prompt(self.profile, angle="mvp_speed")
        self.assertIn("⚡ SPEED & RAPID MVP", p_speed)
        self.assertIn("working prototype within 7-14 days", p_speed)

        p_arch = _build_pitch_system_prompt(self.profile, angle="architecture_quality")
        self.assertIn("🛡️ PRODUCTION QUALITY & SCALABLE ARCHITECTURE", p_arch)
        self.assertIn("clean domain-driven design", p_arch)

        p_roi = _build_pitch_system_prompt(self.profile, angle="roi_advisory")
        self.assertIn("💡 HIGH-ROI SOLUTION ADVISORY", p_roi)
        self.assertIn("business impact", p_roi)

    def test_followup_prompt_references_profile(self):
        fu_prompt = _build_followup_prompt(self.profile)
        self.assertIn("Alex Tech", fu_prompt)
        self.assertIn("Senior Full-Stack Architect", fu_prompt)


class TestOfflinePitchGenerator(unittest.TestCase):
    def setUp(self):
        self.mock_lead = MagicMock(spec=FreelanceLead)
        self.mock_lead.id = 42
        self.mock_lead.title = "Need Java Spring Boot developer for B2B portal MVP"
        self.mock_lead.client_name = "Sarah Connor"
        self.mock_lead.client_type = "startup founder"
        self.mock_lead.source_platform = "reddit"
        self.mock_lead.description = (
            "We need a backend specialist to build a secured REST API in Spring Boot and React frontend. "
            "Timeline is 3 weeks. Ready to start immediately."
        )
        self.mock_lead.budget_estimate = "3000 - 5000 EUR"
        self.mock_lead.match_score = 88
        self.profile = dict(TEST_CANDIDATE_PROFILE)

    def test_offline_pitch_structure_mvp_speed(self):
        subject, body = generate_offline_pitch(self.mock_lead, self.profile, angle="mvp_speed")
        self.assertTrue(len(subject) > 10)
        self.assertIn("MVP", subject)
        # Verify 5 essential sections
        self.assertIn("Hi Sarah,", body)
        self.assertIn("Sprint 1", body)
        self.assertIn("Sprint 2", body)
        self.assertIn("Sprint 3", body)
        self.assertIn("investment rate is", body)
        self.assertIn("Hamza Oukhouya", body)

    def test_offline_pitch_structure_architecture_quality(self):
        subject, body = generate_offline_pitch(self.mock_lead, self.profile, angle="architecture_quality")
        self.assertIn("Architecture", subject)
        self.assertIn("clean, modular architecture", body)
        self.assertIn("Step 1", body)

    def test_offline_pitch_structure_roi_advisory(self):
        subject, body = generate_offline_pitch(self.mock_lead, self.profile, angle="roi_advisory")
        self.assertIn("Roadmap", subject)
        self.assertIn("pragmatic approach", body)
        self.assertIn("Core MVP Scope", body)

    def test_offline_pitch_client_name_fallback(self):
        self.mock_lead.client_name = None
        _, body = generate_offline_pitch(self.mock_lead, self.profile, angle="mvp_speed")
        self.assertIn("Hi there,", body)


class TestHeuristicLeadEvaluation(unittest.TestCase):
    def setUp(self):
        self.profile = dict(TEST_CANDIDATE_PROFILE)

    def test_detects_spam_and_unpaid_leads(self):
        lead = MagicMock(spec=FreelanceLead)
        lead.title = "Build me the next Uber for free (equity only)"
        lead.description = "Unpaid internship with future revenue share. Zero budget right now."
        lead.budget_estimate = None

        result = evaluate_lead_heuristic(lead, self.profile)
        self.assertTrue(result["spam"])
        self.assertEqual(result["match_score"], 0)
        self.assertEqual(result["action"], "ignore")
        self.assertIn("unpaid", result["reason"].lower())

    def test_high_skill_match_and_budget_detection(self):
        lead = MagicMock(spec=FreelanceLead)
        lead.title = "Fullstack Java Spring Boot & React Developer for SaaS MVP"
        lead.raw_description = (
            "We are looking for an experienced engineer to build a Dockerized REST API with "
            "Spring Boot, PostgreSQL, and a React frontend. Budget is $4,000 fixed price. "
            "Must be ready to begin next Monday."
        )
        lead.budget_estimate = None

        result = evaluate_lead_heuristic(lead, self.profile)
        self.assertFalse(result["spam"])
        self.assertGreaterEqual(result["match_score"], 70)
        self.assertEqual(result["action"], "pitch")
        self.assertIn("$4,000", result["budget_estimate"])

    def test_general_evaluation_prompt_construction(self):
        prompt = _build_evaluation_prompt(self.profile)
        self.assertIn("Hamza Oukhouya", prompt)
        self.assertIn("Java, Spring Boot", prompt)
        self.assertIn("Return ONLY valid JSON", prompt)


class TestGeneratePitchCascade(unittest.TestCase):
    @patch("src.freelance.pitch_generator.get_session")
    @patch("src.freelance.pitch_generator.get_active_profile")
    @patch("src.freelance.pitch_generator.AIService")
    def test_cascades_to_offline_generator_on_ai_failure(self, mock_ai_cls, mock_profile_fn, mock_session_fn):
        mock_profile_fn.return_value = dict(TEST_CANDIDATE_PROFILE)
        mock_ai_instance = MagicMock()
        mock_ai_instance.generate.side_effect = RuntimeError("Ollama connection refused")
        mock_ai_cls.return_value = mock_ai_instance

        # Mock DB session and lead
        mock_session = MagicMock()
        mock_lead = FreelanceLead(
            id=101,
            title="Spring Boot Microservices Refactoring",
            client_name="David Clark",
            client_type="CTO",
            source_platform="jobicy",
            raw_description="Migrate our monolith to Spring Boot microservices with Docker.",
            stage=FreelanceStage.DISCOVERED,
        )
        mock_session.get.return_value = mock_lead
        mock_session.__enter__.return_value = mock_session
        mock_session_fn.return_value = mock_session

        status = generate_pitch(101, angle="architecture_quality")

        self.assertEqual(status, "generated")
        self.assertIsNotNone(mock_lead.pitch_subject)
        self.assertIn("Architecture", mock_lead.pitch_subject)
        self.assertIn("Step 1", mock_lead.pitch_body)
        self.assertIn("Hamza Oukhouya", mock_lead.pitch_body)


class TestSmartFollowUpCadence(unittest.TestCase):
    @patch("src.freelance.pitch_generator.get_session")
    @patch("src.freelance.pitch_generator.get_active_profile")
    @patch("src.freelance.pitch_generator.AIService")
    def test_follow_up_1_generation_and_scheduling(self, mock_ai_cls, mock_profile_fn, mock_session_fn):
        from datetime import datetime, timezone
        from src.freelance.pitch_generator import generate_follow_up
        from src.storage.models import FreelanceMessage

        mock_profile_fn.return_value = dict(TEST_CANDIDATE_PROFILE)
        mock_ai_instance = MagicMock()
        mock_ai_instance.generate.side_effect = RuntimeError("Offline fallback")
        mock_ai_cls.return_value = mock_ai_instance

        mock_session = MagicMock()
        mock_lead = FreelanceLead(
            id=202,
            title="Need Backend Architect for Payments Service",
            client_name="Sarah Connor",
            client_type="Founder",
            source_platform="reddit",
            raw_description="Build secured payments API.",
            stage=FreelanceStage.PITCHED,
            pitch_body="Original proposal text...",
            follow_up_count=0,
            last_contact_at=datetime.now(timezone.utc),
        )
        mock_session.get.return_value = mock_lead
        mock_session.query.return_value.filter.return_value.first.return_value = None
        mock_session.__enter__.return_value = mock_session
        mock_session_fn.return_value = mock_session

        status = generate_follow_up(202, follow_up_number=1)

        self.assertEqual(status, "generated")
        self.assertEqual(mock_lead.follow_up_count, 1)
        self.assertIsNotNone(mock_lead.last_contact_at)
        self.assertIsNotNone(mock_lead.next_follow_up_due)
        self.assertGreater(mock_lead.next_follow_up_due, mock_lead.last_contact_at)

        # Verify a follow_up_1 message was saved
        saved_messages = [call[0][0] for call in mock_session.add.call_args_list if isinstance(call[0][0], FreelanceMessage)]
        self.assertEqual(len(saved_messages), 1)
        fu_msg = saved_messages[0]
        self.assertEqual(fu_msg.message_type, "follow_up_1")
        self.assertIn("architecture perspective", fu_msg.body)
        self.assertIn("Hamza Oukhouya", fu_msg.body)
        self.assertIn("Hi Sarah,", fu_msg.body)

    @patch("src.freelance.pitch_generator.get_session")
    @patch("src.freelance.pitch_generator.get_active_profile")
    @patch("src.freelance.pitch_generator.AIService")
    def test_follow_up_2_cadence_completion(self, mock_ai_cls, mock_profile_fn, mock_session_fn):
        from datetime import datetime, timezone
        from src.freelance.pitch_generator import generate_follow_up
        from src.storage.models import FreelanceMessage

        mock_profile_fn.return_value = dict(TEST_CANDIDATE_PROFILE)
        mock_ai_instance = MagicMock()
        mock_ai_instance.generate.side_effect = RuntimeError("Offline fallback")
        mock_ai_cls.return_value = mock_ai_instance

        mock_session = MagicMock()
        mock_lead = FreelanceLead(
            id=203,
            title="FastAPI & React Dashboard",
            client_name="Elena Rostova",
            client_type="CTO",
            source_platform="hackernews",
            stage=FreelanceStage.PITCHED,
            pitch_body="Initial proposal...",
            follow_up_count=1,
            last_contact_at=datetime.now(timezone.utc),
        )
        mock_session.get.return_value = mock_lead
        mock_session.query.return_value.filter.return_value.first.return_value = None
        mock_session.__enter__.return_value = mock_session
        mock_session_fn.return_value = mock_session

        status = generate_follow_up(203, follow_up_number=2)

        self.assertEqual(status, "generated")
        self.assertEqual(mock_lead.follow_up_count, 2)
        # Follow-up #2 is final cadence touchpoint -> next_follow_up_due is None
        self.assertIsNone(mock_lead.next_follow_up_due)

        saved_messages = [call[0][0] for call in mock_session.add.call_args_list if isinstance(call[0][0], FreelanceMessage)]
        self.assertEqual(len(saved_messages), 1)
        fu_msg = saved_messages[0]
        self.assertEqual(fu_msg.message_type, "follow_up_2")
        self.assertIn("roadmap on file", fu_msg.body)

    @patch("src.freelance.pitch_generator.get_session")
    def test_max_follow_up_limit_respected(self, mock_session_fn):
        from src.freelance.pitch_generator import generate_follow_up

        mock_session = MagicMock()
        mock_lead = FreelanceLead(
            id=204,
            title="Monolith Migration",
            stage=FreelanceStage.PITCHED,
            pitch_body="Pitch...",
            follow_up_count=2,  # Already at maximum (freelance_max_follow_ups = 2)
        )
        mock_session.get.return_value = mock_lead
        mock_session.__enter__.return_value = mock_session
        mock_session_fn.return_value = mock_session

        status = generate_follow_up(204)
        self.assertEqual(status, "max_followups_reached")

    def test_get_leads_needing_follow_up(self):
        from src.freelance.outreach import get_leads_needing_follow_up
        mock_session = MagicMock()
        mock_query = MagicMock()
        mock_query.filter.return_value.all.return_value = [(301,), (302,)]
        mock_session.query.return_value = mock_query

        res = get_leads_needing_follow_up(session=mock_session)
        self.assertEqual(res, [301, 302])


class TestZeroCostMultiChannelFormats(unittest.TestCase):
    def setUp(self):
        self.mock_lead = MagicMock(spec=FreelanceLead)
        self.mock_lead.id = 501
        self.mock_lead.title = "Looking for Senior Spring Boot Engineer to build real-time payment gateway"
        self.mock_lead.client_name = "David Miller"
        self.mock_lead.source_platform = "reddit"
        self.profile = {
            "name": "Hamza Oukhouya",
            "headline": "Java & React Engineer",
            "core_stack": ["Java", "Spring Boot", "React"],
            "freelance_daily_eur": 400,
            "freelance_currency": "EUR",
            "calendar_url": "https://cal.com/hamza/15min",
        }

    def test_linkedin_note_strictly_under_300_chars(self):
        note = generate_linkedin_note(self.mock_lead, self.profile)
        self.assertLessEqual(len(note), 300)
        self.assertIn("Hi David,", note)
        self.assertIn("Java", note)
        self.assertIn("Hamza", note)

    def test_linkedin_note_with_extreme_title(self):
        self.mock_lead.title = "A" * 300
        note = generate_linkedin_note(self.mock_lead, self.profile)
        self.assertLessEqual(len(note), 300)

    def test_chat_dm_structure_and_calendar(self):
        dm = generate_chat_dm(self.mock_lead, self.profile)
        self.assertIn("Hey David,", dm)
        self.assertIn("Java", dm)
        self.assertIn("https://cal.com/hamza/15min", dm)
        self.assertIn("Hamza", dm)

    def test_offline_pitch_injects_calendar_cta(self):
        draft = generate_offline_pitch(self.mock_lead, self.profile, angle="mvp_speed")
        body = draft.get("pitch_body")
        self.assertIn("https://cal.com/hamza/15min", body)


if __name__ == "__main__":
    unittest.main()
