"""One-time migration: assign all existing orphan data to the admin user.

This script finds the admin account (by role or .env email) and sets user_id
on every JobPosting, FreelanceLead, CandidateProfile, and PipelineRun row
that currently has user_id = NULL.

Safe to run multiple times — it only touches NULL rows.

Usage:
    python -m scripts.migrate_data_to_admin
"""
from __future__ import annotations

import logging
import sys
from pathlib import Path

# Ensure project root is on sys.path
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from sqlalchemy import select, update, func
from config.settings import settings
from src.storage.db import init_db, get_session
from src.storage.models import (
    CandidateProfile,
    FreelanceLead,
    JobPosting,
    PipelineRun,
    User,
    UserRole,
)

logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")
logger = logging.getLogger("migrate_data_to_admin")


def _find_admin_id() -> int:
    """Locate the admin user id. Tries .env admin email first, then role-based lookup."""
    with get_session() as session:
        admin_email = (settings.admin_email or "").strip().lower()
        if admin_email:
            user = session.scalar(select(User).where(User.email == admin_email))
            if user:
                return user.id

        # Fallback: any admin role
        user = session.scalar(select(User).where(User.role == UserRole.ADMIN.value))
        if user:
            return user.id

    raise RuntimeError(
        "No admin user found in database. Ensure the app has been started at least once "
        "so the default admin is seeded, or set ADMIN_EMAIL in .env."
    )


def _count_orphans(session, model, label: str) -> int:
    """Count rows where user_id IS NULL."""
    n = session.scalar(select(func.count(model.id)).where(model.user_id.is_(None))) or 0
    logger.info("  %s: %d orphan rows (user_id IS NULL)", label, n)
    return n


def migrate():
    init_db()
    admin_id = _find_admin_id()
    logger.info("Admin user ID: %d", admin_id)

    tables = [
        (JobPosting, "job_postings"),
        (FreelanceLead, "freelance_leads"),
        (CandidateProfile, "candidate_profiles"),
        (PipelineRun, "pipeline_runs"),
    ]

    with get_session() as session:
        logger.info("Scanning orphan rows...")
        total = 0
        for model, label in tables:
            n = _count_orphans(session, model, label)
            total += n

        if total == 0:
            logger.info("✅ No orphan data found — all rows already have a user_id.")
            return

        logger.info("Assigning %d total orphan rows to admin (user_id=%d)...", total, admin_id)

        for model, label in tables:
            result = session.execute(
                update(model)
                .where(model.user_id.is_(None))
                .values(user_id=admin_id)
            )
            logger.info("  %s: updated %d rows", label, result.rowcount)

        # session auto-commits via get_session context manager
        logger.info("✅ Migration complete! All orphan data now belongs to admin user %d.", admin_id)


if __name__ == "__main__":
    migrate()
