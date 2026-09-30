#!/usr/bin/env python3
"""
Entrypoint.

Usage:
  python scripts/run_pipeline.py            # run once and exit
  python scripts/run_pipeline.py --loop      # run on a schedule (configurable)
"""
import argparse
import logging
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from apscheduler.schedulers.blocking import BlockingScheduler

from config.settings import settings
from src.orchestrator import run_pipeline

logger = logging.getLogger(__name__)


def run_full_cycle() -> None:
    """Execute both full-time job discovery and freelance client pipeline in a unified cycle for all active users."""
    logger.info("==================================================")
    logger.info("Starting Unified Job & Freelance Automation Cycle")
    logger.info("==================================================")

    # Discover all active users with candidate profiles
    active_user_ids = []
    try:
        from src.storage.db import get_session
        from src.storage.models import User, CandidateProfile
        from sqlalchemy import select
        with get_session() as session:
            users = session.scalars(select(User).where(User.is_active.is_(True)).order_by(User.id)).all()
            for u in users:
                has_profile = session.scalar(
                    select(CandidateProfile.id).where(
                        CandidateProfile.user_id == u.id,
                        CandidateProfile.is_active.is_(True),
                    ).limit(1)
                )
                if not has_profile:
                    from sqlalchemy import desc
                    import json
                    prof = session.scalar(select(CandidateProfile).where(CandidateProfile.user_id == u.id).order_by(desc(CandidateProfile.id)).limit(1))
                    if prof:
                        prof.is_active = True
                        session.commit()
                        has_profile = prof.id
                    else:
                        from src.candidate.profile_manager import DEFAULT_STATIC_PROFILE
                        new_prof = CandidateProfile(
                            user_id=u.id,
                            name=u.full_name or u.email.split("@")[0],
                            headline=DEFAULT_STATIC_PROFILE.get("headline", "Fullstack Software Engineer"),
                            current_location=DEFAULT_STATIC_PROFILE.get("current_location", "Morocco"),
                            target_locations=json.dumps(DEFAULT_STATIC_PROFILE.get("target_locations", ["Remote", "Europe", "France"])),
                            target_titles=json.dumps(DEFAULT_STATIC_PROFILE.get("target_titles", ["Software Engineer", "Backend Engineer", "Java Developer"])),
                            core_stack=json.dumps(DEFAULT_STATIC_PROFILE.get("core_stack", ["Java", "Spring Boot", "React", "Docker", "PostgreSQL"])),
                            keywords=json.dumps(DEFAULT_STATIC_PROFILE.get("keywords", ["Microservices", "REST API", "CI/CD"])),
                            negative_keywords="[]",
                            experience_years=DEFAULT_STATIC_PROFILE.get("experience_years", 3),
                            languages=json.dumps(DEFAULT_STATIC_PROFILE.get("languages", {"French": "Fluent", "English": "Fluent"})),
                            freelance_services="[]",
                            is_active=True,
                        )
                        session.add(new_prof)
                        session.commit()
                        has_profile = new_prof.id
                if has_profile:
                    active_user_ids.append(u.id)
    except Exception as exc:
        logger.warning("Could not query active users: %s", exc)

    if not active_user_ids:
        # Fallback to single/default run
        try:
            run_pipeline()
        except Exception as exc:
            logger.error("Job search pipeline run error: %s", exc)

        try:
            from src.freelance.orchestrator import run_freelance_pipeline
            summary = run_freelance_pipeline()
            logger.info("Freelance automated run finished: %s", summary)
        except Exception as exc:
            logger.error("Freelance automation cycle error: %s", exc)
        return

    logger.info("Executing automated cycle across %s active candidate users: %s", len(active_user_ids), active_user_ids)
    for uid in active_user_ids:
        logger.info("--- Running pipeline for user_id=%s ---", uid)
        try:
            run_pipeline(user_id=uid)
        except Exception as exc:
            logger.error("Job search pipeline error for user %s: %s", uid, exc)

        try:
            from src.freelance.orchestrator import run_freelance_pipeline
            summary = run_freelance_pipeline(user_id=uid)
            logger.info("Freelance run for user %s finished: %s", uid, summary)
        except Exception as exc:
            logger.error("Freelance automation error for user %s: %s", uid, exc)




def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--loop", action="store_true", help="Run continuously on a schedule")
    parser.add_argument("--interval-hours", type=int, default=settings.pipeline_interval_hours, help="Hours between runs in --loop mode")
    args = parser.parse_args()

    if not args.loop:
        run_full_cycle()
        return

    scheduler = BlockingScheduler()
    scheduler.add_job(run_full_cycle, "interval", hours=args.interval_hours, next_run_time=None)
    logger.info("Scheduler started: running every %s hours. Ctrl+C to stop.", args.interval_hours)
    run_full_cycle()  # run immediately once, then let the interval take over
    try:
        scheduler.start()
    except (KeyboardInterrupt, SystemExit):
        pass


if __name__ == "__main__":
    main()
