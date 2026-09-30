"""Tests for multi-dimensional accuracy evaluation:
- Continent detection (Europe, MENA, Global Remote, North America, Asia-Pacific)
- Experience level & years extraction (entry, mid, senior, lead)
- Degree requirement extraction (bachelor, master, phd, none)
- Visa sponsorship & relocation categorization
- Moroccan entry-level engineer scoring calibration (Switzerland / Europe / Global Remote)
"""
from src.evaluation.heuristic_scorer import (
    extract_continent,
    extract_experience_requirement,
    extract_degree_requirement,
    extract_visa_relocation,
    evaluate_heuristic,
)


def test_extract_continent():
    # Switzerland & European countries
    assert extract_continent("Zurich, Switzerland", False, "") == "Europe"
    assert extract_continent("Geneva", False, "Office in Geneva, Switzerland") == "Europe"
    assert extract_continent("Berlin, Germany", False, "") == "Europe"
    assert extract_continent("Paris, France", False, "") == "Europe"
    assert extract_continent("Amsterdam, Netherlands", False, "") == "Europe"
    assert extract_continent("London, United Kingdom", False, "") == "Europe"

    # Global Remote
    assert extract_continent("Remote", True, "Work from anywhere in the world worldwide") == "Global Remote"
    assert extract_continent("Worldwide", True, "Global remote team") == "Global Remote"

    # MENA
    assert extract_continent("Casablanca, Morocco", False, "") == "MENA"
    assert extract_continent("Rabat, Morocco", False, "") == "MENA"
    assert extract_continent("Dubai, UAE", False, "") == "MENA"

    # North America
    assert extract_continent("San Francisco, CA, USA", False, "") == "North America"
    assert extract_continent("Toronto, Canada", False, "") == "North America"

    # Asia-Pacific
    assert extract_continent("Singapore", False, "") == "Asia-Pacific"
    assert extract_continent("Tokyo, Japan", False, "") == "Asia-Pacific"


def test_extract_experience_requirement():
    # Entry level
    lvl, yrs = extract_experience_requirement(
        "We are looking for a Junior Backend Developer. 0-2 years of experience or fresh graduates welcome.",
        "Junior Backend Developer"
    )
    assert lvl == "entry"
    assert yrs in (0, 1, 2)

    # Entry level with no years stated but entry title
    lvl, yrs = extract_experience_requirement(
        "Join our team as an Entry Level Software Engineer. Mentorship provided.",
        "Entry Level Software Engineer"
    )
    assert lvl == "entry"

    # Mid level
    lvl, yrs = extract_experience_requirement(
        "Requires 3-5 years of professional backend engineering experience in Java.",
        "Software Engineer"
    )
    assert lvl == "mid"
    assert yrs in (3, 4, 5)

    # Senior level
    lvl, yrs = extract_experience_requirement(
        "Minimum 6+ years of production experience building high-scale distributed systems.",
        "Senior Java Engineer"
    )
    assert lvl == "senior"
    assert yrs >= 5

    # Lead / Principal
    lvl, yrs = extract_experience_requirement(
        "Looking for a Principal Architect with 10+ years of software engineering experience.",
        "Principal Architect"
    )
    assert lvl == "lead"
    assert yrs >= 8


def test_extract_degree_requirement():
    # PhD
    assert extract_degree_requirement("Requires a Ph.D. or Doctorate in Computer Science or Machine Learning.") == "phd"

    # Master
    assert extract_degree_requirement("Master's degree or Diplôme d'ingénieur in Computer Science or related.") == "master"
    assert extract_degree_requirement("Minimum Bac+5 / Master of Science required.") == "master"

    # Bachelor
    assert extract_degree_requirement("Bachelor's degree in Computer Science, Software Engineering or equivalent experience.") == "bachelor"
    assert extract_degree_requirement("Licence or Bac+3 required.") == "bachelor"

    # None / Flexible
    assert extract_degree_requirement("No degree required. We value proven open-source contributions and portfolio.") == "none"


