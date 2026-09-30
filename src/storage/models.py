"""SQLAlchemy models for jobs, applications, email threads and live telemetry."""
import enum
from datetime import datetime, timezone

from sqlalchemy import Boolean, CheckConstraint, Column, DateTime, Enum, Float, ForeignKey, Index, Integer, String, Text, UniqueConstraint
from sqlalchemy.orm import declarative_base, relationship

Base = declarative_base()


class PipelineStage(str, enum.Enum):
    DISCOVERED = "discovered"
    EVALUATED_LOW = "evaluated_low"
    EVALUATED_MATCH = "evaluated_match"
    APPLIED = "applied"
    RESPONSE_RECEIVED = "response_received"
    INTERVIEW_REQUESTED = "interview_requested"
    INTERVIEW_SCHEDULED = "interview_scheduled"
    REJECTED = "rejected"
    OFFER_RECEIVED = "offer_received"
    OFFER_NEGOTIATING = "offer_negotiating"
    ACCEPTED = "accepted"
    WITHDRAWN = "withdrawn"


class JobPosting(Base):
    __tablename__ = "job_postings"

    id = Column(Integer, primary_key=True, autoincrement=True)
    user_id = Column(Integer, ForeignKey("users.id", ondelete="CASCADE"), nullable=True, index=True)
    dedup_hash = Column(String(32), nullable=False, index=True)
    source_site = Column(String(50), index=True)
    source_query = Column(String(120), nullable=True)
    source_sites = Column(Text, nullable=True)
    title = Column(String(255), index=True)
    company = Column(String(255), index=True)
    location = Column(String(255))
    is_remote = Column(Boolean, default=False, nullable=False)
    job_url = Column(Text)
    job_url_direct = Column(Text, nullable=True)
    application_url = Column(Text, nullable=True)
    company_url = Column(Text, nullable=True)
    application_emails = Column(Text, nullable=True)
    raw_description = Column(Text)
    date_posted = Column(String(50))
    scraped_at = Column(DateTime, default=lambda: datetime.now(timezone.utc), nullable=False)

    ai_relevance_score = Column(Integer, nullable=True)
    evaluated = Column(Boolean, default=False, nullable=False)
    evaluation_status = Column(String(32), default="pending", nullable=False)
    evaluation_reason = Column(Text, nullable=True)
    evaluation_attempted_at = Column(DateTime, nullable=True)
    evaluation_method = Column(String(30), nullable=True)
    match_score = Column(Integer, nullable=True)
    continent = Column(String(50), nullable=True, index=True)
    experience_level = Column(String(30), nullable=True, index=True)
    experience_years_required = Column(Integer, nullable=True)
    degree_required = Column(String(50), nullable=True)
    degree_matched = Column(Boolean, nullable=True)
    visa_category = Column(String(50), default="unspecified", nullable=True, index=True)
    relocation_detected = Column(Boolean, default=False, nullable=True)
    visa_sponsorship_detected = Column(Boolean, nullable=True)
    visa_status_notes = Column(Text, nullable=True)
    key_skills_matched = Column(Text, nullable=True)
    recruiter_pitch_fr = Column(Text, nullable=True)
    recruiter_pitch_en = Column(Text, nullable=True)
    application_subject = Column(String(998), nullable=True)
    application_email_body = Column(Text, nullable=True)

    alerted = Column(Boolean, default=False, nullable=False)
    outreach_generated = Column(Boolean, default=False, nullable=False)
    pipeline_stage = Column(Enum(PipelineStage), default=PipelineStage.DISCOVERED, nullable=False)

    application_email = Column(String(255), nullable=True)
    application_status = Column(String(50), default="not_attempted", nullable=False, index=True)
    application_error = Column(Text, nullable=True)
    application_attempted_at = Column(DateTime, nullable=True)
    resume_attached = Column(Boolean, default=False, nullable=False)
    application_screenshot_path = Column(Text, nullable=True)
    applied_at = Column(DateTime, nullable=True)
    application_method = Column(String(60), nullable=True)
    thread_subject = Column(String(998), nullable=True)
    last_contact_at = Column(DateTime, nullable=True)
    follow_up_count = Column(Integer, default=0, nullable=False)

    interview_datetime_utc = Column(DateTime, nullable=True)
    interview_notes = Column(Text, nullable=True)
    offer_details = Column(Text, nullable=True)
    offer_counter_draft = Column(Text, nullable=True)

    user = relationship("User", back_populates="jobs", foreign_keys=[user_id])
    email_events = relationship("EmailEvent", back_populates="job", cascade="all, delete-orphan")

    __table_args__ = (
        UniqueConstraint("user_id", "dedup_hash", name="uq_user_job_dedup"),
        Index("ix_job_company_title", "company", "title"),
        Index("ix_job_application_status", "application_status"),
    )

    def __repr__(self):
        return f"<JobPosting {self.company} - {self.title} ({self.match_score})>"


