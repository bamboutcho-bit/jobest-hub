import pytest
from src.storage.db import get_session, init_db
from src.candidate.profile_manager import (
    create_profile,
    get_active_profile,
    set_active_profile,
    list_profiles,
    update_profile,
    delete_profile,
    generate_search_matrix,
    generate_freelance_queries,
)
from src.evaluation.heuristic_scorer import evaluate_heuristic
from config.candidate_profile import CANDIDATE_PROFILE, SEARCH_MATRIX


@pytest.fixture(autouse=True)
def setup_test_db():
    init_db()


def test_profile_lifecycle():
    with get_session() as session:
        # Create a new custom profile
        created = create_profile(
            data={
                "name": "Python AI Architect",
                "headline": "Senior Python & Generative AI Engineer",
                "target_titles": ["AI Engineer", "Python Backend Developer"],
                "core_stack": ["Python", "FastAPI", "PyTorch", "Docker"],
                "keywords": ["llm", "rag", "agents", "langchain"],
                "negative_keywords": ["php", "wordpress"],
                "target_locations": ["Remote", "Germany", "United Kingdom"],
                "experience_years": 6,
                "is_active": False,
            },
            session=session
        )

        assert created["id"] is not None
        assert created["name"] == "Python AI Architect"
        assert "AI Engineer" in created["target_titles"]
        assert "rag" in created["keywords"]

        # List profiles — use keyword arg to avoid confusion with user_id
        profiles = list_profiles(session=session)
        assert any(p["name"] == "Python AI Architect" for p in profiles)

        # Switch active profile
        activated = set_active_profile(created["id"], session=session)
        assert activated["is_active"] is True

        active = get_active_profile(session=session)
        assert active["id"] == created["id"]
        assert active["name"] == "Python AI Architect"

        # Update profile
        updated = update_profile(
            profile_id=created["id"],
            data={
                "keywords": ["llm", "rag", "langgraph", "vector db"],
                "freelance_hourly_usd": 85,
            },
            session=session
        )
        assert "vector db" in updated["keywords"]
        assert updated["freelance_hourly_rate"] == 85

        # Dynamic search matrix generation
        matrix = generate_search_matrix(updated)
        assert len(matrix) > 0
        terms = [m["term"] for m in matrix]
        assert any("AI Engineer" in t for t in terms)
        assert any(m["is_remote"] for m in matrix)

        # Dynamic freelance queries generation
        f_queries = generate_freelance_queries(updated)
        assert len(f_queries) > 0
        assert any("python" in q["term"].lower() for q in f_queries)

        # Clean up test profile
        delete_profile(created["id"], session=session)


def test_proxy_candidate_profile_compatibility():
    # CANDIDATE_PROFILE and SEARCH_MATRIX proxy objects should resolve without error
    profile = CANDIDATE_PROFILE
    assert isinstance(profile, dict)
    assert "name" in profile
    assert "target_titles" in profile
    assert "core_stack" in profile

    # Access via dict key
    assert len(profile["target_titles"]) > 0

    # Search matrix proxy list
    matrix = SEARCH_MATRIX
    assert len(matrix) > 0
    assert "term" in matrix[0]
    assert "location" in matrix[0]


def test_heuristic_evaluator():
    java_profile = {
        "name": "Hamza",
        "target_titles": ["Backend Engineer", "Java Developer", "Spring Boot Developer"],
        "core_stack": ["Java", "Spring Boot", "PostgreSQL", "Docker"],
        "keywords": ["java", "spring boot", "microservices"],
        "negative_keywords": ["wordpress", "php"],
        "current_location": "Morocco"
    }

    job_match = {
        "id": 999,
        "title": "Senior Java Spring Boot Engineer",
        "description": "We are seeking an experienced Java backend engineer skilled in Spring Boot, Microservices, PostgreSQL, and Docker. Visa sponsorship is available for qualified international candidates.",
        "company": "Tech Global Inc",
        "location": "Berlin, Germany"
    }

    res = evaluate_heuristic(job_match, profile=java_profile)
    assert res["match_score"] >= 50
    assert res["visa_sponsorship_detected"] is True
    assert "Java" in res["key_skills_matched"] or "Spring Boot" in res["key_skills_matched"]
    assert res["recruiter_pitch_en"] is not None
    assert len(res["recruiter_pitch_en"]) > 20


def test_heuristic_evaluator_unrelated_job():
    java_profile = {
        "name": "Hamza",
        "target_titles": ["Backend Engineer", "Java Developer", "Spring Boot Developer"],
        "core_stack": ["Java", "Spring Boot", "PostgreSQL", "Docker"],
        "keywords": ["java", "spring boot", "microservices"],
        "negative_keywords": ["wordpress", "php"],
        "current_location": "Morocco"
    }

    job_unrelated = {
        "id": 1000,
        "title": "Senior PHP WordPress Theme Designer",
        "description": "Building custom WordPress themes with PHP, jQuery, and WooCommerce. No visa sponsorship.",
        "company": "Design Studio",
        "location": "Casablanca, Morocco"
    }

    res = evaluate_heuristic(job_unrelated, profile=java_profile)
    assert res["match_score"] < 50
    assert res["visa_sponsorship_detected"] is False
