#!/usr/bin/env python3
"""
Inbox/response monitoring entrypoint — runs far more often than the job
search pipeline (default every 30 min vs every 6h), since replies need a
fast reaction time to look responsive and to schedule interviews before
proposed slots go stale.

Usage:
  python scripts/check_inbox.py            # run once and exit
  python scripts/check_inbox.py --loop      # run on a schedule
"""
import argparse
import logging
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from apscheduler.schedulers.blocking import BlockingScheduler

from config.settings import settings
from src.inbox.action_dispatcher import run_dispatch_cycle
from src.inbox.inbox_monitor import check_inbox_once
from src.storage.db import init_db

logging.basicConfig(level=logging.INFO, format="%(asctime)s [%(levelname)s] %(name)s: %(message)s")
logger = logging.getLogger(__name__)


def run_once() -> None:
    init_db()
    check_inbox_once()
    run_dispatch_cycle()


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--loop", action="store_true")
    args = parser.parse_args()

    if not args.loop:
        run_once()
        return

    scheduler = BlockingScheduler()
    scheduler.add_job(run_once, "interval", minutes=settings.inbox_poll_minutes)
    logger.info("Inbox monitor started: polling every %s minutes.", settings.inbox_poll_minutes)
    run_once()
    try:
        scheduler.start()
    except (KeyboardInterrupt, SystemExit):
        pass


if __name__ == "__main__":
    main()