class EmailEvent(Base):
    __tablename__ = "email_events"

    id = Column(Integer, primary_key=True, autoincrement=True)
    job_id = Column(Integer, ForeignKey("job_postings.id"), nullable=False)
    direction = Column(String(10), nullable=False)
    message_id = Column(String(998), nullable=True)
    in_reply_to = Column(String(998), nullable=True)
    references = Column(Text, nullable=True)
    sender_email = Column(String(255), nullable=True)
    recipient_email = Column(String(255), nullable=True)
    subject = Column(String(998), nullable=True)
    body = Column(Text, nullable=True)
    classified_intent = Column(String(50), nullable=True)
    imap_uid = Column(String(64), nullable=True)
    email_type = Column(String(30), nullable=False, default="other")
    created_at = Column(DateTime, default=lambda: datetime.now(timezone.utc), nullable=False)

    job = relationship("JobPosting", back_populates="email_events")

    __table_args__ = (
        Index("ix_email_events_message_id", "message_id"),
        Index("ix_email_events_in_reply_to", "in_reply_to"),
        Index("ix_email_events_imap_uid", "imap_uid"),
    )


class ClaudeUsage(Base):
    """Legacy-compatible usage table; Ollama is the actual AI provider."""
    __tablename__ = "claude_usage"
    id = Column(Integer, primary_key=True, autoincrement=True)
    usage_date = Column(String(10), nullable=False, unique=True, index=True)
    calls = Column(Integer, default=0, nullable=False)
    updated_at = Column(DateTime, default=lambda: datetime.now(timezone.utc), nullable=False)


class OutboundMessage(Base):
    __tablename__ = "outbound_messages"
    id = Column(Integer, primary_key=True, autoincrement=True)
    user_id = Column(Integer, ForeignKey("users.id", ondelete="CASCADE"), nullable=True, index=True)
    idempotency_key = Column(String(255), nullable=False, unique=True, index=True)
    job_id = Column(Integer, ForeignKey("job_postings.id"), nullable=True)
    email_type = Column(String(30), nullable=False)
    recipient_email = Column(String(255), nullable=False, index=True)
    subject = Column(String(998), nullable=True)
    message_id = Column(String(998), nullable=True, index=True)
    status = Column(String(20), nullable=False, default="pending")
    failure_reason = Column(Text, nullable=True)
    created_at = Column(DateTime, default=lambda: datetime.now(timezone.utc), nullable=False)
    sent_at = Column(DateTime, nullable=True)
    updated_at = Column(DateTime, default=lambda: datetime.now(timezone.utc), nullable=False)

    user = relationship("User", foreign_keys=[user_id])
    job = relationship("JobPosting", foreign_keys=[job_id])

    __table_args__ = (
        Index("ix_outbound_messages_recipient_created", "recipient_email", "created_at"),
        Index("ix_outbound_messages_status_created", "status", "created_at"),
    )


