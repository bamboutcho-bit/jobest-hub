"""SQLAlchemy engine/session factory plus additive migrations."""
from contextlib import contextmanager

from sqlalchemy import create_engine, inspect, text
from sqlalchemy.orm import sessionmaker

from config.settings import settings
from src.storage.models import Base

_engine = create_engine(
    settings.database_url,
    pool_pre_ping=True,
    connect_args={"check_same_thread": False, "timeout": 30} if settings.database_url.startswith("sqlite") else {"connect_timeout": 2},
)
_SessionLocal = sessionmaker(bind=_engine, autoflush=False, autocommit=False, expire_on_commit=False)


def _additive_migrations() -> None:
    inspector = inspect(_engine)
    migrations = {
        "job_postings": {
            "source_query": "VARCHAR(120)",
            "source_sites": "TEXT",
            "job_url_direct": "TEXT",
            "application_url": "TEXT",
            "company_url": "TEXT",
            "application_emails": "TEXT",
            "ai_relevance_score": "INTEGER",
            "evaluation_method": "VARCHAR(30)",
            "application_subject": "VARCHAR(998)",
            "application_email_body": "TEXT",
            "application_status": "VARCHAR(50) DEFAULT 'not_attempted'",
            "application_error": "TEXT",
            "application_attempted_at": "TIMESTAMP",
            "resume_attached": "BOOLEAN DEFAULT FALSE",
            "application_screenshot_path": "TEXT",
            "continent": "VARCHAR(40)",
            "experience_level": "VARCHAR(40)",
            "experience_years_required": "INTEGER",
            "degree_required": "VARCHAR(60)",
            "degree_matched": "BOOLEAN",
            "visa_category": "VARCHAR(60)",
            "relocation_detected": "BOOLEAN DEFAULT FALSE",
            "user_id": "INTEGER",
        },
        "pipeline_runs": {
            "updated_at": "TIMESTAMP",
            "phase": "VARCHAR(40) DEFAULT 'starting'",
            "progress_pct": "INTEGER DEFAULT 0",
            "current_task": "TEXT",
            "total_queries": "INTEGER DEFAULT 0",
            "completed_queries": "INTEGER DEFAULT 0",
            "raw_scraped_live": "INTEGER DEFAULT 0",
            "new_postings_live": "INTEGER DEFAULT 0",
            "evaluated_live": "INTEGER DEFAULT 0",
            "applications_sent": "INTEGER DEFAULT 0",
            "application_failures": "INTEGER DEFAULT 0",
            "application_manual": "INTEGER DEFAULT 0",
            "scrape_queries_json": "TEXT",
            "user_id": "INTEGER",
        },
        "inbox_messages": {
            "message_id": "VARCHAR(998)", "imap_uid": "VARCHAR(64)", "status": "VARCHAR(20)",
            "subject": "VARCHAR(998)", "sender_email": "VARCHAR(255)", "reason": "TEXT",
            "user_id": "INTEGER", "job_id": "INTEGER",
        },
        "email_events": {
            "in_reply_to": "VARCHAR(998)", "references": "TEXT", "sender_email": "VARCHAR(255)",
            "recipient_email": "VARCHAR(255)", "imap_uid": "VARCHAR(64)", "email_type": "VARCHAR(30) DEFAULT 'other' NOT NULL",
        },
        "outbound_messages": {
            "idempotency_key": "VARCHAR(255)", "job_id": "INTEGER", "email_type": "VARCHAR(30)",
            "recipient_email": "VARCHAR(255)", "subject": "VARCHAR(998)", "message_id": "VARCHAR(998)",
            "status": "VARCHAR(20)", "failure_reason": "TEXT", "created_at": "TIMESTAMP", "sent_at": "TIMESTAMP", "updated_at": "TIMESTAMP",
            "user_id": "INTEGER",
        },
        "outbound_suppressions": {
            "recipient_email": "VARCHAR(255)", "domain": "VARCHAR(255)", "reason": "VARCHAR(50)",
            "details": "TEXT", "created_at": "TIMESTAMP",
            "user_id": "INTEGER",
        },
        "freelance_leads": {
            "budget_estimate": "VARCHAR(120)", "currency": "VARCHAR(10)", "project_scope": "TEXT",
            "match_score": "INTEGER", "evaluation_reason": "TEXT", "evaluated": "BOOLEAN DEFAULT FALSE",
            "is_spam": "BOOLEAN DEFAULT FALSE",
            "pitch_subject": "VARCHAR(998)", "pitch_body": "TEXT",
            "pitch_status": "VARCHAR(20) DEFAULT 'not_generated'",
            "last_contact_at": "TIMESTAMP", "follow_up_count": "INTEGER DEFAULT 0",
            "next_follow_up_due": "TIMESTAMP",
            "offer_amount": "VARCHAR(120)", "offer_terms": "TEXT", "counter_offer_notes": "TEXT",
            "updated_at": "TIMESTAMP",
            "user_id": "INTEGER",
        },
        "freelance_messages": {
            "subject": "VARCHAR(998)", "body": "TEXT", "sent_at": "TIMESTAMP",
        },
        "candidate_profiles": {
            "calendar_url": "VARCHAR(500)",
            "degree_level": "VARCHAR(60) DEFAULT 'bachelor'",
            "target_experience_level": "VARCHAR(40) DEFAULT 'entry'",
            "user_id": "INTEGER",
        },
        "support_messages": {
            "user_id": "INTEGER",
            "sender_role": "VARCHAR(20) DEFAULT 'user'",
            "sender_name": "VARCHAR(255)",
            "admin_id": "INTEGER",
            "message": "TEXT",
            "language": "VARCHAR(20)",
            "is_read_by_user": "BOOLEAN DEFAULT TRUE",
            "is_read_by_admin": "BOOLEAN DEFAULT FALSE",
            "created_at": "TIMESTAMP",
        },
        "subscription_payments": {
            "amount_usd": "FLOAT",
            "currency": "VARCHAR(10) DEFAULT 'USD'",
            "receipt_image_path": "VARCHAR(500)",
        },
    }
    with _engine.begin() as conn:
        tables = set(inspector.get_table_names())
        for table, columns in migrations.items():
            if table not in tables:
                continue
            existing = {c["name"] for c in inspector.get_columns(table)}
            for name, ddl in columns.items():
                if name not in existing:
                    conn.execute(text(f'ALTER TABLE {table} ADD COLUMN {name} {ddl}'))


