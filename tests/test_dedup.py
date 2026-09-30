"""
Minimal unit tests for the dedup hashing logic — the one piece of pure
logic in the pipeline that's trivial to test without mocking network/DB.
Run with: pytest tests/
"""
from src.dedup.deduplicator import compute_hash


def test_hash_is_deterministic():
    h1 = compute_hash("Acme Corp", "Backend Engineer", "Great role, visa sponsorship available.")
    h2 = compute_hash("Acme Corp", "Backend Engineer", "Great role, visa sponsorship available.")
    assert h1 == h2


def test_hash_is_case_insensitive():
    h1 = compute_hash("Acme Corp", "Backend Engineer", "Some description")
    h2 = compute_hash("ACME CORP", "backend engineer", "SOME DESCRIPTION")
    assert h1 == h2


def test_hash_changes_with_content():
    h1 = compute_hash("Acme Corp", "Backend Engineer", "Description A")
    h2 = compute_hash("Acme Corp", "Backend Engineer", "Description B")
    assert h1 != h2


def test_hash_handles_none_fields():
    # Should not raise even if a scraped field is missing
    h = compute_hash(None, "Backend Engineer", None)
    assert isinstance(h, str) and len(h) == 32
