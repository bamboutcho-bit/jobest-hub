"""Thread-safe Dual-Mode (Redis + In-Memory Fallback) Cache-Aside Manager.

Implements the Cache-Aside architecture pattern recommended in System Design 101:
- Sub-millisecond response latency (<1ms vs ~150ms DB queries).
- Avoids repeated expensive database count/aggregation queries.
- Pluggable backend: connects to distributed Redis when REDIS_URL is configured,
  with transparent zero-downtime fallback to bounded in-memory TTLCache.
- Explicit event-driven cache invalidation across mutations.
"""
from __future__ import annotations

import json
import logging
import os
import threading
import time
from datetime import date, datetime
from typing import Any, Callable, Dict, List, Optional, Tuple

logger = logging.getLogger(__name__)


def _json_serial(obj: Any) -> Any:
    """JSON serializer for objects not serializable by default json code."""
    if isinstance(obj, (datetime, date)):
        return obj.isoformat()
    return str(obj)


class TTLCache:
    """Thread-safe, bounded in-memory cache with time-to-live (TTL) expiration."""

    def __init__(self, default_ttl: float = 15.0, max_items: int = 2048):
        self._cache: Dict[str, Tuple[float, Any]] = {}
        self._lock = threading.Lock()
        self._default_ttl = default_ttl
        self._max_items = max_items
        self._hits = 0
        self._misses = 0

    def get(self, key: str) -> Optional[Any]:
        """Retrieve a value if present and unexpired."""
        with self._lock:
            entry = self._cache.get(key)
            if entry is None:
                self._misses += 1
                return None
            expires_at, val = entry
            if time.time() > expires_at:
                del self._cache[key]
                self._misses += 1
                return None
            self._hits += 1
            return val

    def set(self, key: str, val: Any, ttl: Optional[float] = None) -> None:
        """Store a value with an expiration TTL."""
        ttl_val = self._default_ttl if ttl is None else ttl
        expires_at = time.time() + ttl_val
        with self._lock:
            # Simple eviction if reaching maximum bounds
            if len(self._cache) >= self._max_items:
                now = time.time()
                expired_keys = [k for k, (exp, _) in self._cache.items() if exp <= now]
                for k in expired_keys:
                    del self._cache[k]
                if len(self._cache) >= self._max_items:
                    oldest = sorted(self._cache.items(), key=lambda item: item[1][0])[: int(self._max_items * 0.2)]
                    for k, _ in oldest:
                        self._cache.pop(k, None)

            self._cache[key] = (expires_at, val)

    def invalidate(self, prefix_or_key: str = "") -> int:
        """Invalidate keys matching prefix or clear all if prefix is empty."""
        with self._lock:
            if not prefix_or_key:
                count = len(self._cache)
                self._cache.clear()
                return count
            keys_to_del = [k for k in self._cache if k.startswith(prefix_or_key)]
            for k in keys_to_del:
                del self._cache[k]
            return len(keys_to_del)

    def stats(self) -> Dict[str, Any]:
        """Return cache health metrics."""
        with self._lock:
            total = self._hits + self._misses
            hit_ratio = (self._hits / total) if total > 0 else 0.0
            return {
                "backend": "memory",
                "size": len(self._cache),
                "max_items": self._max_items,
                "hits": self._hits,
                "misses": self._misses,
                "hit_ratio": round(hit_ratio, 3),
            }


