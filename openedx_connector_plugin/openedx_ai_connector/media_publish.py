"""ACMS v18 media-aware Content Library problem publishing.

Keep this adapter isolated from the large Studio connector module so CMS-FPT can
accept ACMS canonical question media without replacing branch-specific Open edX
integration code. The public URL contract stays unchanged through ``views.py``.
"""

from __future__ import annotations

import base64
import hashlib
import hmac
import json
import re
from typing import Any

from django.http import HttpResponseBadRequest
from django.views.decorators.csrf import csrf_exempt

from .auth import _json_response, _require_connector_write
from . import studio


_ALLOWED_MIME = {'image/png', 'image/jpeg', 'image/webp'}
_MAX_ITEM_BYTES = 4 * 1024 * 1024
_MAX_TOTAL_BYTES = 16 * 1024 * 1024
_MAX_ITEMS = 4
_PATH_RE = re.compile(r'^[A-Za-z0-9_.-]+(?:/[A-Za-z0-9_.-]+)*$')
_MEDIA_TOKEN_RE = re.compile(r'__ACMS_MEDIA_[A-Za-z0-9_-]+__')


def _max_body_bytes() -> int:
    raw = studio._setting_or_env('AI_CONNECTOR_MAX_BODY_BYTES', 24 * 1024 * 1024)
    try:
        value = int(raw)
    except (TypeError, ValueError):
        value = 24 * 1024 * 1024
    return max(1024 * 1024, min(value, 64 * 1024 * 1024))


def _read_media_json_body(request) -> tuple[dict[str, Any] | None, Any | None]:
    try:
        raw_body = request.body or b''
    except Exception:
        return None, _json_response(
            {'ok': False, 'code': 'invalid_body', 'message': 'Không đọc được request body.'},
            status=400,
        )
    if len(raw_body) > _max_body_bytes():
        return None, _json_response(
            {
                'ok': False,
                'code': 'body_too_large',
                'message': f'Request body vượt giới hạn {_max_body_bytes()} bytes.',
            },
            status=413,
        )
    if not raw_body:
        return {}, None
    try:
        payload = json.loads(raw_body.decode('utf-8'))
    except (UnicodeDecodeError, json.JSONDecodeError):
        return None, _json_response(
            {'ok': False, 'code': 'invalid_json', 'message': 'JSON không hợp lệ.'},
            status=400,
        )
    if not isinstance(payload, dict):
        return None, _json_response(
            {'ok': False, 'code': 'invalid_json', 'message': 'Request body phải là JSON object.'},
            status=400,
        )
    return payload, None


def _decode_assets(raw_assets: object, olx: str) -> list[dict[str, Any]]:
    if raw_assets in (None, []):
        if _MEDIA_TOKEN_RE.search(str(olx or '')):
            raise ValueError('OLX có media placeholder nhưng request không gửi assets.')
        return []
    if not isinstance(raw_assets, list) or len(raw_assets) > _MAX_ITEMS:
        raise ValueError('assets phải là danh sách tối đa 4 ảnh.')

    decoded: list[dict[str, Any]] = []
    total = 0
    seen_placeholders: set[str] = set()
    seen_paths: set[str] = set()
    working_olx = str(olx or '')

    for item in raw_assets:
        if not isinstance(item, dict):
            raise ValueError('Media asset không hợp lệ.')
        placeholder = str(item.get('placeholder') or '').strip()
        file_path = str(item.get('file_path') or '').strip()
        mime = str(item.get('content_type') or '').strip().lower()
        encoded = str(item.get('content_b64') or '').strip()

        if not _MEDIA_TOKEN_RE.fullmatch(placeholder) or placeholder in seen_placeholders:
            raise ValueError('Media placeholder không hợp lệ hoặc bị trùng.')
        if placeholder not in working_olx:
            raise ValueError(f'OLX thiếu placeholder của media {file_path or placeholder}.')
        if (
            not file_path
            or file_path != file_path.strip().strip('/')
            or '//' in file_path
            or '..' in file_path.split('/')
            or not _PATH_RE.fullmatch(file_path)
            or file_path in seen_paths
        ):
            raise ValueError('Media file_path không hợp lệ hoặc bị trùng.')
        if mime not in _ALLOWED_MIME:
            raise ValueError('Media MIME không được hỗ trợ; chỉ nhận PNG/JPEG/WebP.')

        try:
            content = base64.b64decode(encoded, validate=True)
        except Exception as exc:
            raise ValueError('Media base64 không hợp lệ.') from exc
        if not content or len(content) > _MAX_ITEM_BYTES:
            raise ValueError('Media rỗng hoặc vượt 4 MB.')

        expected_sha = str(item.get('sha256') or '').strip().lower()
        actual_sha = hashlib.sha256(content).hexdigest()
        if expected_sha and not hmac.compare_digest(expected_sha, actual_sha):
            raise ValueError('Checksum media không khớp.')

        total += len(content)
        if total > _MAX_TOTAL_BYTES:
            raise ValueError('Tổng media vượt 16 MB.')

        seen_placeholders.add(placeholder)
        seen_paths.add(file_path)
        decoded.append(
            {
                'placeholder': placeholder,
                'file_path': file_path,
                'content_type': mime,
                'content': content,
                'sha256': actual_sha,
            }
        )

    unresolved = set(_MEDIA_TOKEN_RE.findall(working_olx)) - seen_placeholders
    if unresolved:
        raise ValueError('OLX còn media placeholder không có asset tương ứng.')
    return decoded