class OutboundSuppression(Base):
    __tablename__ = "outbound_suppressions"
    id = Column(Integer, primary_key=True, autoincrement=True)
    user_id = Column(Integer, ForeignKey("users.id", ondelete="CASCADE"), nullable=True, index=True)
    recipient_email = Column(String(255), nullable=True, index=True)
    domain = Column(String(255), nullable=True, index=True)
    reason = Column(String(50), nullable=False)
    details = Column(Text, nullable=True)
    created_at = Column(DateTime, default=lambda: datetime.now(timezone.utc), nullable=False)

    user = relationship("User", foreign_keys=[user_id])


class PipelineRun(Base):
    __tablename__ = "pipeline_runs"
    id = Column(Integer, primary_key=True, autoincrement=True)
    user_id = Column(Integer, ForeignKey("users.id", ondelete="CASCADE"), nullable=True, index=True)
    started_at = Column(DateTime, default=lambda: datetime.now(timezone.utc), nullable=False)
    finished_at = Column(DateTime, nullable=True)
    updated_at = Column(DateTime, default=lambda: datetime.now(timezone.utc), nullable=False)
    status = Column(String(20), default="running", nullable=False)
    phase = Column(String(40), default="starting", nullable=False)
    progress_pct = Column(Integer, default=0, nullable=False)
    current_task = Column(Text, nullable=True)
    total_queries = Column(Integer, default=0, nullable=False)
    completed_queries = Column(Integer, default=0, nullable=False)
    raw_scraped_live = Column(Integer, default=0, nullable=False)
    new_postings_live = Column(Integer, default=0, nullable=False)
    evaluated_live = Column(Integer, default=0, nullable=False)
    applications_sent = Column(Integer, default=0, nullable=False)
    application_failures = Column(Integer, default=0, nullable=False)
    application_manual = Column(Integer, default=0, nullable=False)
    scrape_queries_json = Column(Text, nullable=True)

    raw_scraped = Column(Integer, default=0, nullable=False)
    scrape_errors = Column(Integer, default=0, nullable=False)
    new_postings = Column(Integer, default=0, nullable=False)
    evaluated = Column(Integer, default=0, nullable=False)
    claude_calls = Column(Integer, default=0, nullable=False)
    claude_skipped_prefilter = Column(Integer, default=0, nullable=False)
    claude_skipped_budget = Column(Integer, default=0, nullable=False)
    evaluation_failures = Column(Integer, default=0, nullable=False)
    anomaly_alerted = Column(Boolean, default=False, nullable=False)
    error = Column(Text, nullable=True)
    summary_json = Column(Text, nullable=True)

    user = relationship("User", back_populates="pipeline_runs", foreign_keys=[user_id])


class PipelineRunEvent(Base):
    __tablename__ = "pipeline_run_events"
    id = Column(Integer, primary_key=True, autoincrement=True)
    run_id = Column(Integer, ForeignKey("pipeline_runs.id", ondelete="CASCADE"), nullable=False, index=True)
    phase = Column(String(40), nullable=False)
    status = Column(String(30), nullable=False)
    message = Column(Text, nullable=False)
    details_json = Column(Text, nullable=True)
    created_at = Column(DateTime, default=lambda: datetime.now(timezone.utc), nullable=False, index=True)

    run = relationship("PipelineRun")


class InboxMessage(Base):
    __tablename__ = "inbox_messages"
    id = Column(Integer, primary_key=True, autoincrement=True)
    user_id = Column(Integer, ForeignKey("users.id", ondelete="CASCADE"), nullable=True, index=True)
    job_id = Column(Integer, ForeignKey("job_postings.id", ondelete="CASCADE"), nullable=True, index=True)
    message_id = Column(String(998), nullable=True)
    imap_uid = Column(String(64), nullable=True)
    status = Column(String(20), nullable=False)
    subject = Column(String(998), nullable=True)
    sender_email = Column(String(255), nullable=True)
    reason = Column(Text, nullable=True)
    created_at = Column(DateTime, default=lambda: datetime.now(timezone.utc), nullable=False)

    user = relationship("User", foreign_keys=[user_id])
    job = relationship("JobPosting", foreign_keys=[job_id])

    __table_args__ = (
        Index("ix_inbox_messages_message_id", "message_id"),
        Index("ix_inbox_messages_imap_uid", "imap_uid"),
    )


