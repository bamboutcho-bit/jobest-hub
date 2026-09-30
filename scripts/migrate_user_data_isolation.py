"""
Data Migration & Schema Evolution: User Data Isolation.

Assigns all legacy unassigned records (5,103+ jobs, 132 freelance leads,
active candidate profiles, pipeline runs) to Admin User ID 1.
Adds user_id foreign keys, indexes, and user-scoped unique constraints.
"""
import logging
import sys
from sqlalchemy import text, inspect
from src.storage.db import get_session, _engine

logging.basicConfig(level=logging.INFO)
logger = logging.getLogger("migration")

def run_migration():
    logger.info("Starting User Data Isolation migration...")
    
    with get_session() as session:
        # 1. Identify Admin User
        admin_id = session.execute(text("SELECT id FROM users WHERE role = 'admin' ORDER BY id ASC LIMIT 1")).scalar()
        if not admin_id:
            admin_id = session.execute(text("SELECT id FROM users ORDER BY id ASC LIMIT 1")).scalar()
        if not admin_id:
            logger.error("No user found in the database. Cannot migrate.")
            sys.exit(1)
        
        logger.info(f"Primary Admin User ID identified: {admin_id}")
        
        # 2. Add columns if missing
        inspector = inspect(session.bind)
        tables = set(inspector.get_table_names())
        
        if "job_postings" in tables:
            cols = {c["name"] for c in inspector.get_columns("job_postings")}
            if "user_id" not in cols:
                logger.info("Adding user_id column to job_postings...")
                session.execute(text("ALTER TABLE job_postings ADD COLUMN user_id INTEGER REFERENCES users(id) ON DELETE CASCADE"))
                session.commit()
            
            # Create index if not exists
            indexes = {idx["name"] for idx in inspector.get_indexes("job_postings")}
            if "ix_job_postings_user_id" not in indexes:
                logger.info("Creating index ix_job_postings_user_id...")
                session.execute(text("CREATE INDEX IF NOT EXISTS ix_job_postings_user_id ON job_postings(user_id)"))
                session.commit()

        if "freelance_leads" in tables:
            cols = {c["name"] for c in inspector.get_columns("freelance_leads")}
            if "user_id" not in cols:
                logger.info("Adding user_id column to freelance_leads...")
                session.execute(text("ALTER TABLE freelance_leads ADD COLUMN user_id INTEGER REFERENCES users(id) ON DELETE CASCADE"))
                session.commit()
            
            indexes = {idx["name"] for idx in inspector.get_indexes("freelance_leads")}
            if "ix_freelance_leads_user_id" not in indexes:
                logger.info("Creating index ix_freelance_leads_user_id...")
                session.execute(text("CREATE INDEX IF NOT EXISTS ix_freelance_leads_user_id ON freelance_leads(user_id)"))
                session.commit()

        if "pipeline_runs" in tables:
            cols = {c["name"] for c in inspector.get_columns("pipeline_runs")}
            if "user_id" not in cols:
                logger.info("Adding user_id column to pipeline_runs...")
                session.execute(text("ALTER TABLE pipeline_runs ADD COLUMN user_id INTEGER REFERENCES users(id) ON DELETE CASCADE"))
                session.commit()
            
            indexes = {idx["name"] for idx in inspector.get_indexes("pipeline_runs")}
            if "ix_pipeline_runs_user_id" not in indexes:
                logger.info("Creating index ix_pipeline_runs_user_id...")
                session.execute(text("CREATE INDEX IF NOT EXISTS ix_pipeline_runs_user_id ON pipeline_runs(user_id)"))
                session.commit()

        if "candidate_profiles" in tables:
            cols = {c["name"] for c in inspector.get_columns("candidate_profiles")}
            if "user_id" not in cols:
                logger.info("Adding user_id column to candidate_profiles...")
                session.execute(text("ALTER TABLE candidate_profiles ADD COLUMN user_id INTEGER REFERENCES users(id) ON DELETE SET NULL"))
                session.commit()

        # 3. Backfill all unassigned records to admin_id
        logger.info(f"Backfilling unassigned records to admin user {admin_id}...")
        
        res_jobs = session.execute(text("UPDATE job_postings SET user_id = :uid WHERE user_id IS NULL"), {"uid": admin_id})
        logger.info(f"Assigned {res_jobs.rowcount} job_postings to user {admin_id}")
        
        res_leads = session.execute(text("UPDATE freelance_leads SET user_id = :uid WHERE user_id IS NULL"), {"uid": admin_id})
        logger.info(f"Assigned {res_leads.rowcount} freelance_leads to user {admin_id}")
        
        res_runs = session.execute(text("UPDATE pipeline_runs SET user_id = :uid WHERE user_id IS NULL"), {"uid": admin_id})
        logger.info(f"Assigned {res_runs.rowcount} pipeline_runs to user {admin_id}")
        
        res_profiles = session.execute(text("UPDATE candidate_profiles SET user_id = :uid WHERE user_id IS NULL"), {"uid": admin_id})
        logger.info(f"Assigned {res_profiles.rowcount} candidate_profiles to user {admin_id}")
        
        session.commit()

        # 4. Update unique constraints to be scoped by (user_id, dedup_hash) in Postgres
        dialect = session.bind.dialect.name
        logger.info(f"Database dialect is: {dialect}")
        if dialect == "postgresql":
            try:
                logger.info("Updating PostgreSQL unique constraints for per-user deduplication...")
                session.execute(text("ALTER TABLE job_postings DROP CONSTRAINT IF EXISTS uq_dedup_hash"))
                session.execute(text("DROP INDEX IF EXISTS uq_dedup_hash"))
                session.execute(text("ALTER TABLE job_postings DROP CONSTRAINT IF EXISTS uq_user_job_dedup"))
                session.execute(text("ALTER TABLE job_postings ADD CONSTRAINT uq_user_job_dedup UNIQUE (user_id, dedup_hash)"))
                
                session.execute(text("ALTER TABLE freelance_leads DROP CONSTRAINT IF EXISTS uq_freelance_dedup_hash"))
                session.execute(text("DROP INDEX IF EXISTS uq_freelance_dedup_hash"))
                session.execute(text("ALTER TABLE freelance_leads DROP CONSTRAINT IF EXISTS uq_user_freelance_dedup"))
                session.execute(text("ALTER TABLE freelance_leads ADD CONSTRAINT uq_user_freelance_dedup UNIQUE (user_id, dedup_hash)"))
                
                # candidate_profiles name uniqueness per user
                session.execute(text("ALTER TABLE candidate_profiles DROP CONSTRAINT IF EXISTS candidate_profiles_name_key"))
                session.execute(text("ALTER TABLE candidate_profiles DROP CONSTRAINT IF EXISTS uq_user_profile_name"))
                session.execute(text("ALTER TABLE candidate_profiles ADD CONSTRAINT uq_user_profile_name UNIQUE (user_id, name)"))
                session.commit()
                logger.info("PostgreSQL unique constraints updated successfully.")
            except Exception as e:
                logger.warning(f"Note on unique constraint update: {e}")
                session.rollback()

        # 5. Verification summary
        total_admin_jobs = session.execute(text("SELECT count(*) FROM job_postings WHERE user_id = :uid"), {"uid": admin_id}).scalar()
        total_admin_leads = session.execute(text("SELECT count(*) FROM freelance_leads WHERE user_id = :uid"), {"uid": admin_id}).scalar()
        total_admin_profiles = session.execute(text("SELECT count(*) FROM candidate_profiles WHERE user_id = :uid"), {"uid": admin_id}).scalar()
        total_admin_runs = session.execute(text("SELECT count(*) FROM pipeline_runs WHERE user_id = :uid"), {"uid": admin_id}).scalar()
        
        unassigned_jobs = session.execute(text("SELECT count(*) FROM job_postings WHERE user_id IS NULL")).scalar()
        
        logger.info("=== Migration Verification ===")
        logger.info(f"Admin User {admin_id} Jobs: {total_admin_jobs}")
        logger.info(f"Admin User {admin_id} Freelance Leads: {total_admin_leads}")
        logger.info(f"Admin User {admin_id} Candidate Profiles: {total_admin_profiles}")
        logger.info(f"Admin User {admin_id} Pipeline Runs: {total_admin_runs}")
        logger.info(f"Unassigned Jobs: {unassigned_jobs} (Should be 0)")
        logger.info("Migration complete!")

if __name__ == "__main__":
    run_migration()
