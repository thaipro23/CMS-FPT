"""Fail-open middleware for FPT active-user presence."""

from __future__ import annotations

from .service import get_presence_service

COUNT_PATH = "/api/fpt-presence/v1/count"
EXCLUDED_PREFIXES = (
    "/static/",
    "/heartbeat",
    "/health",
    "/favicon.ico",
)
EXCLUDED_METHODS = {"HEAD", "OPTIONS"}


class FPTPresenceMiddleware:
    """Touch authenticated users after normal backend requests complete."""

    def __init__(self, get_response):
        self.get_response = get_response

    def __call__(self, request):
        response = self.get_response(request)
        try:
            self._track(request)
        except Exception:
            # Presence is observability/UI sugar and must never affect Open edX.
            pass
        return response

    @staticmethod
    def _track(request) -> None:
        if getattr(request, "method", "GET").upper() in EXCLUDED_METHODS:
            return

        path = getattr(request, "path_info", "") or ""
        if path == COUNT_PATH or path == f"{COUNT_PATH}/":
            return
        if any(path.startswith(prefix) for prefix in EXCLUDED_PREFIXES):
            return

        user = getattr(request, "user", None)
        if user is None or not getattr(user, "is_authenticated", False):
            return

        user_id = getattr(user, "pk", None)
        if user_id is None:
            return

        get_presence_service().touch_safely(user_id)