class DailyQuota(Base):
    __tablename__ = "daily_quotas"
    id = Column(Integer, primary_key=True, autoincrement=True)
    usage_date = Column(String(10), nullable=False, unique=True, index=True)
    ai_calls = Column(Integer, default=0, nullable=False)
    outbound_sends = Column(Integer, default=0, nullable=False)
    application_sends = Column(Integer, default=0, nullable=False)
    updated_at = Column(DateTime, default=lambda: datetime.now(timezone.utc), nullable=False)

    __table_args__ = (
        CheckConstraint("ai_calls >= 0", name="ck_daily_quotas_ai_nonnegative"),
        CheckConstraint("outbound_sends >= 0", name="ck_daily_quotas_outbound_nonnegative"),
        CheckConstraint("application_sends >= 0", name="ck_daily_quotas_application_nonnegative"),
    )


class AIProviderState(Base):
    __tablename__ = "ai_provider_state"
    id = Column(Integer, primary_key=True, autoincrement=True)
    provider = Column(String(30), nullable=False, unique=True, index=True)
    failure_count = Column(Integer, default=0, nullable=False)
    cooldown_until = Column(DateTime, nullable=True)
    last_error = Column(Text, nullable=True)
    updated_at = Column(DateTime, default=lambda: datetime.now(timezone.utc), nullable=False)


# ---------------------------------------------------------------------------
# Freelance & Client Acquisition Engine
# ---------------------------------------------------------------------------

class FreelanceStage(str, enum.Enum):
    DISCOVERED = "discovered"
    EVALUATED = "evaluated"
    PITCHED = "pitched"
    IN_DISCUSSION = "in_discussion"
    OFFER_RECEIVED = "offer_received"
    DEAL_WON = "deal_won"
    LOST = "lost"


class FreelanceLead(Base):
    """A freelance project lead / potential client discovered from public sources."""
    __tablename__ = "freelance_leads"

    id = Column(Integer, primary_key=True, autoincrement=True)
    user_id = Column(Integer, ForeignKey("users.id", ondelete="CASCADE"), nullable=True, index=True)
    dedup_hash = Column(String(32), nullable=False, index=True)
    title = Column(String(500), nullable=True)
    client_name = Column(String(255), nullable=True)
    client_type = Column(String(30), nullable=True)  # individual, founder, agency, company
    contact_email = Column(String(255), nullable=True)
    contact_url = Column(Text, nullable=True)
    source_platform = Column(String(60), nullable=False, index=True)
    source_url = Column(Text, nullable=True)
    raw_description = Column(Text, nullable=True)

    budget_estimate = Column(String(120), nullable=True)
    currency = Column(String(10), nullable=True)
    project_scope = Column(Text, nullable=True)

    match_score = Column(Integer, nullable=True)
    evaluation_reason = Column(Text, nullable=True)
    evaluated = Column(Boolean, default=False, nullable=False)
    is_spam = Column(Boolean, default=False, nullable=False)

    pitch_subject = Column(String(998), nullable=True)
    pitch_body = Column(Text, nullable=True)
    pitch_status = Column(String(20), default="not_generated", nullable=False)  # not_generated, draft, sent, failed

    stage = Column(Enum(FreelanceStage), default=FreelanceStage.DISCOVERED, nullable=False, index=True)
    last_contact_at = Column(DateTime, nullable=True)
    follow_up_count = Column(Integer, default=0, nullable=False)
    next_follow_up_due = Column(DateTime, nullable=True)

    offer_amount = Column(String(120), nullable=True)
    offer_terms = Column(Text, nullable=True)
    counter_offer_notes = Column(Text, nullable=True)

    discovered_at = Column(DateTime, default=lambda: datetime.now(timezone.utc), nullable=False)
    updated_at = Column(DateTime, default=lambda: datetime.now(timezone.utc), nullable=False)

    user = relationship("User", back_populates="freelance_leads", foreign_keys=[user_id])
    messages = relationship("FreelanceMessage", back_populates="lead", cascade="all, delete-orphan")

    __table_args__ = (
        UniqueConstraint("user_id", "dedup_hash", name="uq_user_freelance_dedup"),
        Index("ix_freelance_stage", "stage"),
        Index("ix_freelance_next_followup", "next_follow_up_due"),
    )

    def __repr__(self):
        return f"<FreelanceLead {self.client_name} — {self.title} (score={self.match_score})>"


