"""Shared authentication, HMAC and request helpers for the Open edX AI connector."""

from __future__ import annotations

import hashlib
import hmac
import ipaddress
import json
import os
import re
import socket
import time
from datetime import datetime, timezone
from django.core.serializers.json import DjangoJSONEncoder
from typing import Any
from urllib.parse import urlparse
from urllib.request import HTTPRedirectHandler

from django.conf import settings
from django.core.cache import cache
from django.http import JsonResponse

MAX_AI_CONNECTOR_BODY_BYTES = int(os.environ.get('AI_CONNECTOR_MAX_BODY_BYTES') or 2 * 1024 * 1024)
MAX_STUDENT_INSIGHT_BATCH_SIZE = int(os.environ.get('AI_CONNECTOR_MAX_BATCH_SIZE') or os.environ.get('AI_STUDENT_INSIGHT_MAX_BATCH_SIZE') or 500)


class _NoRedirectHandler(HTTPRedirectHandler):
    def redirect_request(self, req, fp, code, msg, headers, newurl):  # pragma: no cover - stdlib callback
        return None


def _setting_or_env(name: str, default: Any = None) -> Any:
    """Read connector config from process env first, then Tutor/Django settings.

    v25.9.13.41: Tutor plugin mode stores AI_CONNECTOR_* values in generated
    CMS Django settings instead of requiring docker-compose.override.yml env
    injection. Env still wins so emergency overrides keep working.
    """
    value = os.environ.get(name)
    if value is not None:
        return value
    return getattr(settings, name, default)


def _env_bool(name: str, default: bool = False) -> bool:
    value = _setting_or_env(name)
    if value is None:
        return default
    if isinstance(value, bool):
        return value
    return str(value).strip().lower() in {'1', 'true', 'yes', 'on'}


def _connector_hmac_secret() -> str:
    return str(_setting_or_env('AI_CONNECTOR_HMAC_SECRET') or _setting_or_env('OPENEDX_CONNECTOR_HMAC_SECRET') or '')


def _request_path_with_query(request) -> str:
    path = request.path or ''
    query = request.META.get('QUERY_STRING') or ''
    return f'{path}?{query}' if query else path


def _check_and_store_hmac_nonce(kind: str, client: str, timestamp: str, nonce: str, ttl: int) -> bool:
    clean_client = re.sub(r'[^a-zA-Z0-9_.:-]+', '_', str(client or 'unknown'))[:80]
    clean_nonce = hashlib.sha256(str(nonce or '').encode('utf-8')).hexdigest()
    key = f'ai_hmac_nonce:{kind}:{clean_client}:{timestamp}:{clean_nonce}'
    try:
        return bool(cache.add(key, '1', timeout=max(60, min(int(ttl or 300), 3600))))
    except Exception:
        # Fail closed. If nonce storage is unavailable, replay protection is unavailable.
        return False


def _valid_connector_hmac(request) -> bool:
    secret = _connector_hmac_secret()
    if not secret:
        return False
    timestamp = request.META.get('HTTP_X_AI_CONNECTOR_TIMESTAMP') or ''
    supplied = request.META.get('HTTP_X_AI_CONNECTOR_SIGNATURE') or ''
    nonce = request.META.get('HTTP_X_AI_CONNECTOR_NONCE') or ''
    try:
        ts = int(timestamp)
    except Exception:
        return False
    skew = int(_setting_or_env('AI_CONNECTOR_HMAC_SKEW_SECONDS', '300') or '300')
    if abs(int(time.time()) - ts) > skew:
        return False
    body = request.body or b''
    body_hash = hashlib.sha256(body).hexdigest()
    path = _request_path_with_query(request)
    if nonce:
        message = f'{timestamp}.{request.method.upper()}.{path}.{body_hash}.{nonce}'
        replay_nonce = nonce
        expected = hmac.new(secret.encode('utf-8'), message.encode('utf-8'), hashlib.sha256).hexdigest()
        if not hmac.compare_digest(expected, supplied):
            return False
    else:
        # Backward compatibility for older AI Server builds that did not send a
        # connector nonce. Replay protection uses the signature in that case, so
        # identical requests inside the same second may be rejected; new clients
        # should always send X-AI-Connector-Nonce.
        message = f'{timestamp}.{request.method.upper()}.{path}.{body_hash}'
        replay_nonce = supplied
        expected = hmac.new(secret.encode('utf-8'), message.encode('utf-8'), hashlib.sha256).hexdigest()
        if not hmac.compare_digest(expected, supplied):
            return False
    return _check_and_store_hmac_nonce('connector', 'ai-connector', timestamp, replay_nonce, skew)


