"""Unit and integration tests for System Design 101 improvements:
- Dual-Mode Cache-Aside architecture (UnifiedCacheManager, TTLCache, Redis fallback).
- Cache invalidation across domains.
- System telemetry endpoint (/api/system/metrics).
- Keyset / Cursor-based pagination on /api/jobs.
"""
import time
from fastapi.testclient import TestClient
from src.dashboard.app import app
from src.storage.cache import (
    UnifiedCacheManager,
    TTLCache,
    app_cache,
    invalidate_summary_cache,
    invalidate_outbound_cache,
    invalidate_stats_cache,
    invalidate_freelance_cache,
    invalidate_all_caches,
)
from src.storage.db import get_session
from src.storage.models import JobPosting, PipelineStage, User

client = TestClient(app)


def test_ttl_cache_in_memory_behavior():
    cache = TTLCache(default_ttl=0.2, max_items=10)
    cache.set("user:1:summary", {"total": 42})
    assert cache.get("user:1:summary") == {"total": 42}

    # Stats verify hit
    st = cache.stats()
    assert st["hits"] >= 1
    assert st["hit_ratio"] > 0.0

    # Wait for expiration
    time.sleep(0.25)
    assert cache.get("user:1:summary") is None
    assert cache.stats()["misses"] >= 1


def test_prefix_invalidation():
    cache = TTLCache(default_ttl=60.0)
    cache.set("summary:1:true", {"v": 1})
    cache.set("summary:1:false", {"v": 2})
    cache.set("summary:2:true", {"v": 3})
    cache.set("freelance_stats:1", {"v": 4})

    # Invalidate user 1 summary only
    del_count = cache.invalidate("summary:1:")
    assert del_count == 2
    assert cache.get("summary:1:true") is None
    assert cache.get("summary:1:false") is None
    assert cache.get("summary:2:true") == {"v": 3}
    assert cache.get("freelance_stats:1") == {"v": 4}


def test_unified_cache_manager_graceful_fallback():
    mgr = UnifiedCacheManager(default_ttl=30.0)
    mgr.set("metrics:test", {"cpu": 12})
    assert mgr.get("metrics:test") == {"cpu": 12}

    st = mgr.stats()
    assert "hits" in st
    assert "hit_ratio" in st
    assert st["backend"] in ("memory", "redis")


def test_system_metrics_endpoint():
    resp = client.get("/api/system/metrics")
    assert resp.status_code == 200
    data = resp.json()
    assert data["ok"] is True
    assert "cache" in data
    assert "active_websockets" in data
    assert "broker" in data
    assert "hit_ratio" in data["cache"]


def test_cursor_based_jobs_pagination():
    import uuid
    u = uuid.uuid4().hex[:8]
    with get_session() as session:
        # Create 3 jobs for testing pagination
        j1 = JobPosting(title="Cursor Job 1", company="TestCo", dedup_hash=f"cursor_test_1_{u}")
        j2 = JobPosting(title="Cursor Job 2", company="TestCo", dedup_hash=f"cursor_test_2_{u}")
        j3 = JobPosting(title="Cursor Job 3", company="TestCo", dedup_hash=f"cursor_test_3_{u}")
        session.add_all([j1, j2, j3])
        session.commit()
        id1, id2, id3 = j1.id, j2.id, j3.id

    # Normal fetch without cursor
    resp = client.get("/api/jobs?limit=10")
    assert resp.status_code == 200
    all_jobs = resp.json()
    assert len(all_jobs) >= 3

    # Fetch with cursor: should only return jobs with id < id3
    resp_cursor = client.get(f"/api/jobs?limit=10&cursor={id3}")
    assert resp_cursor.status_code == 200
    cursor_jobs = resp_cursor.json()
    cursor_ids = [j["id"] for j in cursor_jobs]
    assert id3 not in cursor_ids
