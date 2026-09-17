"""Authenticated streaming endpoint for grade reports stored in private MinIO."""

from __future__ import annotations

import logging
import mimetypes
from pathlib import PurePosixPath

from botocore.exceptions import BotoCoreError, ClientError
from django.conf import settings
from django.core import signing
from django.http import (
    FileResponse,
    HttpResponse,
    HttpResponseBadRequest,
    HttpResponseForbidden,
    HttpResponseNotFound,
)
from django.views.decorators.cache import cache_control
from django.views.decorators.http import require_GET
from opaque_keys.edx.keys import CourseKey

from common.djangoapps.student.models import CourseAccessRole
from lms.djangoapps.instructor import permissions
from openedx.core.lib.courses import get_course_by_id

from .security import course_hash, normalize_report_key, report_course_hash
from .storage import FPTReportProxyS3Storage, TOKEN_SALT

log = logging.getLogger(__name__)


def _candidate_course_ids(user):
    """Return course ids for which the user has an explicit Open edX course role."""
    return (
        CourseAccessRole.objects.filter(user=user)
        .values_list("course_id", flat=True)
        .distinct()
    )


def _user_can_download(user, key: str) -> bool:
    """Authorize the signed report key against the current user."""
    if not getattr(user, "is_authenticated", False):
        return False
    if getattr(user, "is_superuser", False) or getattr(user, "is_staff", False):
        return True

    try:
        expected_hash = report_course_hash(key)
    except ValueError:
        return False

    for raw_course_id in _candidate_course_ids(user):
        if course_hash(raw_course_id) != expected_hash:
            continue
        try:
            course_key = CourseKey.from_string(str(raw_course_id))
            course = get_course_by_id(course_key)
        except Exception:  # pragma: no cover - defensive across course-store releases
            log.exception("Could not resolve course while authorizing report download")
            return False
        return bool(user.has_perm(permissions.CAN_RESEARCH, course))

    return False


def _decode_token(token: str) -> str:
    ttl = int(getattr(settings, "FPT_REPORT_PROXY_TOKEN_TTL_SECONDS", 300))
    payload = signing.loads(token, salt=TOKEN_SALT, max_age=ttl)
    if not isinstance(payload, dict) or "key" not in payload:
        raise signing.BadSignature("report token payload is invalid")
    return normalize_report_key(payload["key"])


@require_GET
@cache_control(no_cache=True, no_store=True, must_revalidate=True)
def download_report(request):
    """Stream one authorized grade report from private MinIO through the LMS."""
    if not getattr(request.user, "is_authenticated", False):
        return HttpResponse(status=401)

    token = request.GET.get("token", "")
    if not token:
        return HttpResponseBadRequest("Missing report download token.")

    try:
        key = _decode_token(token)
        report_course_hash(key)
    except signing.SignatureExpired:
        return HttpResponse("Report download link has expired.", status=410)
    except (signing.BadSignature, ValueError, TypeError):
        return HttpResponseBadRequest("Invalid report download token.")

    if not _user_can_download(request.user, key):
        return HttpResponseForbidden("You do not have access to this report.")

    storage_kwargs = dict(getattr(settings, "GRADES_DOWNLOAD", {}).get("STORAGE_KWARGS", {}))
    storage = FPTReportProxyS3Storage(**storage_kwargs)

    try:
        if not storage.exists(key):
            return HttpResponseNotFound("Report not found.")
        stream = storage.open(key, "rb")
    except (ClientError, BotoCoreError, OSError):
        log.exception("Private report storage is unavailable for key=%s", key)
        return HttpResponse("Report storage is temporarily unavailable.", status=503)

    filename = PurePosixPath(key).name
    content_type = mimetypes.guess_type(filename)[0] or "application/octet-stream"
    response = FileResponse(stream, as_attachment=True, filename=filename, content_type=content_type)
    response["X-Content-Type-Options"] = "nosniff"
    return response