def _upload_assets(usage_key, olx: str, assets: list[dict[str, Any]], user) -> tuple[str, list[dict[str, Any]]]:
    if not assets:
        return olx, []
    try:
        from openedx.core.djangoapps.content_libraries.api.blocks import add_library_block_static_asset_file  # type: ignore
    except Exception as exc:
        raise RuntimeError('Open edX Content Libraries static asset API không khả dụng.') from exc

    final_olx = str(olx or '')
    uploaded: list[dict[str, Any]] = []
    for asset in assets:
        static_file = add_library_block_static_asset_file(
            usage_key,
            asset['file_path'],
            asset['content'],
            user=user,
        )
        url = str(getattr(static_file, 'url', '') or '').strip()
        if not url:
            raise RuntimeError('Open edX upload media không trả URL static asset.')
        final_olx = final_olx.replace(asset['placeholder'], url)
        uploaded.append(
            {
                'file_path': asset['file_path'],
                'url': url,
                'size': len(asset['content']),
                'sha256': asset['sha256'],
            }
        )

    if _MEDIA_TOKEN_RE.search(final_olx):
        raise ValueError('OLX còn media placeholder chưa được resolve.')
    return final_olx, uploaded


def _import_problem_olx_with_media(
    request,
    course_id: str,
    library_key: str,
    display_name: str,
    olx: str,
    *,
    metadata: dict | None = None,
    tag_names: list | None = None,
    assets: object = None,
) -> dict:
    metadata = metadata or {}
    tag_names = studio._tag_value_from_metadata(
        course_id,
        metadata,
        tag_names or metadata.get('tag_names') or metadata.get('tags') or [],
    )
    if not olx or '<problem' not in olx:
        raise ValueError('OLX không có thẻ <problem>, không import vào Library được.')

    decoded_assets = _decode_assets(assets, olx)

    try:
        from openedx.core.djangoapps.content_libraries.api.blocks import (  # type: ignore
            create_library_block,
            set_library_block_olx,
        )
        try:
            from openedx.core.djangoapps.content_libraries.api.exceptions import LibraryBlockAlreadyExists  # type: ignore
        except Exception:  # pragma: no cover - release compatibility
            LibraryBlockAlreadyExists = type('LibraryBlockAlreadyExists', (Exception,), {})
    except Exception as exc:
        raise RuntimeError(
            'Open edX Content Libraries V2 block API không khả dụng. Không thể tạo problem thật trong Library.'
        ) from exc

    normalized_key = studio._v2_library_key_string(course_id, library_key, display_name, metadata)
    locator = studio._library_locator(normalized_key)
    try:
        from openedx.core.djangoapps.content_libraries.api.libraries import get_library  # type: ignore
        library_meta = get_library(locator)
        actual_library_key = studio._canonicalize_existing_library_key(library_meta, normalized_key)
        if actual_library_key != normalized_key:
            normalized_key = actual_library_key
            locator = studio._library_locator(normalized_key)
    except Exception:
        # Library existence/permission is checked again by create/get block paths.
        pass

    user = studio._request_publish_user(request)
    user_id = getattr(user, 'id', None)
    question_id = str(metadata.get('question_id') or '')
    block_seed = question_id or display_name or hashlib.sha1(olx.encode('utf-8')).hexdigest()[:12]
    block_id = studio._safe_slug(f'ai-{block_seed}', max_len=64, fallback='ai-problem')
    usage_key_str = studio._usage_key_string(normalized_key, 'problem', block_id)
    usage_key = None
    created = False

    try:
        block_meta = create_library_block(
            locator,
            'problem',
            block_id,
            user_id=user_id,
            can_stand_alone=True,
        )
        usage_key = getattr(block_meta, 'usage_key', None) or studio._usage_locator(usage_key_str)
        created = True
    except Exception as exc:
        if (
            (LibraryBlockAlreadyExists and isinstance(exc, LibraryBlockAlreadyExists))
            or exc.__class__.__name__ in {'LibraryBlockAlreadyExists', 'IntegrityError'}
        ):
            usage_key = studio._usage_locator(usage_key_str)
        else:
            raise

    final_olx, uploaded_assets = _upload_assets(usage_key, olx, decoded_assets, user)
    component_version = set_library_block_olx(usage_key, final_olx)

    # Tags must be applied before publish; otherwise Library UI can show an
    # unnecessary unpublished-change state after content itself is published.
    tag_result = studio._apply_openedx_component_tags(
        usage_key,
        course_id,
        metadata,
        tag_names,
    )
    publish_library_result = studio._publish_library_drafts_without_post_tasks(locator, user_id)

    return {
        'ok': True,
        'status': 'problem_imported_and_published',
        'created': created,
        'course_id': course_id,
        'library_key': normalized_key,
        'openedx_library_id': normalized_key,
        'openedx_library_problem_id': studio._safe_str(usage_key),
        'openedx_block_id': studio._safe_str(usage_key),
        'component_version': studio._safe_str(component_version),
        'publish_component_result': None,
        'publish_library_result': studio._metadata_obj_to_dict(publish_library_result),
        'publish_warnings': [
            {
                'step': 'question_media_before_olx',
                'message': 'Media được upload vào Library component trước khi lưu OLX cuối.',
            },
            {
                'step': 'tag_before_publish',
                'message': 'Tag được gắn trước publish để tránh trạng thái Unpublished changes giả.',
            },
            {
                'step': 'post_publish_events',
                'message': 'Giữ strategy publish Ulmo hiện tại: publish core drafts, không phụ thuộc post-publish task lỗi PublishLog.',
            },
        ],
        'tag_result': tag_result,
        'uploaded_assets': uploaded_assets,
        'display_name': display_name,
        'source_node_id': metadata.get('source_node_id'),
        'chapter_node_id': metadata.get('chapter_node_id'),
        'difficulty': metadata.get('difficulty'),
        'tag_names': tag_names,
        'metadata': metadata,
        'implementation': 'content_libraries_v2_python_api_media_v18',
        'stub': False,
        'user_id': user_id,
    }