class FreelanceMessage(Base):
    """Communication record for a freelance lead (pitches, follow-ups, client replies)."""
    __tablename__ = "freelance_messages"

    id = Column(Integer, primary_key=True, autoincrement=True)
    lead_id = Column(Integer, ForeignKey("freelance_leads.id", ondelete="CASCADE"), nullable=False, index=True)
    direction = Column(String(10), nullable=False)  # outbound, inbound
    message_type = Column(String(30), nullable=False)  # pitch, follow_up_1, follow_up_2, client_reply
    subject = Column(String(998), nullable=True)
    body = Column(Text, nullable=True)
    sent_at = Column(DateTime, nullable=True)
    created_at = Column(DateTime, default=lambda: datetime.now(timezone.utc), nullable=False)

    lead = relationship("FreelanceLead", back_populates="messages")


# ---------------------------------------------------------------------------
# Dynamic Candidate Profiles & Keywords
# ---------------------------------------------------------------------------

class CandidateProfile(Base):
    """Dynamic, configurable candidate profile with custom keywords and search matrix."""
    __tablename__ = "candidate_profiles"

    id = Column(Integer, primary_key=True, autoincrement=True)
    name = Column(String(255), nullable=False, index=True)
    is_active = Column(Boolean, default=False, nullable=False, index=True)
    headline = Column(String(255), nullable=True)
    current_location = Column(String(255), nullable=True)
    target_locations = Column(Text, nullable=True)  # JSON list of countries/cities
    target_titles = Column(Text, nullable=True)  # JSON list of target job titles
    core_stack = Column(Text, nullable=True)  # JSON list of primary technologies
    keywords = Column(Text, nullable=True)  # JSON list of match keywords
    negative_keywords = Column(Text, nullable=True)  # JSON list of exclusion/penalty keywords
    experience_years = Column(Integer, default=3, nullable=False)
    degree_level = Column(String(50), default="bachelor", nullable=True)
    target_experience_level = Column(String(50), default="entry", nullable=True)
    visa_requirement = Column(Text, nullable=True)
    languages = Column(Text, nullable=True)  # JSON dict
    resume_text = Column(Text, nullable=True)  # custom markdown / text resume
    resume_path = Column(String(500), nullable=True)
    freelance_services = Column(Text, nullable=True)  # JSON list
    freelance_hourly_usd = Column(Integer, default=50, nullable=False)
    freelance_daily_eur = Column(Integer, default=400, nullable=False)
    freelance_currency = Column(String(10), default="EUR", nullable=False)
    calendar_url = Column(String(500), nullable=True)  # Free Cal.com / Calendly booking link
    user_id = Column(Integer, ForeignKey("users.id", ondelete="SET NULL"), nullable=True, index=True)
    created_at = Column(DateTime, default=lambda: datetime.now(timezone.utc), nullable=False)
    updated_at = Column(DateTime, default=lambda: datetime.now(timezone.utc), nullable=False)

    user = relationship("User", back_populates="profiles", foreign_keys=[user_id])

    __table_args__ = (
        UniqueConstraint("user_id", "name", name="uq_user_profile_name"),
    )

    def __repr__(self):
        return f"<CandidateProfile {self.name} (active={self.is_active})>"


# ---------------------------------------------------------------------------
# RBAC, Authentication & Moroccan Subscription Payments
# ---------------------------------------------------------------------------

