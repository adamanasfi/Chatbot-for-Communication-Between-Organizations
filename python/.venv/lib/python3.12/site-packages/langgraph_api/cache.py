"""Basic distributed key/value cache for internal use."""

from datetime import timedelta
from typing import Any

import orjson

from langgraph_api.feature_flags import IS_POSTGRES_OR_GRPC_BACKEND
from langgraph_api.utils.cache import LRUCache

MAX_CACHE_TTL = timedelta(hours=24)


def _clamp_ttl(ttl: timedelta | None) -> timedelta:
    """Normalise caller-supplied TTL: None/0 → MAX, >MAX → MAX."""
    if ttl is None or ttl <= timedelta(0):
        return MAX_CACHE_TTL
    return min(ttl, MAX_CACHE_TTL)


if IS_POSTGRES_OR_GRPC_BACKEND:
    from langgraph_api.grpc.ops.cache import cache_get as _cache_get
    from langgraph_api.grpc.ops.cache import cache_set as _cache_set
else:
    _CACHE: LRUCache[bytes] = LRUCache(ttl=MAX_CACHE_TTL.total_seconds())

    async def _cache_get(key: str) -> bytes | None:
        return await _CACHE.get(key)

    async def _cache_set(key: str, value: bytes, ttl: timedelta | None = None) -> None:
        ttl = _clamp_ttl(ttl)
        _CACHE.set(key, value)


async def cache_get(key: str) -> Any | None:
    """Get a value from the cache."""
    val = await _cache_get(key)
    return orjson.loads(val) if val is not None else None


async def cache_set(key: str, value: Any, ttl: timedelta | None = None) -> None:
    """Set a value in the cache.

    Args:
        key: The cache key.
        value: The value to cache (must be serializable to JSON).
        ttl: Optional time-to-live.  Capped at 1 day; ``None`` or zero
            defaults to 1 day.
    """
    ttl = _clamp_ttl(ttl)
    await _cache_set(key, orjson.dumps(value), ttl)
