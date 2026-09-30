import sys
import os
sys.path.insert(0, os.path.abspath("."))

from src.evaluation.language import is_french_job
from src.evaluation.heuristic_scorer import evaluate_heuristic
from src.application.auto_apply import _application_subject, _application_body, apply_to_job
from src.outreach.message_generator import generate_outreach_draft


def test_french_job_detection():
    print("=== Testing French Job Detection ===")
    
    # 1. Location in France
    job_paris = {"title": "Software Engineer", "company": "Doctolib", "location": "Paris, France"}
    assert is_french_job(job_paris) is True, "Failed: Paris, France should be detected as French"
    
    job_remote_fr = {"title": "Backend Developer", "company": "PayFit", "location": "Remote (France)"}
    assert is_french_job(job_remote_fr) is True, "Failed: Remote (France) should be detected as French"
    
    job_lyon = {"title": "Fullstack Engineer", "company": "TechCorp", "location": "Lyon"}
    assert is_french_job(job_lyon) is True, "Failed: Lyon should be detected as French"
    
    # 2. French Title
    job_title_fr = {"title": "Développeur Python / Django", "company": "Startup", "location": "Remote"}
    assert is_french_job(job_title_fr) is True, "Failed: Développeur should be detected as French"
    
    # 3. French Description
    job_desc_fr = {
        "title": "Software Engineer",
        "company": "Enterprise",
        "location": "Remote",
        "raw_description": "Nous recherchons un développeur pour rejoindre notre équipe. Profil recherché: maîtrise de Java et Spring Boot."
    }
    assert is_french_job(job_desc_fr) is True, "Failed: French keywords in description should be detected as French"
    
    # 4. English Job (US / UK / Global)
    job_us = {"title": "Senior Backend Engineer", "company": "Stripe", "location": "San Francisco, CA, USA", "raw_description": "Looking for a backend engineer."}
    assert is_french_job(job_us) is False, "Failed: US job should NOT be detected as French"
    
    job_london = {"title": "DevOps Engineer", "company": "Revolut", "location": "London, UK", "raw_description": "Looking for a cloud specialist."}
    assert is_french_job(job_london) is False, "Failed: London job should NOT be detected as French"

    print(" [PASS] All 6 detection test cases passed!")


def test_application_language_routing():
    print("\n=== Testing Application Subject & Body Routing ===")
    
    # French Job
    french_job = {
        "id": 101,
        "title": "Ingénieur Backend Python",
        "company": "Qonto",
        "location": "Paris, France",
        "raw_description": "Nous recherchons un ingénieur backend pour concevoir nos APIs financières.",
        "recruiter_pitch_fr": "Bonjour l'équipe Qonto,\n\nJe suis très intéressé par votre poste d'Ingénieur Backend Python.",
        "recruiter_pitch_en": "Hi Qonto Team,\n\nI am interested in your Backend position.",
        "application_emails": ["recrutement@qonto.com"]
    }
    
    subj_fr = _application_subject(french_job)
    body_fr = _application_body(french_job)
    
    print(f"French Job Subject: {subj_fr}")
    print(f"French Job Body snippet: {body_fr[:80]}...")
    
    assert "Candidature" in subj_fr, f"Expected 'Candidature' in French subject, got: {subj_fr}"
    assert "Bonjour" in body_fr, f"Expected French greeting in French body, got: {body_fr}"
    
    # English Job
    english_job = {
        "id": 102,
        "title": "Backend Engineer",
        "company": "Spotify",
        "location": "Stockholm, Sweden",
        "raw_description": "We are seeking a backend engineer to join our playback core team.",
        "recruiter_pitch_fr": "Bonjour l'équipe Spotify...",
        "recruiter_pitch_en": "Hi Spotify Team,\n\nI am reaching out regarding the Backend Engineer role.",
        "application_email_en": "Dear Spotify Hiring Team,\n\nI am writing to submit my application.",
        "application_emails": ["jobs@spotify.com"]
    }
    
    subj_en = _application_subject(english_job)
    body_en = _application_body(english_job)
    
    print(f"English Job Subject: {subj_en}")
    print(f"English Job Body snippet: {body_en[:80]}...")
    
    assert "Application" in subj_en, f"Expected 'Application' in English subject, got: {subj_en}"
    assert "Dear" in body_en or "Hi" in body_en, f"Expected English greeting in English body, got: {body_en}"

    print(" [PASS] Language routing correctly separated French vs English applications!")


def test_heuristic_evaluation_generation():
    print("\n=== Testing Heuristic Bilingual Pitch Generation ===")
    
    job = {
        "title": "Développeur Java Spring",
        "company": "Société Générale",
        "location": "Paris, France",
        "raw_description": "Poste en CDI basé à Paris. Maîtrise de Java, Spring Boot, Docker requise."
    }
    
    res = evaluate_heuristic(job, {"name": "Hamza", "current_location": "Morocco"})
    assert "application_subject_fr" in res, "Missing application_subject_fr in heuristic output"
    assert "application_email_fr" in res, "Missing application_email_fr in heuristic output"
    assert "Candidature" in res["application_subject_fr"]
    assert "Bonjour" in res["application_email_fr"]
    
    print(" [PASS] Heuristic evaluation successfully generated dedicated French application fields!")


if __name__ == "__main__":
    try:
        test_french_job_detection()
        test_application_language_routing()
        test_heuristic_evaluation_generation()
        print("\n🎉 ALL FRENCH APPLICATION LANGUAGE TESTS PASSED 100%!")
    except AssertionError as e:
        print(f"\n❌ TEST FAILED: {e}")
        sys.exit(1)
