import sys
sys.path.insert(0, ".")
from src.storage.db import get_session, init_db
from sqlalchemy import text
from datetime import datetime, timezone

init_db()
with get_session() as s:
    total_jobs = s.execute(text("SELECT count(*) FROM job_postings")).scalar()
    applied_jobs = s.execute(text("SELECT count(*) FROM job_postings WHERE applied_at IS NOT NULL")).scalar()
    matches_60 = s.execute(text("SELECT count(*) FROM job_postings WHERE match_score >= 60")).scalar()
    matches_60_unapplied = s.execute(text("SELECT count(*) FROM job_postings WHERE match_score >= 60 AND applied_at IS NULL")).scalar()
    statuses = s.execute(text("SELECT application_status, count(*) FROM job_postings GROUP BY application_status")).fetchall()
    
    print(f"Total jobs: {total_jobs}")
    print(f"Applied jobs: {applied_jobs}")
    print(f"Matches >= 60: {matches_60}")
    print(f"Matches >= 60 unapplied: {matches_60_unapplied}")
    print("\nApplication statuses across all jobs:")
    for st, c in statuses:
        print(f"  {st}: {c}")

    print("\nRecent 10 matches >= 60:")
    recent = s.execute(text("SELECT id, title, company, match_score, application_status, application_error FROM job_postings WHERE match_score >= 60 ORDER BY id DESC LIMIT 10")).fetchall()
    for r in recent:
        print(f"  #{r[0]} | {r[1]} @ {r[2]} | score={r[3]} | status={r[4]} | err={str(r[5])[:80] if r[5] else 'none'}")