class UserRole(str, enum.Enum):
    ADMIN = "admin"
    USER = "user"


class User(Base):
    """Registered platform user with role-based access control and quota tracking."""
    __tablename__ = "users"

    id = Column(Integer, primary_key=True, autoincrement=True)
    email = Column(String(255), nullable=False, unique=True, index=True)
    full_name = Column(String(255), nullable=False)
    password_hash = Column(String(255), nullable=True)  # nullable for OAuth users
    role = Column(String(20), default=UserRole.USER.value, nullable=False, index=True)
    is_active = Column(Boolean, default=True, nullable=False)
    is_verified = Column(Boolean, default=False, nullable=False)
    auth_provider = Column(String(50), default="local", nullable=False)  # local, google
    google_sub = Column(String(255), nullable=True, unique=True, index=True)
    avatar_url = Column(String(500), nullable=True)

    # Subscription plan & limits (Moroccan MAD pricing)
    # free: 5/day, starter_99: 50/day (99 MAD/mo), pro_499: 200/day (499 MAD/mo)
    current_plan = Column(String(50), default="free", nullable=False)
    daily_apply_limit = Column(Integer, default=5, nullable=False)
    plan_expires_at = Column(DateTime, nullable=True)

    created_at = Column(DateTime, default=lambda: datetime.now(timezone.utc), nullable=False)
    updated_at = Column(DateTime, default=lambda: datetime.now(timezone.utc), nullable=False)

    profiles = relationship("CandidateProfile", back_populates="user")
    jobs = relationship("JobPosting", back_populates="user", cascade="all, delete-orphan")
    freelance_leads = relationship("FreelanceLead", back_populates="user", cascade="all, delete-orphan")
    pipeline_runs = relationship("PipelineRun", back_populates="user", cascade="all, delete-orphan")
    payments = relationship("SubscriptionPayment", back_populates="user", cascade="all, delete-orphan")
    applications = relationship("UserJobApplication", back_populates="user", cascade="all, delete-orphan")
    settings = relationship("UserSetting", back_populates="user", uselist=False, cascade="all, delete-orphan")
    notifications = relationship("Notification", back_populates="user", cascade="all, delete-orphan")

    def __repr__(self):
        return f"<User {self.email} ({self.role}, plan={self.current_plan})>"


class SubscriptionPayment(Base):
    """Payment records for subscription plans (Crypto/USDT, PayPal, Card/Ko-fi, Wise, Bank Wire, etc.)."""
    __tablename__ = "subscription_payments"

    id = Column(Integer, primary_key=True, autoincrement=True)
    user_id = Column(Integer, ForeignKey("users.id", ondelete="CASCADE"), nullable=False, index=True)
    plan_name = Column(String(50), nullable=False)  # starter, pro, ultra / starter_99, pro_499
    amount_mad = Column(Integer, nullable=True)     # optional MAD amount
    amount_usd = Column(Float, nullable=True)       # optional USD amount
    currency = Column(String(10), default="USD", nullable=True)  # USD, MAD, EUR, USDT
    payment_method = Column(String(50), nullable=False)  # usdt_trc20, usdt_polygon, solana, btc, paypal, kofi_card, wise, revolut, cih_wire, attijari_wire, cashplus
    reference_code = Column(String(100), nullable=False, index=True)
    receipt_note = Column(Text, nullable=True)
    receipt_image_path = Column(String(500), nullable=True)
    status = Column(String(30), default="pending", nullable=False, index=True)  # pending, approved, rejected
    admin_notes = Column(Text, nullable=True)
    reviewed_by = Column(Integer, nullable=True)
    reviewed_at = Column(DateTime, nullable=True)

    created_at = Column(DateTime, default=lambda: datetime.now(timezone.utc), nullable=False)
    updated_at = Column(DateTime, default=lambda: datetime.now(timezone.utc), nullable=False)

    user = relationship("User", back_populates="payments")

    def __repr__(self):
        return f"<SubscriptionPayment #{self.id} User:{self.user_id} {self.plan_name} ({self.status})>"