def _staff_or_superuser(request) -> bool:
    user = getattr(request, 'user', None)
    return bool(
        getattr(user, 'is_authenticated', False)
        and (getattr(user, 'is_staff', False) or getattr(user, 'is_superuser', False))
    )


def _auth_failed_response(reason: str = 'connector authentication required') -> JsonResponse:
    return _json_response({'ok': False, 'status': 'forbidden', 'code': 'connector_auth_required', 'message': reason}, status=403)


def _student_insight_hmac_secret() -> str:
    return str(
        _setting_or_env('AI_STUDENT_INSIGHT_SHARED_SECRET')
        or _setting_or_env('OPENEDX_STUDENT_INSIGHT_SHARED_SECRET')
        or _setting_or_env('AI_CONNECTOR_HMAC_SECRET')
        or _setting_or_env('OPENEDX_CONNECTOR_HMAC_SECRET')
        or ''
    )


def _valid_student_insight_hmac(request) -> bool:
    """Validate deprecated AI Server -> LMS/CMS X-AI-* HMAC with replay protection."""
    secret = _student_insight_hmac_secret()
    if not secret:
        return False
    client = request.META.get('HTTP_X_AI_CLIENT') or ''
    timestamp = request.META.get('HTTP_X_AI_TIMESTAMP') or ''
    nonce = request.META.get('HTTP_X_AI_NONCE') or ''
    supplied = request.META.get('HTTP_X_AI_SIGNATURE') or ''
    if not client or not timestamp or not nonce or not supplied:
        return False
    try:
        normalized = timestamp.replace('Z', '+00:00')
        ts = datetime.fromisoformat(normalized)
        if ts.tzinfo is None:
            ts = ts.replace(tzinfo=timezone.utc)
        skew = int(_setting_or_env('AI_STUDENT_INSIGHT_HMAC_SKEW_SECONDS', '300') or '300')
        if abs((datetime.now(timezone.utc) - ts).total_seconds()) > skew:
            return False
    except Exception:
        return False
    body = request.body or b''
    body_hash = hashlib.sha256(body).hexdigest()
    path = request.path or ''
    canonical = '\n'.join([request.method.upper(), path, timestamp, nonce, body_hash])
    expected = hmac.new(secret.encode('utf-8'), canonical.encode('utf-8'), hashlib.sha256).hexdigest()
    if not hmac.compare_digest(expected, supplied):
        return False
    return _check_and_store_hmac_nonce('student-insight', client, timestamp, nonce, skew)


def _require_openedx_connector_hmac(request):
    # Canonical v25.9.16.5.8 auth path: all runtime academic APIs are part of
    # openedx_connector_plugin and should use the existing connector HMAC secret.
    if _valid_connector_hmac(request):
        return None
    # Backward-compatible fallback for old AI Server builds that still send the
    # previous X-AI-* HMAC header family.
    if _valid_student_insight_hmac(request):
        return None
    student_headers_present = bool(
        request.META.get('HTTP_X_AI_CLIENT')
        and request.META.get('HTTP_X_AI_TIMESTAMP')
        and request.META.get('HTTP_X_AI_NONCE')
        and request.META.get('HTTP_X_AI_SIGNATURE')
    )
    connector_headers_present = bool(
        request.META.get('HTTP_X_AI_CONNECTOR_TIMESTAMP')
        and request.META.get('HTTP_X_AI_CONNECTOR_SIGNATURE')
    )
    connector_secret_configured = bool(_connector_hmac_secret())
    legacy_secret_configured = bool(_student_insight_hmac_secret())
    return _json_response(
        {
            'ok': False,
            'status': 'forbidden',
            'code': 'openedx_connector_hmac_required',
            'message': 'Open edX Connector endpoint yêu cầu HMAC server-to-server từ AI Server.',
            'diagnostics': {
                'connector_secret_configured': connector_secret_configured,
                'legacy_student_insight_secret_configured': legacy_secret_configured,
                'connector_headers_present': connector_headers_present,
                'connector_nonce_present': bool(request.META.get('HTTP_X_AI_CONNECTOR_NONCE')),
                'legacy_student_insight_headers_present': student_headers_present,
                'path': request.path,
            },
        },
        status=403,
    )


def _require_student_insight_hmac(request):
    # Backward-compatible function name kept for existing endpoint modules. The
    # implementation is now the unified Open edX Connector HMAC validator.
    return _require_openedx_connector_hmac(request)


def _require_connector_hmac(request, reason: str = 'Endpoint này chỉ nhận request server-to-server đã ký HMAC.'):
    if _valid_connector_hmac(request):
        return None
    return _auth_failed_response(reason)


