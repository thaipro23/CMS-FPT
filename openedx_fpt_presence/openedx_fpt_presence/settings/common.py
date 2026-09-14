"""Self-contained runtime settings for FPT presence.

The Open edX image can be built on one Tutor host and deployed from another.
Presence therefore must not depend solely on a Tutor ENV_PATCH being present on
the runtime host. This hook is loaded from the installed Django plugin itself.
"""

from __future__ import annotations


MIDDLEWARE_PATH = "openedx_fpt_presence.middleware.FPTPresenceMiddleware"
DEFAULT_ACTIVE_WINDOW_SECONDS = 600
DEFAULT_TOUCH_INTERVAL_SECONDS = 60
DEFAULT_COUNT_CACHE_SECONDS = 30


def _as_redis_url(value):
    """Return a usable redis/rediss URL from a cache/broker setting value."""

    if isinstance(value, (list, tuple)):
        for item in value:
            url = _as_redis_url(item)
            if url:
                return url
        return None

    if not isinstance(value, str):
        return None

    value = value.strip()
    if value.startswith(("redis://", "rediss://")):
        return value
    return None


def discover_redis_url(settings):
    """Reuse the Redis Open edX already uses instead of inventing a hostname."""

    explicit = _as_redis_url(getattr(settings, "FPT_PRESENCE_REDIS_URL", None))
    if explicit:
        return explicit

    caches = getattr(settings, "CACHES", {}) or {}
    preferred_aliases = ("default", "general", "celery")
    checked = set()

    for alias in (*preferred_aliases, *caches.keys()):
        if alias in checked:
            continue
        checked.add(alias)
        cache_config = caches.get(alias) or {}
        url = _as_redis_url(cache_config.get("LOCATION"))
        if url:
            return url

    for attribute in ("CELERY_BROKER_URL", "BROKER_URL"):
        url = _as_redis_url(getattr(settings, attribute, None))
        if url:
            return url

    return None


def plugin_settings(settings):
    """Install middleware and defaults; Redis is resolved from final runtime settings."""

    middleware = list(getattr(settings, "MIDDLEWARE", []))
    if MIDDLEWARE_PATH not in middleware:
        middleware.append(MIDDLEWARE_PATH)
        settings.MIDDLEWARE = middleware

    # Do not resolve/cache a Redis URL here. Tutor production settings may still
    # be patched after common plugin settings run. PresenceService resolves the
    # final CACHES/Celery settings lazily when it first needs Redis.
    settings.FPT_PRESENCE_ACTIVE_WINDOW_SECONDS = getattr(
        settings,
        "FPT_PRESENCE_ACTIVE_WINDOW_SECONDS",
        DEFAULT_ACTIVE_WINDOW_SECONDS,
    )
    settings.FPT_PRESENCE_TOUCH_INTERVAL_SECONDS = getattr(
        settings,
        "FPT_PRESENCE_TOUCH_INTERVAL_SECONDS",
        DEFAULT_TOUCH_INTERVAL_SECONDS,
    )
    settings.FPT_PRESENCE_COUNT_CACHE_SECONDS = getattr(
        settings,
        "FPT_PRESENCE_COUNT_CACHE_SECONDS",
        DEFAULT_COUNT_CACHE_SECONDS,
    )