def test_extract_visa_relocation():
    # Sponsored
    cat, reloc = extract_visa_relocation(
        "We provide full visa sponsorship and relocation support for qualified international candidates.",
        is_remote=False
    )
    assert cat == "sponsored"
    assert reloc is True

    # Relocation only
    cat, reloc = extract_visa_relocation(
        "Relocation assistance package included for candidates moving to Berlin.",
        is_remote=False
    )
    assert cat in ("relocation", "sponsored")
    assert reloc is True

    # Global remote
    cat, reloc = extract_visa_relocation(
        "Work from anywhere in the world. 100% remote contract.",
        is_remote=True
    )
    assert cat == "remote_global"

    # Restricted / Local auth required (e.g. Swiss local quotas)
    cat, reloc = extract_visa_relocation(
        "Candidates must already possess valid Swiss work authorization (Permit B, C, or EU/EFTA passport). No visa sponsorship provided.",
        is_remote=False
    )
    assert cat == "restricted"


def test_entry_level_moroccan_engineer_scoring():
    """Simulates an Entry-Level Engineer from Morocco targeting Switzerland / Europe / Remote."""
    candidate = {
        "name": "Hamza - Junior Engineer",
        "current_location": "Salé, Morocco",
        "experience_years": 1,
        "target_experience_level": "entry",
        "degree_level": "bachelor",
        "target_locations": ["Switzerland", "Germany", "France", "Remote"],
        "target_titles": ["Software Engineer", "Java Developer", "Backend Engineer"],
        "core_stack": ["Java", "Spring Boot", "React", "PostgreSQL", "Docker", "REST APIs"],
        "keywords": ["java", "spring boot", "react", "postgresql", "docker"],
        "negative_keywords": ["cobol", "wordpress", "php only"],
    }

    # Job 1: True Junior Role in Zurich with Visa Sponsorship
    junior_job = {
        "title": "Junior Java & React Developer",
        "company": "Swiss FinTech AG",
        "location": "Zurich, Switzerland",
        "is_remote": False,
        "raw_description": (
            "We are seeking a Junior Java Developer (0-2 years experience). "
            "Stack: Java, Spring Boot, React, Docker, PostgreSQL. "
            "Bachelor's degree in Computer Science required. "
            "We offer full Swiss visa sponsorship and relocation support for top talent!"
        ),
    }
    res1 = evaluate_heuristic(junior_job, candidate)
    assert res1["continent"] == "Europe"
    assert res1["experience_level"] == "entry"
    assert res1["degree_required"] == "bachelor"
    assert res1["degree_matched"] is True
    assert res1["visa_category"] == "sponsored"
    assert res1["relocation_detected"] is True
    assert res1["score"] >= 80, f"Expected >= 80 for perfect junior match, got {res1['score']}"
    assert "🟢 Entry-Level Match" in res1["reason"]

    # Job 2: Senior Role (8+ years) in Geneva with NO Visa Sponsorship
    senior_job = {
        "title": "Senior Lead Java Architect",
        "company": "Geneva Enterprise",
        "location": "Geneva, Switzerland",
        "is_remote": False,
        "raw_description": (
            "Looking for a Senior Principal Architect with at least 8+ years of production experience. "
            "Master's or PhD degree required. "
            "Must already have valid Swiss work authorization. No visa sponsorship available."
        ),
    }
    res2 = evaluate_heuristic(senior_job, candidate)
    assert res2["continent"] == "Europe"
    assert res2["experience_level"] in ("senior", "lead")
    assert res2["visa_category"] == "restricted"
    assert res2["score"] < 40, f"Expected severe penalty for junior on senior role, got {res2['score']}"
    assert "Senior" in res2["reason"] or "Penalty" in res2["reason"] or "Restricted" in res2["reason"]

    # Job 3: Global Remote Java Developer (work from Morocco)
    remote_job = {
        "title": "Fullstack Java & React Developer",
        "company": "Global Remote Tech",
        "location": "Remote",
        "is_remote": True,
        "raw_description": (
            "We are hiring a Java & Spring Boot developer. Work from anywhere in the world! "
            "1-3 years of experience. Bachelor's degree or equivalent experience. "
            "Build REST APIs with PostgreSQL and React frontend."
        ),
    }
    res3 = evaluate_heuristic(remote_job, candidate)
    assert res3["continent"] == "Global Remote"
    assert res3["visa_category"] == "remote_global"
    assert res3["score"] >= 70, f"Expected >= 70 for global remote match, got {res3['score']}"