class UserJobApplication(Base):
    """User-specific application ledger to ensure individual application tracking and quota counting."""
    __tablename__ = "user_job_applications"

    id = Column(Integer, primary_key=True, autoincrement=True)
    user_id = Column(Integer, ForeignKey("users.id", ondelete="CASCADE"), nullable=False, index=True)
    job_id = Column(Integer, ForeignKey("job_postings.id", ondelete="CASCADE"), nullable=False, index=True)
    application_method = Column(String(50), nullable=True)  # recruiter_email, web_portal
    applied_at = Column(DateTime, default=lambda: datetime.now(timezone.utc), nullable=False, index=True)
    status = Column(String(50), default="sent", nullable=False)  # sent, submitted, failed

    user = relationship("User", back_populates="applications")
    job = relationship("JobPosting")

    __table_args__ = (
        UniqueConstraint("user_id", "job_id", name="uq_user_job_application"),
    )


class UserSetting(Base):
    """User-specific environment variables and operational credentials configured through the platform."""
    __tablename__ = "user_settings"

    id = Column(Integer, primary_key=True, autoincrement=True)
    user_id = Column(Integer, ForeignKey("users.id", ondelete="CASCADE"), nullable=False, unique=True, index=True)

    # Personal Outbound Sender & SMTP configuration
    sender_email = Column(String(255), nullable=True)
    sender_name = Column(String(255), nullable=True)
    smtp_host = Column(String(255), default="smtp.gmail.com", nullable=True)
    smtp_port = Column(Integer, default=587, nullable=True)
    smtp_password = Column(String(255), nullable=True)
    imap_host = Column(String(255), default="imap.gmail.com", nullable=True)
    imap_port = Column(Integer, default=993, nullable=True)
    imap_password = Column(String(255), nullable=True)

    # Candidate profile links & contacts
    phone_number = Column(String(50), nullable=True)
    linkedin_url = Column(String(500), nullable=True)
    github_url = Column(String(500), nullable=True)
    portfolio_url = Column(String(500), nullable=True)

    # Match & auto-apply controls
    min_match_score = Column(Integer, default=65, nullable=True)
    auto_apply_mode = Column(String(50), default="send", nullable=True)
    auto_apply_min_score = Column(Integer, default=70, nullable=True)

    # Personal alert notifications
    telegram_bot_token = Column(String(255), nullable=True)
    telegram_chat_id = Column(String(100), nullable=True)
    alert_email = Column(String(255), nullable=True)

    # Custom environment variables JSON key-value pairs
    custom_env_json = Column(Text, default="{}", nullable=True)

    created_at = Column(DateTime, default=lambda: datetime.now(timezone.utc), nullable=False)
    updated_at = Column(DateTime, default=lambda: datetime.now(timezone.utc), nullable=False)

    user = relationship("User", back_populates="settings")


class Notification(Base):
    """Event-driven real-time notifications for users and administrators."""
    __tablename__ = "notifications"

    id = Column(Integer, primary_key=True, autoincrement=True)
    user_id = Column(Integer, ForeignKey("users.id", ondelete="CASCADE"), nullable=True, index=True)
    event_type = Column(String(50), nullable=False, index=True)  # job_match, app_sent, inbox_reply, payment_approved, payment_rejected, quota_warning, system
    title = Column(String(255), nullable=False)
    message = Column(Text, nullable=False)
    link = Column(String(500), nullable=True)
    data_json = Column(Text, nullable=True)
    is_read = Column(Boolean, default=False, nullable=False, index=True)
    created_at = Column(DateTime, default=lambda: datetime.now(timezone.utc), nullable=False, index=True)

    user = relationship("User", back_populates="notifications")