_db_initialized = False


def init_db() -> None:
    global _engine, _SessionLocal, _db_initialized
    try:
        with _engine.connect() as conn:
            pass
    except Exception as exc:
        if "postgres" in settings.database_url or "5432" in settings.database_url:
            import logging
            logging.getLogger("src.storage.db").warning(
                "PostgreSQL server unreachable (%s). Falling back to local SQLite database (jobsearch_local.db).",
                exc,
            )
            _engine = create_engine(
                "sqlite:///jobsearch_local.db",
                connect_args={"check_same_thread": False, "timeout": 30},
            )
            _SessionLocal = sessionmaker(bind=_engine, autoflush=False, autocommit=False)
    if "sqlite" in str(_engine.url):
        with _engine.connect() as conn:
            conn.execute(text("PRAGMA journal_mode=WAL;"))
            conn.execute(text("PRAGMA busy_timeout=30000;"))
    Base.metadata.create_all(bind=_engine)
    _additive_migrations()
    _seed_default_admin()
    try:
        from src.storage.plans import seed_default_plans_and_gateways
        with _SessionLocal() as session:
            seed_default_plans_and_gateways(session)
    except Exception as plan_seed_exc:
        import logging
        logging.getLogger("src.storage.db").warning("Default plans/gateways seed skipped: %s", plan_seed_exc)
    _db_initialized = True


def _seed_default_admin() -> None:
    """Ensure the admin account from .env exists and has the correct password."""
    try:
        from src.storage.models import User, UserRole
        from src.auth.service import hash_password
        admin_email = (settings.admin_email or "admin@autohunt.internal").strip().lower()
        admin_password = settings.admin_password or settings.dashboard_password or "changeme"
        with _SessionLocal() as session:
            # Look up by email first
            admin_user = session.query(User).filter(User.email == admin_email).first()
            if admin_user:
                # Sync password and ensure admin role
                admin_user.password_hash = hash_password(admin_password)
                admin_user.role = UserRole.ADMIN.value
                admin_user.is_active = True
                admin_user.is_verified = True
                if admin_user.current_plan != "pro_499":
                    admin_user.current_plan = "pro_499"
                    admin_user.daily_apply_limit = 200
                session.commit()
            else:
                # Also check for any existing admin (legacy)
                legacy_admin = session.query(User).filter(User.role == UserRole.ADMIN.value).first()
                if not legacy_admin:
                    admin_user = User(
                        email=admin_email,
                        full_name="Platform Administrator",
                        password_hash=hash_password(admin_password),
                        role=UserRole.ADMIN.value,
                        is_active=True,
                        is_verified=True,
                        auth_provider="local",
                        current_plan="pro_499",
                        daily_apply_limit=200,
                    )
                    session.add(admin_user)
                    session.commit()
    except Exception as exc:
        import logging
        logging.getLogger("src.storage.db").warning("Default admin seed skipped: %s", exc)


@contextmanager
def get_session():
    global _db_initialized
    if not _db_initialized:
        init_db()
    session = _SessionLocal()
    try:
        yield session
        session.commit()
    except Exception:
        session.rollback()
        raise
    finally:
        session.close()
