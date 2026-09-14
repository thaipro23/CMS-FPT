"""Pure helpers for lightweight authenticated-user presence tracking."""
from __future__ import annotations


PRESENCE_ZSET_KEY = "openedx:presence:active"
TOUCH_KEY_PREFIX = "openedx:presence:touch"
COUNT_CACHE_KEY = "openedx:presence:count"
ACTIVE_WINDOW_SECONDS = 10 * 60
TOUCH_THROTTLE_SECONDS = 60
COUNT_CACHE_SECONDS = 30

_EXACT_EXCLUDED_PATHS = {
    "/api/presence/v1/count",
    "/api/presence/v1/count/",
    "/health",
    "/health/",
    "/favicon.ico",
    "/csrf/api/v1/token",
    "/login_refresh",
}
_EXCLUDED_PREFIXES = (
    "/static/",
)


def should_track_path(path: str | None) -> bool:
    """Return True only for requests that represent meaningful backend activity."""
    normalized = str(path or "")
    if normalized in _EXACT_EXCLUDED_PATHS:
        return False
    return not normalized.startswith(_EXCLUDED_PREFIXES)


def touch_user(user_id, cache_backend, redis_client, *, now: int) -> bool:
    """Touch one user at most once per minute; return True when Redis was updated."""
    if not user_id:
        return False

    throttle_key = f"{TOUCH_KEY_PREFIX}:{user_id}"
    if not cache_backend.add(throttle_key, 1, timeout=TOUCH_THROTTLE_SECONDS):
        return False

    redis_client.zadd(PRESENCE_ZSET_KEY, {str(user_id): int(now)})
    return True


def get_active_count(cache_backend, redis_client, *, now: int) -> int:
    """Count unique users active in the last ten minutes, cached for 30 seconds."""
    cached = cache_backend.get(COUNT_CACHE_KEY)
    if cached is not None:
        return int(cached)

    cutoff = int(now) - ACTIVE_WINDOW_SECONDS
    count = int(redis_client.zcount(PRESENCE_ZSET_KEY, cutoff, "+inf"))
    cache_backend.set(COUNT_CACHE_KEY, count, timeout=COUNT_CACHE_SECONDS)
    return count
