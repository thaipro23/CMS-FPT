"""Redis-backed active-user presence service."""

from __future__ import annotations

import logging
import threading
import time
from functools import lru_cache
from typing import Callable

import redis
from django.conf import settings

from openedx_fpt_presence.settings.common import discover_redis_url

LOGGER = logging.getLogger(__name__)

ACTIVE_KEY = "fpt:presence:active"
COUNT_CACHE_KEY = "fpt:presence:count-cache"
DEFAULT_ACTIVE_WINDOW_SECONDS = 600
DEFAULT_TOUCH_INTERVAL_SECONDS = 60
DEFAULT_COUNT_CACHE_SECONDS = 30
MAX_LOCAL_TOUCHES = 50_000


class PresenceService:
    """Track distinct users with a Redis sorted set and cheap local throttling."""

    def __init__(
        self,
        redis_client,
        *,
        clock: Callable[[], float] = time.time,
        active_window_seconds: int = DEFAULT_ACTIVE_WINDOW_SECONDS,
        touch_interval_seconds: int = DEFAULT_TOUCH_INTERVAL_SECONDS,
        count_cache_seconds: int = DEFAULT_COUNT_CACHE_SECONDS,
    ):
        self.redis = redis_client
        self.clock = clock
        self.active_window_seconds = int(active_window_seconds)
        self.touch_interval_seconds = int(touch_interval_seconds)
        self.count_cache_seconds = int(count_cache_seconds)
        self._recent_touches: dict[str, float] = {}
        self._touch_lock = threading.Lock()

    def _should_touch(self, member: str, now: float) -> bool:
        with self._touch_lock:
            last_touch = self._recent_touches.get(member)
            if (
                last_touch is not None
                and self.touch_interval_seconds > 0
                and now - last_touch < self.touch_interval_seconds
            ):
                return False

            self._recent_touches[member] = now
            if len(self._recent_touches) > MAX_LOCAL_TOUCHES:
                cutoff = now - max(self.touch_interval_seconds, 1)
                self._recent_touches = {
                    user_id: touched_at
                    for user_id, touched_at in self._recent_touches.items()
                    if touched_at >= cutoff
                }
            return True

    def touch(self, user_id: int | str) -> bool:
        """Record user activity unless this worker touched the user recently."""

        member = str(user_id)
        now = float(self.clock())
        if not self._should_touch(member, now):
            return False

        self.redis.zadd(ACTIVE_KEY, {member: now})
        return True

    def touch_safely(self, user_id: int | str) -> bool:
        """Fail open when Redis is unavailable."""

        try:
            return self.touch(user_id)
        except Exception:  # Redis/network failure must never break an Open edX request.
            LOGGER.debug("FPT presence touch failed", exc_info=True)
            return False

    def count(self) -> int:
        """Return distinct users active inside the configured time window."""

        if self.count_cache_seconds > 0:
            cached = self.redis.get(COUNT_CACHE_KEY)
            if cached is not None:
                return int(cached)

        now = float(self.clock())
        cutoff = now - self.active_window_seconds

        # Keep users exactly on the cutoff boundary; only older scores are stale.
        self.redis.zremrangebyscore(ACTIVE_KEY, "-inf", f"({cutoff}")
        online = int(self.redis.zcount(ACTIVE_KEY, cutoff, "+inf"))

        if self.count_cache_seconds > 0:
            self.redis.set(COUNT_CACHE_KEY, online, ex=self.count_cache_seconds)
        return online

    def count_safely(self) -> int | None:
        """Return None instead of surfacing a Redis outage to callers."""

        try:
            return self.count()
        except Exception:
            # Count requests may be frequent. Keep outages quiet and hide the badge.
            LOGGER.debug("FPT presence count unavailable", exc_info=True)
            return None


def _build_redis_client():
    """Build a client from explicit FPT config or Open edX's existing Redis URL."""

    config = getattr(settings, "FPT_PRESENCE_REDIS", None) or {}
    if config:
        return redis.Redis(
            host=config.get("HOST"),
            port=int(config.get("PORT", 6379)),
            db=int(config.get("DB", 0)),
            username=config.get("USERNAME") or None,
            password=config.get("PASSWORD") or None,
            socket_connect_timeout=float(config.get("SOCKET_CONNECT_TIMEOUT", 0.3)),
            socket_timeout=float(config.get("SOCKET_TIMEOUT", 0.3)),
            health_check_interval=30,
            decode_responses=True,
        )

    redis_url = getattr(settings, "FPT_PRESENCE_REDIS_URL", None) or discover_redis_url(settings)
    if not redis_url:
        raise RuntimeError(
            "FPT presence could not discover a Redis URL from Open edX settings"
        )

    return redis.Redis.from_url(
        redis_url,
        socket_connect_timeout=0.3,
        socket_timeout=0.3,
        health_check_interval=30,
        decode_responses=True,
    )


@lru_cache(maxsize=1)
def get_presence_service() -> PresenceService:
    """Build one Redis client/service per LMS or CMS worker process."""

    return PresenceService(
        _build_redis_client(),
        active_window_seconds=getattr(
            settings,
            "FPT_PRESENCE_ACTIVE_WINDOW_SECONDS",
            DEFAULT_ACTIVE_WINDOW_SECONDS,
        ),
        touch_interval_seconds=getattr(
            settings,
            "FPT_PRESENCE_TOUCH_INTERVAL_SECONDS",
            DEFAULT_TOUCH_INTERVAL_SECONDS,
        ),
        count_cache_seconds=getattr(
            settings,
            "FPT_PRESENCE_COUNT_CACHE_SECONDS",
            DEFAULT_COUNT_CACHE_SECONDS,
        ),
    )
