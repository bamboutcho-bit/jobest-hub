import sys
from pathlib import Path
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from src.storage.db import get_session
from src.storage.models import JobPosting, UserJobApplication, PipelineStage, PipelineRun
from sqlalchemy import select, desc, func
from datetime import datetime, timezone

with get_session() as s:
    print("--- UserJobApplication count ---")
    apps = s.scalars(select(UserJobApplication).order_by(desc(UserJobApplication.applied_at)).limit(40)).all()
    print(f"Total apps in DB: {s.scalar(select(func.count(UserJobApplication.id)))}")
    for a in apps[:10]:
        print(f"App {a.id}: job={a.job_id} at={a.applied_at} method={a.application_method} status={a.status}")

    print("\n--- Pipeline Runs ---")
    runs = s.scalars(select(PipelineRun).order_by(desc(PipelineRun.id)).limit(5)).all()
    for r in runs:
        print(f"Run {r.id}: status={r.status} apps_sent={r.applications_sent} mode={getattr(r, 'run_mode', None)}")

    print("\n--- JobPosting application_status breakdown ---")
    res = s.execute(select(JobPosting.application_status, func.count(JobPosting.id)).group_by(JobPosting.application_status)).all()
    for status, cnt in res:
        print(f"Status: {status!r:25s} -> {cnt} jobs")

    print("\n--- JobPosting pipeline_stage breakdown ---")
    res2 = s.execute(select(JobPosting.pipeline_stage, func.count(JobPosting.id)).group_by(JobPosting.pipeline_stage)).all()
    for stage, cnt in res2:
        print(f"Stage: {stage!r:25s} -> {cnt} jobs")
