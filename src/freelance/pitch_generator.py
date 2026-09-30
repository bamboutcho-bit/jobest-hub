"""AI-powered freelance pitch generator with multi-angle proposal engineering and offline fallback.

Generates high-converting, personalized project proposals tailored to each client's specific needs.
Includes 3-step technical blueprints, scope estimates, rate quotes, proof-of-work, and clear CTAs.
Automatically falls back to battle-tested proposal templates if LLM providers are offline.
"""
from __future__ import annotations

import json
import logging
from datetime import datetime, timedelta, timezone
from typing import Any

from config.settings import settings
from src.ai.service import AIService, AIProviderUnavailable, AIQuotaExceeded
from src.candidate.profile_manager import get_active_profile
from src.storage.db import get_session
from src.storage.models import FreelanceLead, FreelanceMessage, FreelanceStage

logger = logging.getLogger(__name__)

PROPOSAL_ANGLES = {
    "mvp_speed": "⚡ Speed & Rapid MVP: Rapid turnaround, working prototype in 7-14 days.",
    "architecture_quality": "🛡️ Production Quality & Scalable Architecture: Clean domain design, robustness, security.",
    "roi_advisory": "💡 High-ROI Solution Advisory: Business impact, cost reduction, technical audit.",
}


def _build_pitch_system_prompt(profile: dict[str, Any] | None = None, angle: str = "mvp_speed") -> str:
    """Build dynamic system prompt using the active candidate profile and selected proposal angle."""
    if profile is None:
        profile = get_active_profile()

    name = profile.get("name") or "Senior Developer"
    headline = profile.get("headline") or "Fullstack & Backend Software Engineer"
    stack = profile.get("core_stack") or ["Java", "Spring Boot", "React", "PostgreSQL", "Docker"]
    services = profile.get("freelance_services") or [
        "Custom MVP & Prototype Development",
        "Backend API Architecture & Implementation",
        "Full-Stack Web Application Engineering",
    ]
    daily_rate = profile.get("freelance_daily_eur") or profile.get("freelance_daily_rate") or 400
    hourly_rate = profile.get("freelance_hourly_usd") or profile.get("freelance_hourly_rate") or 50
    currency = profile.get("freelance_currency") or "EUR"

    if angle == "architecture_quality":
        angle_instructions = (
            "PROPOSAL ANGLE: 🛡️ PRODUCTION QUALITY & SCALABLE ARCHITECTURE.\n"
            "Emphasize clean domain-driven design, modular microservices, clean API boundaries, test coverage, and automated deployment.\n"
            "Position as a senior engineer who delivers production-grade software that scales effortlessly."
        )
    elif angle == "roi_advisory":
        angle_instructions = (
            "PROPOSAL ANGLE: 💡 HIGH-ROI SOLUTION ADVISORY & BUSINESS IMPACT.\n"
            "Emphasize saving development costs, lean milestone delivery, maximizing business impact, and business alignment.\n"
            "Position as a technical partner who protects the client's budget and prioritizes high-impact features."
        )
    else:  # mvp_speed default
        angle_instructions = (
            "PROPOSAL ANGLE: ⚡ SPEED & RAPID MVP.\n"
            "Emphasize delivering a working prototype within 7-14 days, fast sprint iterations, and agile communication.\n"
            "Position as a full-stack builder who turns ideas into deployed software rapidly."
        )

    return f"""You are an elite, top-rated freelance software engineer writing high-converting project proposals that win clients and outperform generic bids.

FREELANCER PROFILE:
Name: {name}
Headline: {headline}
Core Stack: {', '.join(stack)}
Primary Services: {', '.join(services)}
Standard Rates: {daily_rate} {currency}/day (~{hourly_rate} USD/hour)

{angle_instructions}

HIGH-CONVERTING PROPOSAL BLUEPRINT:
1. HOOK (Sentence 1-2): Acknowledge their project and show immediate understanding of the core problem or goal.
2. TECHNICAL BLUEPRINT (3-4 sentences): Lay out a concrete 3-step implementation roadmap:
   - Step 1: Architecture & Data Schema
   - Step 2: Core API / Business Logic & Frontend Flow
   - Step 3: Hardening, Automated Testing & Deployment
3. PROOF & RELEVANCE (2 sentences): Mention relevant experience with {', '.join(stack[:3])} solving similar problems.
4. ESTIMATED TIMELINE & INVESTMENT (1-2 sentences): Provide a ballpark duration and standard rate ({daily_rate} {currency}/day).
5. LOW-FRICTION CALL TO ACTION (1 sentence): Propose an async review or brief 10-minute discovery chat with zero obligation.

TONE GUIDELINES:
- No generic agency fluff ("We are a team of passionate developers...").
- Write in first person ("I can build this...", "My recommendation is...").
- Be confident, technical, and directly solution-oriented.

Return ONLY valid JSON:
{{
  "subject": "Clear, compelling subject line citing project title and angle",
  "pitch_body": "Full tailored pitch following the 5-point blueprint above"
}}
"""


