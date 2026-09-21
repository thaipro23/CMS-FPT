"""Authenticated streaming endpoint for private Studio user-task artifacts."""

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
from user_tasks.models import UserTaskArtifact

from .security import normalize_user_task_artifact_key
from .storage import ARTIFACT_TOKEN_SALT, FPTUserTaskArtifactProxyS3Storage

log = logging.getLogger(__name__)


def _decode_token(token: str) -> str:
    ttl = int(getattr(settings, "FPT_ARTIFACT_PROXY_TOKEN_TTL_SECONDS", 300))
    payload = signing.loads(token, salt=ARTIFACT_TOKEN_SALT, max_age=ttl)
    if not isinstance(payload, dict) or "key" not in payload:
        raise signing.BadSignature("artifact token payload is invalid")
    return normalize_user_task_artifact_key(payload["key"])


@require_GET
@cache_control(no_cache=True, no_store=True, must_revalidate=True)
def download_artifact(request):
    """Stream one user-owned task artifact from private object storage."""
    if not getattr(request.user, "is_authenticated", False):
        return HttpResponse(status=401)

    token = request.GET.get("token", "")
    if not token:
        return HttpResponseBadRequest("Missing artifact download token.")

    try:
        key = _decode_token(token)
    except signing.SignatureExpired:
        return HttpResponse("Artifact download link has expired.", status=410)
    except (signing.BadSignature, ValueError, TypeError):
        return HttpResponseBadRequest("Invalid artifact download token.")

    is_owner = UserTaskArtifact.objects.filter(
        file=key,
        status__user_id=request.user.pk,
    ).exists()
    if not is_owner:
        return HttpResponseForbidden("You do not have access to this artifact.")

    storage_kwargs = dict(getattr(settings, "FPT_ARTIFACT_STORAGE_KWARGS", {}))
    storage = FPTUserTaskArtifactProxyS3Storage(**storage_kwargs)

    try:
        if not storage.exists(key):
            return HttpResponseNotFound("Artifact not found.")
        stream = storage.open(key, "rb")
    except (ClientError, BotoCoreError, OSError):
        log.exception("Private artifact storage is unavailable for key=%s", key)
        return HttpResponse("Artifact storage is temporarily unavailable.", status=503)

    filename = PurePosixPath(key).name
    content_type = mimetypes.guess_type(filename)[0] or "application/octet-stream"
    response = FileResponse(stream, as_attachment=True, filename=filename, content_type=content_type)
    response["X-Content-Type-Options"] = "nosniff"
    return response
