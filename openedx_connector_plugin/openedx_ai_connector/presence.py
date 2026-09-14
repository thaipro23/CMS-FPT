"""Redis-backed online-presence integration for LMS and Studio."""
from __future__ import annotations

import logging
import time

from django.core.cache import cache
from django.http import JsonResponse
from django.views.decorators.http import require_GET
from django_redis import get_redis_connection

from .presence_core import get_active_count, should_track_path, touch_user


logger = logging.getLogger(__name__)


def _redis():
    return get_redis_connection("default")


class PresenceMiddleware:
    """Record authenticated backend activity without affecting request handling."""

    def __init__(self, get_response):
        self.get_response = get_response

    def __call__(self, request):
        self._record_activity(request)
        return self.get_response(request)

    @staticmethod
    def _record_activity(request):
        user = getattr(request, "user", None)
        if not user or not getattr(user, "is_authenticated", False):
            return
        if not should_track_path(getattr(request, "path", "")):
            return

        try:
            touch_user(
                getattr(user, "id", None),
                cache,
                _redis(),
                now=int(time.time()),
            )
        except Exception:  # Presence must never break LMS/CMS traffic.
            logger.warning("Unable to update Open edX presence", exc_info=True)


@require_GET
def presence_count(request):
    """Return the unique authenticated users active in the previous 10 minutes."""
    user = getattr(request, "user", None)
    if not user or not getattr(user, "is_authenticated", False):
        return JsonResponse({"detail": "Authentication required."}, status=401)

    try:
        active = get_active_count(cache, _redis(), now=int(time.time()))
    except Exception:
        logger.warning("Unable to read Open edX presence count", exc_info=True)
        return JsonResponse({"detail": "Presence temporarily unavailable."}, status=503)

    return JsonResponse({"active": active, "window_minutes": 10})