def _build_followup_prompt(profile: dict[str, Any] | None = None) -> str:
    """Build dynamic follow-up prompt using the active candidate profile."""
    if profile is None:
        profile = get_active_profile()

    name = profile.get("name") or "Senior Developer"
    headline = profile.get("headline") or "Fullstack Software Engineer"
    return f"""You write polite, value-adding follow-up messages for freelance proposals.

FREELANCER: {name} ({headline})

Rules:
- Follow-up #1 (3 days after pitch): Add value — share a quick technical insight or architectural tip for their project. Keep it short (3-4 sentences).
- Follow-up #2 (7 days after pitch): Gentle check-in. Acknowledge they are busy. Offer to keep the roadmap on file for future sprints.

Return ONLY valid JSON:
{{
  "subject": "Quick follow-up regarding [Project Name]",
  "body": "[follow-up message]\\n\\nBest regards,\\n{name}"
}}
"""


def _parse_json(text: str) -> dict:
    """Parse AI JSON output, stripping markdown fences if present."""
    cleaned = text.strip()
    if cleaned.startswith("```"):
        cleaned = cleaned.strip("`").removeprefix("json").strip()
    return json.loads(cleaned)


class ProposalDraft(dict):
    """Dictionary representing a proposal draft that also supports 2-tuple unpacking (subject, pitch_body)."""

    def __iter__(self):
        yield self.get("subject", "")
        yield self.get("pitch_body", "")


def generate_offline_pitch(
    lead: FreelanceLead,
    profile: dict[str, Any] | None = None,
    angle: str = "mvp_speed",
) -> ProposalDraft:
    """Offline heuristic proposal generator that guarantees a high-converting pitch even without LLMs."""
    if profile is None:
        profile = get_active_profile()

    name = profile.get("name") or "Developer"
    headline = profile.get("headline") or "Fullstack Software Engineer"
    stack = profile.get("core_stack") or ["Java", "Spring Boot", "React", "PostgreSQL"]
    daily_rate = profile.get("freelance_daily_eur") or profile.get("freelance_daily_rate") or 400
    currency = profile.get("freelance_currency") or "EUR"

    client_name = (lead.client_name or "").strip()
    if not client_name or client_name.lower() in ("unknown", "reddit user", "none", "client"):
        greeting = "Hi there,"
    else:
        first_name = client_name.split()[0]
        greeting = f"Hi {first_name},"

    title = lead.title or "your project"
    primary_tech = ", ".join(stack[:3])

    calendar_url = profile.get("calendar_url") or settings.calendar_booking_url
    booking_call = f"\nFeel free to grab a quick 10-minute slot on my calendar: {calendar_url}\n" if calendar_url else ""

    if angle == "architecture_quality":
        body = (
            f"{greeting}\n\n"
            f"I saw your requirements for \"{title}\" and wanted to reach out with a direct technical roadmap. "
            f"Building this reliably requires a clean, modular architecture so you avoid technical debt as the platform scales.\n\n"
            f"Here is how I would structure delivery for you:\n"
            f"• Step 1 (Foundation & Schema): Clean API boundaries, database migrations ({stack[0] if stack else 'SQL'}), and secure authentication.\n"
            f"• Step 2 (Core Business Logic): High-performance endpoints, responsive UI integration, and input validation workflows.\n"
            f"• Step 3 (Hardening & Launch): Automated test coverage, containerized Docker deployment, and production monitoring.\n\n"
            f"Having architected enterprise and production applications using {primary_tech}, I focus on writing robust, maintainable code you can easily scale.\n\n"
            f"My typical rate is {daily_rate} {currency}/day, and I can deliver a solid first milestone in 5 to 7 business days.\n\n"
            f"Would you be open to a quick 10-minute async chat or call this week to review the technical blueprint?{booking_call}\n\n"
            f"Best regards,\n{name}\n{headline}"
        )
    elif angle == "roi_advisory":
        body = (
            f"{greeting}\n\n"
            f"I came across your posting regarding \"{title}\" and wanted to share a pragmatic approach to getting this built cost-effectively.\n\n"
            f"Rather than over-engineering upfront, we can execute this in 3 focused iterations:\n"
            f"1. Core MVP Scope: Build only the essential user workflows that prove value immediately.\n"
            f"2. Seamless Integration: Connect {primary_tech} services with zero latency bottlenecks and clean error handling.\n"
            f"3. Deployment & Handoff: Fully automated deployment with thorough documentation and knowledge transfer.\n\n"
            f"With extensive hands-on experience in {primary_tech}, I deliver fast, predictable turnarounds at {daily_rate} {currency}/day.\n\n"
            f"Let me know if you'd like to do a quick 10-minute kickoff chat to iron out the scope.{booking_call}\n\n"
            f"Best,\n{name}\n{headline}"
        )
    else:  # mvp_speed default
        body = (
            f"{greeting}\n\n"
            f"I came across \"{title}\" and can help you bring this to production quickly and cleanly.\n\n"
            f"Here is a streamlined 3-step roadmap to get you live:\n"
            f"1. Sprint 1 (Days 1–3): Core backend data models, REST endpoints, and security schema.\n"
            f"2. Sprint 2 (Days 4–7): Modern UI components, responsive layout, and end-to-end API integration.\n"
            f"3. Sprint 3 (Days 8–10): Cloud deployment, smoke testing, and final handoff.\n\n"
            f"I specialize in {primary_tech} development, moving from specification to working software rapidly with clear milestone updates.\n\n"
            f"My investment rate is {daily_rate} {currency}/day. Would you be available for a brief 10-minute call or chat to go over the details?{booking_call}\n\n"
            f"Best regards,\n{name}\n{headline}"
        )

    if angle == "architecture_quality":
        subject = f"Production Architecture & Technical Proposal: {title[:50]}"
    elif angle == "roi_advisory":
        subject = f"Pragmatic Roadmap & Engineering Proposal: {title[:50]}"
    else:
        subject = f"Fast MVP Technical Proposal: {title[:55]}"

    return ProposalDraft({
        "subject": subject,
        "pitch_body": body,
    })


