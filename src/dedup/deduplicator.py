"""
Deduplication — MD5 hash of (company + title + raw_description), checked
against the DB unique index on JobPosting.dedup_hash. Anything already
present is dropped before it ever reaches the (paid) Claude evaluation
step, which is where dedup earns its keep.
"""
import hashlib
import logging
from typing import Any

from sqlalchemy import select

from src.storage.db import get_session
from src.storage.models import JobPosting

logger = logging.getLogger(__name__)


def compute_hash(company: str, title: str, raw_description: str) -> str:
    normalized = f"{(company or '').strip().lower()}|{(title or '').strip().lower()}|{(raw_description or '').strip().lower()}"
    return hashlib.md5(normalized.encode("utf-8")).hexdigest()


def filter_new_postings(raw_jobs: list[dict[str, Any]], user_id: int | None = None) -> list[dict[str, Any]]:
    """Tag each raw job with its dedup_hash, then filter out anything whose
    hash already exists in job_postings for the target user. Returns only genuinely new jobs."""
    for job in raw_jobs:
        job["dedup_hash"] = compute_hash(
            job.get("company"), job.get("title"), job.get("description")
        )
        if user_id is not None:
            job["user_id"] = user_id

    incoming_hashes = [j["dedup_hash"] for j in raw_jobs]
    if not incoming_hashes:
        return []

    with get_session() as session:
        query = select(JobPosting.dedup_hash).where(
            JobPosting.dedup_hash.in_(incoming_hashes)
        )
        if user_id is not None:
            query = query.where(JobPosting.user_id == user_id)
        existing = set(session.scalars(query).all())

    # De-dup within this batch too (same job can appear via multiple queries)
    seen_in_batch: set[str] = set()
    new_jobs = []
    for job in raw_jobs:
        h = job["dedup_hash"]
        if h in existing or h in seen_in_batch:
            continue
        seen_in_batch.add(h)
        new_jobs.append(job)

    logger.info(
        "Dedup (user_id=%s): %d raw -> %d new (%d already known, %d intra-batch dupes)",
        user_id, len(raw_jobs), len(new_jobs), len(existing),
        len(raw_jobs) - len(new_jobs) - len(existing) + len(existing),
    )
    return new_jobs