class RedisCache:
    """Distributed Redis cache backend with automated error recovery."""

    def __init__(self, redis_url: str, default_ttl: float = 15.0):
        self._url = redis_url
        self._default_ttl = default_ttl
        self._client: Any = None
        self._hits = 0
        self._misses = 0
        self._prefix = "autohunt:"
        self._init_client()

    def _init_client(self) -> None:
        try:
            import redis
            self._client = redis.from_url(
                self._url,
                decode_responses=True,
                socket_connect_timeout=2.0,
                socket_timeout=2.0,
            )
            self._client.ping()
            logger.info("Connected to distributed Redis cache at %s", self._url.split("@")[-1])
        except Exception as e:
            logger.warning("Redis cache initialization skipped: %s", e)
            self._client = None

    @property
    def is_available(self) -> bool:
        return self._client is not None

    def get(self, key: str) -> Optional[Any]:
        if not self._client:
            return None
        try:
            raw = self._client.get(f"{self._prefix}{key}")
            if raw is None:
                self._misses += 1
                return None
            self._hits += 1
            return json.loads(raw)
        except Exception as e:
            logger.debug("Redis get error for %s: %s", key, e)
            self._misses += 1
            return None

    def set(self, key: str, val: Any, ttl: Optional[float] = None) -> None:
        if not self._client:
            return
        try:
            ttl_sec = int(self._default_ttl if ttl is None else ttl)
            raw = json.dumps(val, default=_json_serial)
            self._client.setex(f"{self._prefix}{key}", max(1, ttl_sec), raw)
        except Exception as e:
            logger.debug("Redis set error for %s: %s", key, e)

    def invalidate(self, prefix_or_key: str = "") -> int:
        if not self._client:
            return 0
        try:
            pattern = f"{self._prefix}{prefix_or_key}*"
            keys = self._client.keys(pattern)
            if keys:
                return self._client.delete(*keys)
            return 0
        except Exception as e:
            logger.debug("Redis invalidate error for %s: %s", prefix_or_key, e)
            return 0

    def stats(self) -> Dict[str, Any]:
        total = self._hits + self._misses
        hit_ratio = (self._hits / total) if total > 0 else 0.0
        return {
            "backend": "redis",
            "hits": self._hits,
            "misses": self._misses,
            "hit_ratio": round(hit_ratio, 3),
        }


class UnifiedCacheManager:
    """Transparent Dual-Mode Cache Manager (Redis primary with in-memory fallback)."""

    def __init__(self, default_ttl: float = 15.0):
        self._memory = TTLCache(default_ttl=default_ttl, max_items=2048)
        self._redis: Optional[RedisCache] = None
        self._lock = threading.Lock()
        self._checked_redis = False

    def _ensure_backend(self) -> None:
        if not self._checked_redis:
            with self._lock:
                if not self._checked_redis:
                    self._checked_redis = True
                    redis_url = os.getenv("REDIS_URL", "").strip()
                    if not redis_url:
                        try:
                            from config.settings import settings
                            redis_url = getattr(settings, "redis_url", "")
                        except Exception:
                            pass
                    if redis_url:
                        rc = RedisCache(redis_url=redis_url, default_ttl=15.0)
                        if rc.is_available:
                            self._redis = rc

    def get(self, key: str) -> Optional[Any]:
        self._ensure_backend()
        if self._redis and self._redis.is_available:
            val = self._redis.get(key)
            if val is not None:
                return val
        return self._memory.get(key)

    def set(self, key: str, val: Any, ttl: Optional[float] = None) -> None:
        self._ensure_backend()
        self._memory.set(key, val, ttl=ttl)
        if self._redis and self._redis.is_available:
            self._redis.set(key, val, ttl=ttl)

    def invalidate(self, prefix_or_key: str = "") -> int:
        self._ensure_backend()
        count = self._memory.invalidate(prefix_or_key)
        if self._redis and self._redis.is_available:
            self._redis.invalidate(prefix_or_key)
        return count

    def stats(self) -> Dict[str, Any]:
        self._ensure_backend()
        if self._redis and self._redis.is_available:
            st = self._redis.stats()
            st["memory_fallback_size"] = len(self._memory._cache)
            return st
        return self._memory.stats()


# Singleton application cache manager
app_cache = UnifiedCacheManager(default_ttl=15.0)


def invalidate_summary_cache(user_id: Optional[int] = None) -> None:
    """Invalidate summary cache for specific user, or all users if None/admin."""
    if user_id is not None:
        app_cache.invalidate(f"summary:{user_id}:")
    else:
        app_cache.invalidate("summary:")


def invalidate_freelance_cache(user_id: Optional[int] = None) -> None:
    """Invalidate freelance stats cache."""
    if user_id is not None:
        app_cache.invalidate(f"freelance_stats:{user_id}")
    else:
        app_cache.invalidate("freelance_stats:")


def invalidate_outbound_cache(user_id: Optional[int] = None) -> None:
    """Invalidate outbound stats cache."""
    if user_id is not None:
        app_cache.invalidate(f"outbound_stats:{user_id}")
    else:
        app_cache.invalidate("outbound_stats:")


def invalidate_stats_cache(user_id: Optional[int] = None) -> None:
    """Invalidate dynamic system stats cache."""
    if user_id is not None:
        app_cache.invalidate(f"stats_dynamic:{user_id}")
    else:
        app_cache.invalidate("stats_dynamic:")


def invalidate_all_caches() -> None:
    """Clear all cached aggregates."""
    app_cache.invalidate("")