def _require_connector_admin(request):
    # csrf_exempt connector endpoints expose privileged Open edX internals, so they
    # are HMAC-only. Browser staff/admin flows must use separate csrf_protected views.
    return _require_connector_hmac(request, 'Endpoint diagnostics yêu cầu HMAC server-to-server; staff cookie không được chấp nhận ở endpoint csrf_exempt.')


def _require_connector_write(request):
    # Publish/rollback/quiz-create endpoints are AI Server -> CMS server-to-server only.
    return _require_connector_hmac(request, 'Publish/rollback endpoint yêu cầu HMAC server-to-server; staff cookie không được chấp nhận ở endpoint csrf_exempt.')


def _host_from_request(request) -> str:
    return (request.get_host() or '').split(':', 1)[0].strip().lower()


def _allowed_download_hosts(request) -> set[str]:
    hosts = {_host_from_request(request)} if _host_from_request(request) else set()
    for name in ('AI_CONNECTOR_ALLOWED_DOWNLOAD_HOSTS', 'OPENEDX_ALLOWED_DOWNLOAD_HOSTS'):
        for host in (str(_setting_or_env(name) or '')).split(','):
            clean = host.strip().lower()
            if clean:
                hosts.add(clean)
    return hosts


def _host_resolves_to_private_address(hostname: str) -> bool:
    try:
        ipaddress.ip_address(hostname)
        addresses = [hostname]
    except ValueError:
        try:
            addresses = [item[4][0] for item in socket.getaddrinfo(hostname, None)]
        except socket.gaierror:
            return True
    for address in addresses:
        try:
            ip = ipaddress.ip_address(address)
        except ValueError:
            return True
        if ip.is_private or ip.is_loopback or ip.is_link_local or ip.is_multicast or ip.is_reserved or ip.is_unspecified:
            return True
    return False


def _validate_download_url(request, url: str) -> tuple[bool, str]:
    parsed = urlparse(url or '')
    if parsed.scheme not in {'http', 'https'} or not parsed.hostname:
        return False, 'invalid_scheme_or_host'
    host = parsed.hostname.lower()
    allowed = _allowed_download_hosts(request)
    if host in allowed:
        return True, 'allowed_host'
    if _host_resolves_to_private_address(host):
        return False, 'private_or_internal_host_blocked'
    return False, 'host_not_in_allowlist'


def _same_request_host(request, url: str) -> bool:
    try:
        return (urlparse(url).hostname or '').lower() == _host_from_request(request)
    except Exception:
        return False


class _ConnectorJSONEncoder(DjangoJSONEncoder):
    """JSON encoder for connector responses across Open edX releases.

    DjangoJSONEncoder already handles datetime/date/time/Decimal/UUID. The
    final string fallback protects connector responses from Open edX opaque keys
    and other model-ish values that otherwise raise "not JSON serializable"
    after the view has already completed the real work.
    """

    def default(self, value):  # noqa: D401 - Django encoder extension
        try:
            return super().default(value)
        except TypeError:
            return str(value)


def _json_response(data: dict, status: int = 200) -> JsonResponse:
    return JsonResponse(data, status=status, encoder=_ConnectorJSONEncoder, json_dumps_params={'ensure_ascii': False})


def _read_json_body(request) -> tuple[dict[str, Any] | None, JsonResponse | None]:
    try:
        raw_body = request.body or b''
        if len(raw_body) > MAX_AI_CONNECTOR_BODY_BYTES:
            return None, _json_response({'ok': False, 'code': 'body_too_large', 'message': 'Request body quá lớn'}, status=413)
        if not raw_body:
            return {}, None
        data = json.loads(raw_body.decode('utf-8'))
        if not isinstance(data, dict):
            return None, _json_response({'ok': False, 'message': 'Request body phải là JSON object'}, status=400)
        return data, None
    except Exception:
        return None, _json_response({'ok': False, 'code': 'invalid_json', 'message': 'JSON không hợp lệ'}, status=400)


def _batch_too_large_response(count: int) -> JsonResponse | None:
    if count <= MAX_STUDENT_INSIGHT_BATCH_SIZE:
        return None
    return _json_response(
        {
            'ok': False,
            'code': 'batch_too_large',
            'message': f'Tối đa {MAX_STUDENT_INSIGHT_BATCH_SIZE} user mỗi request',
            'total': count,
        },
        status=413,
    )


def _env_bool(name: str, default: bool = False) -> bool:
    value = _setting_or_env(name)
    if value is None or value == '':
        return default
    return str(value).strip().lower() in {'1', 'true', 'yes', 'on'}
