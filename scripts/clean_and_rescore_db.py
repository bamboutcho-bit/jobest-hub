import sys
sys.path.insert(0, "/app")

import re
from sqlalchemy import select, or_, func
from src.storage.db import get_session
from src.storage.models import JobPosting, PipelineStage, CandidateProfile
from src.candidate.profile_manager import get_active_profile
from src.evaluation.heuristic_scorer import evaluate_job_heuristic
from src.evaluation.language import is_french_job

def run_cleanup_and_rescore():
    print("=== Starting Database Cleanup and Rescore ===")
    with get_session() as session:
        # 1. Cleanse contaminated text
        contaminated = session.scalars(
            select(JobPosting).where(
                or_(
                    JobPosting.application_email_body.ilike("%Python AI Architect%"),
                    JobPosting.recruiter_pitch_en.ilike("%Python AI Architect%"),
                    JobPosting.recruiter_pitch_fr.ilike("%Python AI Architect%"),
                    JobPosting.application_subject.ilike("%Python AI Architect%"),
                )
            )
        ).all()
        print(f"Found {len(contaminated)} jobs contaminated with 'Python AI Architect'. Cleaning...")
        for j in contaminated:
            if j.application_email_body:
                j.application_email_body = j.application_email_body.replace("Python AI Architect", "Hamza Oukhouya")
            if j.recruiter_pitch_en:
                j.recruiter_pitch_en = j.recruiter_pitch_en.replace("Python AI Architect", "Hamza Oukhouya")
            if j.recruiter_pitch_fr:
                j.recruiter_pitch_fr = j.recruiter_pitch_fr.replace("Python AI Architect", "Hamza Oukhouya")
            if j.application_subject:
                j.application_subject = j.application_subject.replace("Python AI Architect", "Hamza Oukhouya")
        session.commit()
        print("Cleaned contaminated job text.")

        # 2. Reset transient blocked statuses (e.g., old bad credentials)
        bad_cred_jobs = session.scalars(
            select(JobPosting).where(
                JobPosting.application_status == "blocked",
                JobPosting.applied_at.is_(None),
                JobPosting.application_error.ilike("%535%")
            )
        ).all()
        print(f"Resetting {len(bad_cred_jobs)} jobs previously blocked due to bad SMTP credentials...")
        for j in bad_cred_jobs:
            j.application_status = "not_attempted"
            j.application_error = None
        session.commit()

        # 3. Re-evaluate unapplied jobs with emails against Hamza Oukhouya
        profile = get_active_profile(user_id=1, session=session)
        print(f"Active Profile: {profile.get('name')} | {profile.get('headline')}")

        unapplied_email_jobs = session.scalars(
            select(JobPosting).where(
                JobPosting.application_emails.isnot(None),
                JobPosting.application_emails != "",
                JobPosting.applied_at.is_(None),
            )
        ).all()
        print(f"Evaluating {len(unapplied_email_jobs)} unapplied jobs with recruiter emails...")
        high_matches = 0
        for j in unapplied_email_jobs:
            eval_res = evaluate_job_heuristic(j, profile=profile)
            score = eval_res.get("match_score", 0)
            j.match_score = score
            if score >= 60:
                j.pipeline_stage = PipelineStage.EVALUATED_MATCH
                high_matches += 1
            else:
                j.pipeline_stage = PipelineStage.EVALUATED_LOW

            is_fr = is_french_job(j)
            j.recruiter_pitch_en = eval_res.get("recruiter_pitch_en") or j.recruiter_pitch_en
            j.recruiter_pitch_fr = eval_res.get("recruiter_pitch_fr") or j.recruiter_pitch_fr
            j.application_subject = (eval_res.get("application_subject_fr") if is_fr else eval_res.get("application_subject")) or j.application_subject
            j.application_email_body = (eval_res.get("application_email_fr") if is_fr else eval_res.get("application_email_en")) or j.application_email_body
        
        session.commit()
        print(f"Finished evaluation! {high_matches} jobs now score >= 60% with active profile and have direct recruiter emails.")

if __name__ == "__main__":
    run_cleanup_and_rescore()
