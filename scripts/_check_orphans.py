"""Quick orphan check — run inside Docker container."""
import sys; sys.path.insert(0, '.')
from src.storage.db import get_session, init_db
from sqlalchemy import text

init_db()
with get_session() as s:
    admin = s.execute(text("SELECT id, email, role FROM users WHERE role = 'admin' ORDER BY id ASC LIMIT 1")).fetchone()
    print(f"Admin: id={admin[0]}, email={admin[1]}, role={admin[2]}")
    for tbl in ['job_postings', 'freelance_leads', 'candidate_profiles', 'pipeline_runs']:
        n = s.execute(text(f'SELECT count(*) FROM {tbl} WHERE user_id IS NULL')).scalar()
        t = s.execute(text(f'SELECT count(*) FROM {tbl}')).scalar()
        print(f"  {tbl}: {n} orphans / {t} total")