@csrf_exempt
def import_problem_to_library(request, library_key: str):
    """Import ACMS OLX + optional question images into a real V2 Library."""
    guard = _require_connector_write(request)
    if guard:
        return guard
    if request.method != 'POST':
        return HttpResponseBadRequest('POST required')

    payload, body_error = _read_media_json_body(request)
    if body_error:
        return body_error
    payload = payload or {}

    metadata = payload.get('metadata') or {}
    if not isinstance(metadata, dict):
        return _json_response(
            {'ok': False, 'code': 'invalid_metadata', 'message': 'metadata phải là JSON object.'},
            status=400,
        )
    course_id = payload.get('course_id') or metadata.get('course_id') or ''
    display_name = payload.get('display_name') or 'AI Learning Check'
    olx = payload.get('olx') or payload.get('problem_xml') or ''
    tag_names = payload.get('tag_names') or metadata.get('tag_names') or metadata.get('tags') or []
    assets = payload.get('assets') or []
    metadata = {**metadata, 'library_key': library_key, 'tag_names': tag_names}

    try:
        library = studio._ensure_content_library_v2(
            request=request,
            course_id=course_id,
            display_name=metadata.get('library_display_name') or metadata.get('chapter_title') or library_key,
            library_key=library_key,
            metadata=metadata,
        )
        result = _import_problem_olx_with_media(
            request=request,
            course_id=course_id,
            library_key=library['library_key'],
            display_name=display_name,
            olx=olx,
            metadata={**metadata, 'library_key': library['library_key']},
            tag_names=tag_names,
            assets=assets,
        )
        return _json_response(result)
    except studio.LibraryOrganizationMissingError as exc:
        return _json_response(
            {
                'ok': False,
                'status': 'error',
                'error_code': 'openedx_library_org_missing',
                'message': f'Organization {exc.org_short_name} chưa tồn tại trong Open edX.',
                'detail': {'organization': exc.org_short_name},
                'implementation': 'content_libraries_v2_python_api_media_v18',
                'stub': False,
            },
            status=409,
        )
    except ValueError as exc:
        return studio._connector_error(
            studio._message_from_exception(exc, 'OLX/media không hợp lệ'),
            status=400,
            code='invalid_olx_or_media',
            detail=studio._exception_detail(exc, 'import_problem_to_library.media_v18'),
        )
    except Exception as exc:
        return studio._connector_error(
            studio._message_from_exception(exc, 'Import Problem vào Library thất bại trong CMS'),
            status=502,
            code='openedx_library_import_failed',
            detail=studio._exception_detail(exc, 'import_problem_to_library.media_v18'),
        )