class SupportMessage(Base):
    """User support and AI assistant chat messages with live admin intervention capabilities."""
    __tablename__ = "support_messages"

    id = Column(Integer, primary_key=True, autoincrement=True)
    user_id = Column(Integer, ForeignKey("users.id", ondelete="CASCADE"), nullable=False, index=True)
    sender_role = Column(String(20), default="user", nullable=False)  # "user", "bot", "admin"
    sender_name = Column(String(255), nullable=False)
    admin_id = Column(Integer, ForeignKey("users.id", ondelete="SET NULL"), nullable=True)
    message = Column(Text, nullable=False)
    language = Column(String(20), nullable=True)
    is_read_by_user = Column(Boolean, default=True, nullable=False)
    is_read_by_admin = Column(Boolean, default=False, nullable=False, index=True)
    created_at = Column(DateTime, default=lambda: datetime.now(timezone.utc), nullable=False, index=True)

    user = relationship("User", foreign_keys=[user_id], backref="support_messages")
    admin = relationship("User", foreign_keys=[admin_id])

    __table_args__ = (
        Index("ix_support_messages_user_created", "user_id", "created_at"),
    )

    def __repr__(self):
        return f"<SupportMessage #{self.id} user={self.user_id} [{self.sender_role}]: {self.message[:30]}>"


class PlatformPlan(Base):
    """Dynamic platform subscription plans with configurable prices, quotas and features."""
    __tablename__ = "platform_plans"

    id = Column(Integer, primary_key=True, autoincrement=True)
    slug = Column(String(50), nullable=False, unique=True, index=True)  # starter, pro, ultra, starter_99, pro_499
    name = Column(String(100), nullable=False)
    badge = Column(String(50), nullable=True)  # e.g. "Most Popular ⭐"
    description = Column(Text, nullable=True)
    price_usd = Column(Float, default=0.0, nullable=False)
    price_mad = Column(Integer, default=0, nullable=False)
    price_eur = Column(Float, default=0.0, nullable=False)
    price_usdt = Column(Float, default=0.0, nullable=False)
    billing_interval = Column(String(30), default="/ month", nullable=False)
    daily_apply_limit = Column(Integer, default=5, nullable=False)
    max_ai_calls_per_day = Column(Integer, default=10, nullable=False)
    can_access_freelance = Column(Boolean, default=False, nullable=False)
    is_active = Column(Boolean, default=True, nullable=False)
    is_recommended = Column(Boolean, default=False, nullable=False)
    features_json = Column(Text, default="[]", nullable=False)
    sort_order = Column(Integer, default=0, nullable=False)
    created_at = Column(DateTime, default=lambda: datetime.now(timezone.utc), nullable=False)
    updated_at = Column(DateTime, default=lambda: datetime.now(timezone.utc), nullable=False)

    def __repr__(self):
        return f"<PlatformPlan {self.slug}: {self.name} ({self.price_mad} MAD / ${self.price_usd} USD) limit={self.daily_apply_limit}>"


class PaymentGatewayConfig(Base):
    """Configurable payment gateways with live credentials, status and instruction sets."""
    __tablename__ = "payment_gateway_configs"

    id = Column(Integer, primary_key=True, autoincrement=True)
    gateway_key = Column(String(50), nullable=False, unique=True, index=True)  # stripe, paypal, crypto_usdt_trc20, etc.
    name = Column(String(100), nullable=False)
    category = Column(String(50), nullable=False)  # card_stripe, paypal, crypto, card_kofi, bank_wire, etc.
    is_enabled = Column(Boolean, default=True, nullable=False)
    config_json = Column(Text, default="{}", nullable=False)  # API keys, wallet addresses, bank ribs
    instructions = Column(Text, nullable=True)
    sort_order = Column(Integer, default=0, nullable=False)
    created_at = Column(DateTime, default=lambda: datetime.now(timezone.utc), nullable=False)
    updated_at = Column(DateTime, default=lambda: datetime.now(timezone.utc), nullable=False)

    def __repr__(self):
        return f"<PaymentGatewayConfig {self.gateway_key} ({self.name}, enabled={self.is_enabled})>"