def generate_linkedin_note(lead: FreelanceLead, profile: dict[str, Any] | None = None) -> str:
    """Generate a high-converting LinkedIn connection request note strictly under 300 characters."""
    if profile is None:
        profile = get_active_profile()

    name = profile.get("name") or "Developer"
    first_name_self = name.split()[0] if name and name.strip() else "Developer"
    stack = profile.get("core_stack") or ["Software"]
    primary_tech = stack[0] if stack else "Software"

    client_name = (lead.client_name or "").strip()
    if not client_name or client_name.lower() in ("unknown", "reddit user", "none", "client"):
        greeting = "Hi,"
    else:
        greeting = f"Hi {client_name.split()[0]},"

    title = (lead.title or "").strip()
    short_title = title[:32] + "..." if len(title) > 32 else title

    note = (
        f"{greeting} saw your post regarding {short_title}. "
        f"I'm a {primary_tech} specialist and would love to connect and share a few architectural thoughts on your build! "
        f"- {first_name_self}"
    )

    # Strict LinkedIn 300-char guarantee
    if len(note) > 298:
        note = (
            f"{greeting} saw your post on {short_title[:24]}. "
            f"I specialize in {primary_tech} and would love to connect to help with your build. - {first_name_self}"
        )
    if len(note) > 298:
        note = note[:295] + "..."
    return note


def generate_chat_dm(lead: FreelanceLead, profile: dict[str, Any] | None = None) -> str:
    """Generate a conversational 3-sentence DM suitable for Reddit chat, Twitter/X, Discord, or Slack."""
    if profile is None:
        profile = get_active_profile()

    name = profile.get("name") or "Developer"
    first_name_self = name.split()[0] if name and name.strip() else "Developer"
    headline = profile.get("headline") or "Fullstack Engineer"
    stack = profile.get("core_stack") or ["Java", "Spring Boot", "React"]
    primary_tech = ", ".join(stack[:2])
    calendar_url = profile.get("calendar_url") or settings.calendar_booking_url

    client_name = (lead.client_name or "").strip()
    if not client_name or client_name.lower() in ("unknown", "reddit user", "none", "client"):
        greeting = "Hey there,"
    else:
        greeting = f"Hey {client_name.split()[0]},"

    title = lead.title or "your project"
    cta_part = f"grab 10 mins on my calendar ({calendar_url})" if calendar_url else "let me know if you're open for a quick 10-minute async chat"

    dm = (
        f"{greeting}\n\n"
        f"I came across your post about \"{title}\" and wanted to reach out directly. "
        f"I build production applications in {primary_tech} and have delivered several similar MVPs with clean architecture and fast turnarounds.\n\n"
        f"Happy to share an outline of how I'd approach this—{cta_part} to compare notes!\n\n"
        f"Best,\n{first_name_self} ({headline})"
    )
    return dm


