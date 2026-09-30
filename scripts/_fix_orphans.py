"""Assign orphan rows (user_id IS NULL) to admin user. Run inside Docker."""
import sys; sys.path.insert(0, '.')
from src.storage.db import get_session, init_db
from sqlalchemy import text

init_db()
with get_session() as s:
    admin = s.execute(text("SELECT id FROM users WHERE role = 'admin' ORDER BY id ASC LIMIT 1")).scalar()
    print(f"Admin user ID: {admin}")
    for tbl in ['job_postings', 'freelance_leads', 'candidate_profiles', 'pipeline_runs']:
        res = s.execute(text(f"UPDATE {tbl} SET user_id = :uid WHERE user_id IS NULL"), {"uid": admin})
        print(f"  {tbl}: assigned {res.rowcount} rows")
    # verify
    for tbl in ['job_postings', 'freelance_leads', 'candidate_profiles', 'pipeline_runs']:
        n = s.execute(text(f'SELECT count(*) FROM {tbl} WHERE user_id IS NULL')).scalar()
        print(f"  {tbl}: {n} orphans remaining")
    print("Done!")
