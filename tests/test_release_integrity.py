from pathlib import Path


def test_dockerfile_is_lightweight():
    p = Path("Dockerfile.microservices") if Path("Dockerfile.microservices").exists() else Path("Dockerfile")
    text = p.read_text(encoding="utf-8")
    assert "FROM python:3.11-slim" in text
    assert "playwright install" not in text


def test_compose_has_dedicated_services():
    p = Path("docker-compose.microservices.yml") if Path("docker-compose.microservices.yml").exists() else Path("docker-compose.yml")
    text = p.read_text(encoding="utf-8")
    for service in ("postgres:", "ollama:", "ollama-init:", "browser:", "app:", "inbox_monitor:", "dashboard:"):
        assert service in text
    assert "ws://browser:3000/" in text


def test_remote_queries_do_not_send_invalid_indeed_country():
    from config.candidate_profile import SEARCH_MATRIX
    remote = [q for q in SEARCH_MATRIX if q.get("is_remote")]
    assert remote
    assert all(q.get("sites") and "indeed" not in q["sites"] and "glassdoor" not in q["sites"] for q in remote)


def test_candidate_profile_file_is_present():
    assert Path("candidate_data/profile.json").is_file()