def get_all_pitch_formats(lead_id: int) -> dict[str, Any]:
    """Retrieve or generate all outreach formats for a lead (Email, LinkedIn Note, Chat DM, Follow-ups)."""
    with get_session() as session:
        lead = session.get(FreelanceLead, lead_id)
        if not lead:
            return {}

        profile = get_active_profile(user_id=lead.user_id, session=session)

        pitch_sub = lead.pitch_subject
        pitch_bod = lead.pitch_body
        if not pitch_bod:
            draft = generate_offline_pitch(lead, profile, angle="mvp_speed")
            pitch_sub = draft.get("subject")
            pitch_bod = draft.get("pitch_body")

        linkedin = generate_linkedin_note(lead, profile)
        chat = generate_chat_dm(lead, profile)

        fu1_msg = session.query(FreelanceMessage).filter(
            FreelanceMessage.lead_id == lead_id,
            FreelanceMessage.message_type == "follow_up_1",
        ).order_by(FreelanceMessage.created_at.desc()).first()

        fu2_msg = session.query(FreelanceMessage).filter(
            FreelanceMessage.lead_id == lead_id,
            FreelanceMessage.message_type == "follow_up_2",
        ).order_by(FreelanceMessage.created_at.desc()).first()

        return {
            "lead_id": lead.id,
            "title": lead.title,
            "client_name": lead.client_name,
            "source_platform": lead.source_platform,
            "email_proposal": {
                "subject": pitch_sub,
                "body": pitch_bod,
            },
            "linkedin_note": {
                "body": linkedin,
                "char_count": len(linkedin),
                "limit": 300,
            },
            "chat_dm": {
                "body": chat,
                "word_count": len(chat.split()),
            },
            "follow_up_1": {
                "subject": fu1_msg.subject if fu1_msg else f"Quick architecture thought regarding {lead.title}",
                "body": fu1_msg.body if fu1_msg else None,
                "sent_at": fu1_msg.sent_at.isoformat() if fu1_msg and fu1_msg.sent_at else None,
            },
            "follow_up_2": {
                "subject": fu2_msg.subject if fu2_msg else f"Following up — {lead.title}",
                "body": fu2_msg.body if fu2_msg else None,
                "sent_at": fu2_msg.sent_at.isoformat() if fu2_msg and fu2_msg.sent_at else None,
            },
            "calendar_url": profile.get("calendar_url") or settings.calendar_booking_url,
        }


def generate_pitch(lead_id: int, angle: str = "mvp_speed") -> str:
    """Generate a tailored pitch for a freelance lead using AI with offline heuristic fallback.

    Returns: 'generated', 'already_done', 'error', 'quota_exceeded'.
    """
    with get_session() as session:
        lead = session.get(FreelanceLead, lead_id)
        if lead is None:
            return "not_found"
        if lead.pitch_status == "sent":
            return "already_sent"

        profile = get_active_profile(user_id=lead.user_id, session=session)
        description = (lead.raw_description or "")[:2500]
        user_prompt = (
            f"CLIENT PROJECT:\n"
            f"Title: {lead.title}\n"
            f"Client: {lead.client_name} ({lead.client_type or 'unknown'})\n"
            f"Source: {lead.source_platform}\n"
            f"Budget hint: {lead.budget_estimate or 'not specified'}\n"
            f"Scope: {lead.project_scope or 'not assessed yet'}\n\n"
            f"Project description:\n{description}\n"
        )

        system_prompt = _build_pitch_system_prompt(profile, angle=angle)
        result = None

        try:
            result = AIService(session=session).generate(
                system_prompt=system_prompt,
                user_prompt=user_prompt,
                max_tokens=800,
                parse=_parse_json,
            )
        except AIQuotaExceeded:
            logger.info("AI quota exceeded for lead %d; using heuristic proposal engine", lead_id)
            result = generate_offline_pitch(lead, profile, angle=angle)
        except (AIProviderUnavailable, Exception) as exc:
            logger.info("AI unavailable for lead %d (%s); using heuristic proposal engine", lead_id, exc)
            result = generate_offline_pitch(lead, profile, angle=angle)

        if not result or not isinstance(result, dict):
            result = generate_offline_pitch(lead, profile, angle=angle)

        lead.pitch_subject = str(result.get("subject", f"Re: {lead.title}"))[:998]
        lead.pitch_body = str(result.get("pitch_body", ""))
        lead.pitch_status = "draft"
        lead.updated_at = datetime.now(timezone.utc)

        # Store the pitch as a message record
        session.add(FreelanceMessage(
            lead_id=lead.id,
            direction="outbound",
            message_type="pitch",
            subject=lead.pitch_subject,
            body=lead.pitch_body,
        ))

        logger.info("Pitch successfully generated for lead %d (%s, angle=%s)", lead_id, lead.title, angle)
        return "generated"


def generate_follow_up(lead_id: int, follow_up_number: int = 1) -> str:
    """Generate a value-first follow-up message for a pitched lead with offline fallback.

    Returns: 'generated', 'not_eligible', 'error', 'max_follow_ups_reached', 'not_found'.
    """
    with get_session() as session:
        lead = session.get(FreelanceLead, lead_id)
        if lead is None:
            return "not_found"
        if lead.stage not in (FreelanceStage.PITCHED, FreelanceStage.EVALUATED) and lead.pitch_status not in ("draft", "sent"):
            return "not_eligible"
        if lead.follow_up_count >= settings.freelance_max_follow_ups:
            return "max_followups_reached"

        profile = get_active_profile(user_id=lead.user_id, session=session)
        name = profile.get("name") or "Developer"
        stack = profile.get("core_stack") or ["Java", "Spring Boot", "React"]
        primary_tech = ", ".join(stack[:2])
        description = (lead.raw_description or "")[:1500]

        client_name = (lead.client_name or "").strip()
        if not client_name or client_name.lower() in ("unknown", "reddit user", "none", "client"):
            greeting_name = "there"
        else:
            greeting_name = client_name.split()[0]

        user_prompt = (
            f"CONTEXT: This is follow-up #{follow_up_number} for a pitch sent to {client_name or 'client'}.\n"
            f"Original project: {lead.title}\n"
            f"Original pitch subject: {lead.pitch_subject}\n"
            f"Project description: {description}\n\n"
            f"Generate follow-up #{follow_up_number}."
        )

        system_prompt = _build_followup_prompt(profile)
        result = None

        try:
            result = AIService(session=session).generate(
                system_prompt=system_prompt,
                user_prompt=user_prompt,
                max_tokens=400,
                parse=_parse_json,
            )
        except Exception as exc:
            logger.info("AI follow-up unavailable (%s); using offline template", exc)
            if follow_up_number == 1:
                result = {
                    "subject": f"Quick technical thought on {lead.title[:45]}",
                    "body": (
                        f"Hi {greeting_name},\n\n"
                        f"I wanted to follow up briefly regarding \"{lead.title}\".\n\n"
                        f"While reviewing the scope from an architecture perspective, I had a quick idea for structuring the {primary_tech} layer: "
                        f"we can leverage an asynchronous event model or cached endpoints to significantly reduce server costs "
                        f"and ensure lightning-fast response times from Day 1.\n\n"
                        f"Whenever you have a moment this week, I'd be happy to share a quick 2-minute technical outline.\n\n"
                        f"Best regards,\n{name}"
                    ),
                }
            else:
                result = {
                    "subject": f"Checking in regarding {lead.title[:45]}",
                    "body": (
                        f"Hi {greeting_name},\n\n"
                        f"I know you're busy navigating sprint priorities, so I'll keep this brief.\n\n"
                        f"Just wanted to see if you are still looking to bring on engineering help for \"{lead.title}\".\n\n"
                        f"If the timing isn't right now, no worries at all — I'll keep the roadmap on file. Feel free to reach out whenever you're ready to kick off development!\n\n"
                        f"Best regards,\n{name}"
                    ),
                }

        subject = str(result.get("subject", f"Following up — {lead.title}"))[:998]
        body = str(result.get("body", ""))

        session.add(FreelanceMessage(
            lead_id=lead.id,
            direction="outbound",
            message_type=f"follow_up_{follow_up_number}",
            subject=subject,
            body=body,
        ))

        lead.follow_up_count += 1
        lead.last_contact_at = datetime.now(timezone.utc)
        if lead.follow_up_count >= settings.freelance_max_follow_ups:
            lead.next_follow_up_due = None
        else:
            lead.next_follow_up_due = datetime.now(timezone.utc) + timedelta(days=settings.freelance_follow_up_2_days)

        lead.updated_at = datetime.now(timezone.utc)

        logger.info("Follow-up #%d generated for lead %d", follow_up_number, lead_id)
        return "generated"
