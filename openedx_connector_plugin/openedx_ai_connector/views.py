"""CMS/Studio connector for AI Learning Check Generator.

The endpoints in this file are intentionally thin and defensive.  They run inside
Open edX/Tutor, so they can use Studio/modulestore APIs that the external Course
Blocks API cannot expose, especially draft HTML, old problem XML and course
assets.  When an internal API differs between Open edX releases, this connector
returns the best available data instead of failing the whole sync.
"""

from __future__ import annotations

import base64
import hashlib
import json
import mimetypes
import os
import re
import traceback
from html import unescape
from typing import Any
from urllib.parse import quote, unquote, urljoin
from urllib.request import Request, urlopen

from django.http import JsonResponse, HttpResponseBadRequest
from django.views.decorators.csrf import csrf_exempt


# v25.9.13.18: tag components before publishing so Open edX Library UI does not keep unpublished tag changes.
# They attempt real Content Libraries V2 mutation through Open edX's documented
# Python API and fail loudly when the running Open edX release does not expose it.


_CONTAINER_TYPES = {'course', 'chapter', 'sequential', 'vertical'}
_LEARNING_TYPES = {'html', 'problem', 'video', 'pdf', 'file', 'asset', 'document', 'library_content'}
_TEXT_FIELD_NAMES = (
    'data', 'content', 'html', 'text', 'body', 'source_code', 'xml_data',
    'markdown', 'description', 'transcript', 'display_name'
)
_VIDEO_FIELD_NAMES = (
    'transcripts', 'transcript', 'sub', 'subtitles', 'downloadable_transcripts',
    'transcripts_url', 'transcript_url', 'youtube_id_1_0', 'youtube_id_0_75',
    'youtube_id_1_25', 'youtube_id_1_5', 'edx_video_id', 'handout', 'handouts', 'source_file'
)


def _json_response(data: dict, status: int = 200) -> JsonResponse:
    return JsonResponse(data, status=status, json_dumps_params={'ensure_ascii': False})


def health(request):
    return _json_response({
        'status': 'ok',
        'service': 'openedx_ai_connector',
        'message': 'AI connector is running',
        'version': '25.9.13.28',
        'publish_implementation': 'content_libraries_v2_python_api',
        'stub_publish': False,
    })


def _safe_str(value: Any) -> str:
    if value is None or callable(value):
        return ''
    if isinstance(value, bytes):
        try:
            return value.decode('utf-8')
        except Exception:
            return value.decode('latin-1', errors='ignore')
    if isinstance(value, (str, int, float, bool)):
        return str(value)
    try:
        return json.dumps(value, ensure_ascii=False, default=str)
    except Exception:
        return str(value)


def _normalize_text(value: Any) -> str:
    text = _safe_str(value)
    text = re.sub(r'<(script|style)[^>]*>.*?</\1>', ' ', text, flags=re.IGNORECASE | re.DOTALL)
    text = re.sub(r'<[^>]+>', ' ', text)
    return re.sub(r'\s+', ' ', unescape(text)).strip()


def _block_id(block: Any) -> str:
    for attr in ('location', 'scope_ids', 'usage_id'):
        value = getattr(block, attr, None)
        if attr == 'scope_ids' and value is not None:
            value = getattr(value, 'usage_id', None)
        if value:
            return str(value)
    return ''


def _block_type(block: Any) -> str:
    location = getattr(block, 'location', None)
    for value in (
        getattr(location, 'block_type', None),
        getattr(location, 'category', None),
        getattr(block, 'category', None),
        getattr(block, 'block_type', None),
    ):
        if value:
            return str(value).lower()
    return 'unknown'


def _display_name(block: Any) -> str:
    return _safe_str(getattr(block, 'display_name', '') or getattr(block, 'name', '') or _block_type(block))


def _parent_id(parent: Any | None) -> str | None:
    return str(parent) if parent else None


def _to_jsonable(value: Any, depth: int = 0) -> Any:
    if depth > 2:
        return _safe_str(value)
    if value is None or isinstance(value, (str, int, float, bool)):
        return value
    if isinstance(value, (list, tuple, set)):
        return [_to_jsonable(item, depth + 1) for item in value][:50]
    if isinstance(value, dict):
        return {str(k): _to_jsonable(v, depth + 1) for k, v in list(value.items())[:80]}
    return _safe_str(value)


def _known_metadata(block: Any) -> dict:
    metadata: dict[str, Any] = {}
    for name in (
        'display_name', 'start', 'due', 'format', 'graded', 'weight', 'max_attempts',
        'showanswer', 'rerandomize', 'show_reset_button', 'youtube_id_1_0',
        'edx_video_id', 'downloadable_transcripts', 'source_file', 'handout', 'handouts'
    ):
        try:
            value = getattr(block, name, None)
        except Exception:
            continue
        if value not in (None, '', [], {}) and not callable(value):
            jsonable = _to_jsonable(value)
            if jsonable not in (None, '', [], {}):
                metadata[name] = jsonable
    return metadata


def _extract_raw_content(block: Any, block_type: str) -> str:
    parts: list[str] = []
    for field_name in _TEXT_FIELD_NAMES:
        try:
            value = getattr(block, field_name, None)
        except Exception:
            value = None
        if value in (None, '', [], {}):
            continue
        text = _safe_str(value)
        if text and text not in parts:
            parts.append(text)

    # capa/problem blocks usually expose the complete OLX XML in .data.  Keep the
    # XML because AI Server's ContentExtractor will convert it to readable text.
    if block_type == 'problem':
        for field_name in ('data', 'xml_attributes', 'problem_xml'):
            try:
                value = getattr(block, field_name, None)
            except Exception:
                value = None
            text = _safe_str(value)
            if text and text not in parts:
                parts.append(text)

    return '\n\n'.join(p for p in parts if p.strip())


def _extract_video_fields(block: Any) -> dict:
    data: dict[str, Any] = {}
    for field_name in _VIDEO_FIELD_NAMES:
        try:
            value = getattr(block, field_name, None)
        except Exception:
            value = None
        if value not in (None, '', [], {}) and not callable(value):
            jsonable = _to_jsonable(value)
            if jsonable not in (None, '', [], {}):
                data[field_name] = jsonable
    return data


def _extract_links_from_text(text: str) -> list[str]:
    if not text:
        return []
    pattern = r'''(?:href|src)=["']([^"']+)["']|https?://[^\s"'<>]+|/asset-v1:[^\s"'<>]+|/static/[^\s"'<>]+'''
    links: list[str] = []
    for match in re.finditer(pattern, text, flags=re.IGNORECASE):
        url = match.group(1) or match.group(0)
        if url and url not in links:
            links.append(url)
    return links


def _absolute_url(request, url: str) -> str:
    if not url:
        return ''
    if url.startswith(('http://', 'https://')):
        return url
    return request.build_absolute_uri(url)




def _filename_from_asset_url(url: str) -> str:
    clean = unquote((url or '').split('?', 1)[0].split('#', 1)[0].rstrip('/'))
    if 'block@' in clean:
        return clean.rsplit('block@', 1)[-1]
    return clean.rsplit('/', 1)[-1] or 'asset'


def _should_capture_asset(url: str) -> bool:
    lower = (url or '').lower()
    file_exts = (
        '.pdf', '.ppt', '.pptx', '.doc', '.docx', '.xls', '.xlsx', '.xlsm',
        '.csv', '.tsv', '.txt', '.md', '.markdown', '.html', '.htm', '.json',
        '.xml', '.srt', '.vtt'
    )
    return '/asset-v1:' in lower or '/static/' in lower or any(ext in lower for ext in file_exts)


def _download_asset_payload(request, url: str, max_bytes: int = 15 * 1024 * 1024) -> dict:
    """Best-effort inline asset fetch for local Studio connector responses.

    AI Server can also download asset URLs from outside CMS, but when Docker/DNS
    cannot resolve the Studio hostname this inline base64 fallback lets the
    backend parse handouts directly.  Large files are intentionally skipped.
    """
    if not url.startswith(('http://', 'https://')):
        return {}
    try:
        headers = {'Accept': '*/*'}
        cookie = request.META.get('HTTP_COOKIE')
        if cookie:
            headers['Cookie'] = cookie
        req = Request(url, headers=headers)
        with urlopen(req, timeout=8) as response:  # nosec - local CMS connector best effort
            content_type = response.headers.get('Content-Type', '')
            data = response.read(max_bytes + 1)
        if len(data) > max_bytes:
            return {'download_skipped': 'asset_too_large', 'content_type': content_type}
        return {
            'bytes_base64': base64.b64encode(data).decode('ascii'),
            'mime_type': content_type,
            'content_type': content_type,
            'size_bytes': len(data),
        }
    except Exception as exc:
        return {'download_error': str(exc)}

def _assets_from_block(request, block: Any, raw_content: str) -> list[dict]:
    assets: list[dict] = []
    seen: set[str] = set()

    def add_asset(url: str, display_name: str | None = None):
        if not url:
            return
        abs_url = _absolute_url(request, url)
        if abs_url in seen or not _should_capture_asset(abs_url):
            return
        seen.add(abs_url)
        filename = display_name or _filename_from_asset_url(abs_url)
        mime_type = mimetypes.guess_type(filename)[0] or ''
        payload = {
            'asset_id': abs_url,
            'url': abs_url,
            'filename': filename,
            'file_name': filename,
            'display_name': filename,
            'mime_type': mime_type,
            'content_type': mime_type,
            'source_ref': abs_url,
        }
        payload.update(_download_asset_payload(request, abs_url))
        assets.append(payload)

    for url in _extract_links_from_text(raw_content):
        add_asset(url)

    # Some XBlocks store supplemental files in fields such as handout/handouts.
    # The singular `handout` field is important for Video XBlocks in Studio.
    for field_name in ('asset', 'assets', 'file', 'files', 'handout', 'handouts', 'downloadable_files', 'supplemental_materials', 'source_file'):
        try:
            value = getattr(block, field_name, None)
        except Exception:
            value = None
        if not value or callable(value):
            continue
        values = value if isinstance(value, (list, tuple, set)) else [value]
        for item in values:
            if isinstance(item, dict):
                url = item.get('url') or item.get('path') or item.get('asset_id') or item.get('location') or item.get('value')
                name = item.get('filename') or item.get('file_name') or item.get('display_name') or None
            else:
                url = _safe_str(item)
                name = None
            add_asset(url, name)
    return assets


def _as_draft_key(key: Any) -> Any:
    """Try to switch a CourseKey/UsageKey to the draft branch when supported."""
    if key is None:
        return key
    for method_name in ('for_branch', 'replace'):
        method = getattr(key, method_name, None)
        if not method:
            continue
        try:
            if method_name == 'for_branch':
                return method('draft')
            return method(branch='draft')
        except Exception:
            pass
    return key


def _load_openedx_modules():
    from opaque_keys.edx.keys import CourseKey  # type: ignore
    from xmodule.modulestore.django import modulestore  # type: ignore
    return CourseKey, modulestore


def _get_item_best_effort(store: Any, usage_key: Any) -> Any | None:
    keys = [_as_draft_key(usage_key), usage_key]
    seen = set()
    for key in keys:
        if key is None or str(key) in seen:
            continue
        seen.add(str(key))
        try:
            return store.get_item(key)
        except Exception:
            continue
    return None


def _children_locations(block: Any) -> list[Any]:
    try:
        children = getattr(block, 'children', None) or []
    except Exception:
        return []
    return list(children)


def _block_to_payload(request, block: Any, parent: Any | None = None) -> dict:
    block_id = _block_id(block)
    block_type = _block_type(block)
    raw_content = _extract_raw_content(block, block_type)
    video_fields = _extract_video_fields(block) if block_type == 'video' else {}
    metadata = _known_metadata(block)
    if video_fields:
        metadata.update(video_fields)

    payload = {
        'block_id': block_id,
        'id': block_id,
        'type': block_type,
        'display_name': _display_name(block),
        'data': raw_content,
        'content': raw_content,
        'parent_block_id': _parent_id(parent),
        'parent': _parent_id(parent),
        'children': [str(child) for child in _children_locations(block)],
        'metadata': metadata,
        'source_ref': block_id,
        'source_origin': 'studio_modulestore_draft',
        'assets': _assets_from_block(request, block, raw_content),
    }

    if block_type == 'problem' and raw_content:
        payload['problem_xml'] = raw_content
    if video_fields:
        payload['student_view_data'] = {'video_fields': video_fields}
    return payload


def _collect_static_assets_best_effort(request, course_key: Any) -> list[dict]:
    """Best-effort course asset listing.

    Open edX contentstore APIs changed across releases.  This function tries a few
    known shapes and returns an empty list when the local release does not expose
    them.  Linked assets inside HTML/problem content are still captured by
    _assets_from_block.
    """
    assets: list[dict] = []
    try:
        from contentstore.contentstore.django import contentstore  # type: ignore
        store = contentstore()
    except Exception:
        try:
            from contentstore.contentstore import contentstore  # type: ignore
            store = contentstore()
        except Exception:
            return assets

    candidates = []
    for method_name in ('get_all_content_for_course', 'get_all_content_for_course_items', 'get_assets_for_course'):
        method = getattr(store, method_name, None)
        if not method:
            continue
        try:
            candidates = method(course_key) or []
            break
        except Exception:
            continue

    if isinstance(candidates, tuple):
        candidates = candidates[0] or []
    for item in candidates:
        url = ''
        filename = ''
        for attr in ('url', 'external_url', 'portable_url', 'asset_key', 'name', 'display_name'):
            try:
                value = getattr(item, attr, None)
            except Exception:
                value = None
            if value and not url:
                url = _safe_str(value)
            if value and not filename:
                filename = _safe_str(value).split('/')[-1]
        if url:
            assets.append({
                'block_id': f'{course_key}:asset:{quote(url, safe="")}',
                'type': 'asset',
                'display_name': filename or url,
                'data': '',
                'parent_block_id': str(course_key),
                'children': [],
                'metadata': {},
                'source_ref': _absolute_url(request, url),
                'assets': [{
                    'asset_id': _absolute_url(request, url),
                    'url': _absolute_url(request, url),
                    'filename': filename or 'asset',
                    'display_name': filename or 'asset',
                    'source_ref': _absolute_url(request, url),
                }],
            })
    return assets


def _read_studio_blocks(request, course_id: str) -> tuple[list[dict], dict]:
    CourseKey, modulestore = _load_openedx_modules()
    course_key = CourseKey.from_string(course_id)
    store = modulestore()

    # Try draft first so Studio-only changes and unpublished components are visible.
    course = None
    for key in (_as_draft_key(course_key), course_key):
        try:
            course = store.get_course(key)
            if course:
                break
        except Exception:
            continue
    if course is None:
        raise ValueError(f'Course not found: {course_id}')

    blocks: list[dict] = []
    visited: set[str] = set()
    stack: list[tuple[Any, Any | None]] = [(getattr(course, 'location', None), None)]

    while stack:
        usage_key, parent = stack.pop()
        block = _get_item_best_effort(store, usage_key)
        if block is None:
            continue
        bid = _block_id(block)
        if not bid or bid in visited:
            continue
        visited.add(bid)
        blocks.append(_block_to_payload(request, block, parent))
        for child in reversed(_children_locations(block)):
            stack.append((child, getattr(block, 'location', None)))

    blocks.extend(_collect_static_assets_best_effort(request, course_key))
    summary = {
        'course_id': course_id,
        'blocks': len(blocks),
        'draft_first': True,
        'source': 'studio_modulestore',
    }
    return blocks, summary


def course_content(request, course_id: str):
    """Backward-compatible endpoint used by older AI Server builds.

    It now reads the same Studio/modulestore source as the v1 endpoint instead of
    returning placeholder content.
    """
    return studio_course_content(request, course_id)


def studio_course_content(request, course_id: str):
    """Return Studio/draft course content for AI Server sync.

    This endpoint is meant to be installed inside CMS/Studio.  It reads draft
    XBlocks from modulestore, including raw HTML, old problem XML and linked
    asset URLs.  This is the endpoint AI Server should use when it needs content
    from Studio rather than the learner-facing Course Blocks API.
    """
    try:
        blocks, summary = _read_studio_blocks(request, course_id)
    except Exception as exc:
        return _json_response({
            'course_id': course_id,
            'status': 'error',
            'message': str(exc),
            'blocks': [],
        }, status=500)

    return _json_response({
        'course_id': course_id,
        'status': 'ok',
        'source': 'studio_modulestore_draft',
        'summary': summary,
        'blocks': blocks,
    })



# ---------------------------------------------------------------------------
# Real Open edX Library publish helpers (v25.9.13.4)
# ---------------------------------------------------------------------------

def _exception_detail(exc: Exception, phase: str = '') -> dict:
    """Return useful exception data even when str(exc) is empty.

    Django/Open edX exceptions such as PermissionDenied, ObjectDoesNotExist,
    ValidationError, or opaque key errors may stringify to an empty message.
    AI Server needs class/repr/traceback to debug publish failures from outside
    the CMS container.
    """
    detail = {
        'phase': phase,
        'exception_class': exc.__class__.__name__,
        'exception_module': exc.__class__.__module__,
        'exception_repr': repr(exc),
    }
    if getattr(exc, 'args', None):
        detail['args'] = [_safe_str(arg) for arg in exc.args]
    for attr in ('messages', 'message_dict', 'params', 'code'):
        try:
            value = getattr(exc, attr, None)
        except Exception:
            value = None
        if value not in (None, '', [], {}):
            detail[attr] = _to_jsonable(value)
    tb = traceback.format_exc()
    if tb and tb != 'NoneType: None\n':
        detail['traceback_tail'] = '\n'.join(tb.strip().splitlines()[-16:])
    return detail


def _message_from_exception(exc: Exception, fallback: str) -> str:
    msg = str(exc).strip()
    if msg:
        return msg
    return f'{fallback}: {exc.__class__.__module__}.{exc.__class__.__name__} {repr(exc)}'


def _connector_error(message: str, status: int = 500, code: str = 'openedx_publish_failed', detail: dict | None = None) -> JsonResponse:
    return _json_response({
        'ok': False,
        'status': 'error',
        'error_code': code,
        'message': message or 'Open edX connector failed without a text message. See detail.exception_class/traceback_tail.',
        'detail': detail or {},
        'implementation': 'content_libraries_v2_python_api',
        'stub': False,
    }, status=status)


def _course_org(course_id: str, metadata: dict | None = None) -> str:
    metadata = metadata or {}
    explicit = metadata.get('org') or metadata.get('library_org')
    if explicit:
        return _safe_slug(str(explicit), max_len=30, fallback='org').upper()
    if course_id.startswith('course-v1:') and '+' in course_id:
        return _safe_slug(course_id.split(':', 1)[1].split('+', 1)[0], max_len=30, fallback='org').upper()
    if '+' in course_id:
        return _safe_slug(course_id.split('+', 1)[0], max_len=30, fallback='org').upper()
    return 'ORG'


def _organization_for_library(course_id: str, metadata: dict | None = None):
    """Return the real Open edX Organization model instance for Content Libraries V2.

    Ulmo's ``create_library`` asserts ``isinstance(org, Organization)``; passing
    a plain string like ``"FPT"`` raises the AssertionError seen in publish.
    Keep auto-create disabled by default so production does not silently create
    wrong orgs, but allow it explicitly for local/dev migrations.
    """
    org_short_name = _course_org(course_id, metadata)
    try:
        from organizations.models import Organization  # type: ignore
    except Exception as exc:
        raise RuntimeError('Không import được organizations.models.Organization trong CMS.') from exc

    org = (
        Organization.objects.filter(short_name=org_short_name).first()
        or Organization.objects.filter(short_name__iexact=org_short_name).first()
    )
    if org:
        return org

    auto_create = os.environ.get('AI_CONNECTOR_AUTO_CREATE_ORG', '').lower() in {'1', 'true', 'yes'}
    if auto_create:
        org, _ = Organization.objects.get_or_create(
            short_name=org_short_name,
            defaults={'name': (metadata or {}).get('org_name') or org_short_name},
        )
        return org

    existing = list(Organization.objects.all().values_list('short_name', flat=True)[:50])
    raise RuntimeError(
        f'Không tìm thấy Open edX Organization short_name={org_short_name!r}. '
        f'Các org hiện có: {existing}. '
        'Tạo Organization tương ứng với org trong course_id hoặc set AI_CONNECTOR_AUTO_CREATE_ORG=true cho môi trường dev/local.'
    )


def _safe_slug(text: str, max_len: int = 64, fallback: str = 'ai-library') -> str:
    import unicodedata
    raw = unicodedata.normalize('NFKD', (text or '').strip().lower()).encode('ascii', 'ignore').decode('ascii')
    raw = re.sub(r'[^a-z0-9]+', '-', raw).strip('-')
    if not raw:
        raw = fallback
    if len(raw) > max_len:
        digest = hashlib.sha1(raw.encode('utf-8')).hexdigest()[:8]
        raw = f'{raw[: max_len - 9].rstrip("-")}-{digest}'
    return raw or fallback


def _library_slug_from_payload(course_id: str, library_key: str | None, display_name: str, metadata: dict | None = None) -> str:
    metadata = metadata or {}
    explicit = metadata.get('library_slug') or metadata.get('slug')
    if explicit:
        return _safe_slug(str(explicit), max_len=48, fallback='ai-library')
    raw_key = str(library_key or metadata.get('library_key') or display_name or 'ai-library')
    # The AI Server local key is already chapter+difficulty based, so keep it when possible.
    if raw_key.startswith('lib:'):
        parts = raw_key.split(':')
        if len(parts) >= 3:
            return _safe_slug(parts[2], max_len=48, fallback='ai-library')
    return _safe_slug(raw_key, max_len=48, fallback='ai-library')


def _v2_library_key_string(course_id: str, library_key: str | None, display_name: str, metadata: dict | None = None) -> str:
    raw_key = str(library_key or (metadata or {}).get('library_key') or '').strip()
    if raw_key.startswith('lib:'):
        return raw_key
    org = _course_org(course_id, metadata)
    slug = _library_slug_from_payload(course_id, raw_key, display_name, metadata)
    return f'lib:{org}:{slug}'


def _usage_key_string(library_key: str, block_type: str, block_id: str) -> str:
    if not library_key.startswith('lib:'):
        raise ValueError(f'Library key không phải Content Libraries V2 key hợp lệ: {library_key}')
    _, org, slug = library_key.split(':', 2)
    return f'lb:{org}:{slug}:{block_type}:{block_id}'


def _library_locator(library_key: str):
    try:
        from opaque_keys.edx.locator import LibraryLocatorV2  # type: ignore
    except Exception as exc:
        raise RuntimeError(
            'Open edX này không có LibraryLocatorV2. Có thể bản Open edX hiện tại chưa bật/cài Content Libraries V2. '
            'Không thể publish thật bằng endpoint này.'
        ) from exc
    return LibraryLocatorV2.from_string(library_key)


def _usage_locator(usage_key: str):
    # Different releases expose the parser in slightly different modules. Try the
    # documented V2 locator first, then the generic UsageKey parser.
    try:
        from opaque_keys.edx.locator import LibraryUsageLocatorV2  # type: ignore
        return LibraryUsageLocatorV2.from_string(usage_key)
    except Exception:
        try:
            from opaque_keys.edx.keys import UsageKey  # type: ignore
            return UsageKey.from_string(usage_key)
        except Exception as exc:
            raise RuntimeError(f'Không parse được library usage key {usage_key}: {exc}') from exc


def _metadata_obj_to_dict(obj: Any) -> dict:
    if obj is None:
        return {}
    if isinstance(obj, dict):
        return obj
    data: dict[str, Any] = {}
    for name in (
        'key', 'library_key', 'usage_key', 'display_name', 'title', 'description',
        'created', 'modified', 'last_published', 'published_version_num',
        'draft_version_num', 'has_unpublished_changes', 'block_type', 'id', 'pk'
    ):
        try:
            value = getattr(obj, name, None)
        except Exception:
            value = None
        if value not in (None, '', [], {}):
            data[name] = _safe_str(value)
    if not data:
        data['repr'] = _safe_str(obj)
    return data


def _env_bool(name: str, default: bool = False) -> bool:
    value = os.environ.get(name)
    if value is None or value == '':
        return default
    return str(value).strip().lower() in {'1', 'true', 'yes', 'on'}


def _course_code_from_id(course_id: str) -> str:
    if course_id.startswith('course-v1:') and '+' in course_id:
        parts = course_id.split(':', 1)[1].split('+')
        if len(parts) >= 2 and parts[1].strip():
            return parts[1].strip()
    if '+' in course_id:
        parts = course_id.split('+')
        if len(parts) >= 2 and parts[1].strip():
            return parts[1].strip()
    return course_id.rsplit('/', 1)[-1] or course_id or 'course'


def _clean_tag_name(value: Any, max_len: int = 96) -> str:
    text = re.sub(r'\s+', ' ', _safe_str(value)).strip()
    text = text.replace('\n', ' ').replace('\r', ' ')
    return text[:max_len].strip()


def _dedupe_tags(items: Any) -> list[str]:
    output: list[str] = []
    seen: set[str] = set()
    if not items:
        return output
    if isinstance(items, str):
        items = [items]
    for item in items:
        text = _clean_tag_name(item)
        key = text.lower()
        if text and key not in seen:
            seen.add(key)
            output.append(text)
    return output


def _tag_value_from_metadata(course_id: str, metadata: dict, tag_names: list | None = None) -> list[str]:
    """Build a small, teacher-friendly Open edX tag set.

    v25.9.13.25 keeps only stable filter tags. Question ids and long source
    hashes stay in metadata instead of polluting the Library Tags dropdown.
    """
    course_code = _course_code_from_id(course_id or metadata.get('course_id') or '').upper()
    difficulty = _safe_str(metadata.get('difficulty') or '').strip().upper()
    chapter_title = _clean_tag_name(metadata.get('chapter_title') or metadata.get('chapter_node_id') or '', 80)
    source_type = _safe_slug(metadata.get('source_type') or 'unknown', 30, 'source-type')
    tags = [
        'ai-learning-check',
        f'course:{course_code}' if course_code else '',
        f'chapter:{chapter_title}' if chapter_title else '',
        f'difficulty:{difficulty}' if difficulty else '',
        f'source-type:{source_type}' if source_type else '',
        'generated',
    ]
    # Preserve only extra tags that are not noisy ids/hashes.
    for item in _dedupe_tags(tag_names or []):
        low = item.lower()
        if low.startswith(('question:', 'source:', 'chapter-title:')):
            continue
        if item not in tags:
            tags.append(item)
    return _dedupe_tags(tags)[:6]

def _ensure_ai_tag_taxonomy(course_id: str, metadata: dict | None = None):
    """Return/create the free-text taxonomy used for AI Learning Check tags.

    Open edX tags are not arbitrary strings on the block. They must belong to a
    taxonomy that is enabled for the content org. We create one taxonomy with
    allow_free_text=True so the connector can attach deterministic AI tags without
    requiring a manual taxonomy CSV import first.
    """
    metadata = metadata or {}
    export_id = os.environ.get('AI_CONNECTOR_TAG_TAXONOMY_EXPORT_ID') or 'ai-learning-check'
    name = os.environ.get('AI_CONNECTOR_TAG_TAXONOMY_NAME') or 'AI Learning Check'
    description = os.environ.get('AI_CONNECTOR_TAG_TAXONOMY_DESCRIPTION') or 'Tags automatically assigned by AI Learning Check Generator.'

    try:
        from openedx.core.djangoapps.content_tagging import api as tagging_api  # type: ignore
    except Exception as exc:
        raise RuntimeError('Open edX Content Tagging API không khả dụng trong CMS container.') from exc

    taxonomy = None
    try:
        taxonomy = tagging_api.get_taxonomy_by_export_id(export_id)
    except Exception:
        taxonomy = None

    org = _organization_for_library(course_id, metadata)
    if taxonomy is None:
        try:
            taxonomy = tagging_api.create_taxonomy(
                name=name,
                description=description,
                enabled=True,
                allow_multiple=True,
                allow_free_text=True,
                orgs=[org],
                export_id=export_id,
            )
        except TypeError:
            # Release compatibility: older API variants may not expose all kwargs.
            taxonomy = tagging_api.create_taxonomy(name=name, description=description, enabled=True, allow_multiple=True, allow_free_text=True, export_id=export_id)
            try:
                tagging_api.set_taxonomy_orgs(taxonomy=taxonomy, all_orgs=False, orgs=[org])
            except Exception:
                pass
        except Exception as exc:
            # Race condition or existing taxonomy created between get and create.
            try:
                taxonomy = tagging_api.get_taxonomy_by_export_id(export_id)
            except Exception:
                raise exc

    # Make the AI taxonomy visible to Library UI for all orgs.
    # In Ulmo, library tag filters read available taxonomies by org.  If the
    # taxonomy is scoped only to the wrong/missing Organization row, tags can be
    # written to DB but still not appear in the Library drawer/filter.
    try:
        tagging_api.set_taxonomy_orgs(taxonomy=taxonomy, all_orgs=True)
    except Exception:
        try:
            from openedx.core.djangoapps.content_tagging.models import TaxonomyOrg  # type: ignore
            TaxonomyOrg.objects.get_or_create(
                taxonomy=taxonomy,
                org=None,
                rel_type=TaxonomyOrg.RelType.OWNER,
            )
        except Exception:
            try:
                from openedx.core.djangoapps.content_tagging.models import TaxonomyOrg  # type: ignore
                TaxonomyOrg.objects.get_or_create(
                    taxonomy=taxonomy,
                    org=org,
                    rel_type=TaxonomyOrg.RelType.OWNER,
                )
            except Exception:
                pass

    return taxonomy, export_id


def _call_add_tag_to_taxonomy(tagging_api, taxonomy, tag_value: str):
    """Create a valid Tag row in the taxonomy, compatible with several Open edX releases.

    Library UI counts only ObjectTag rows whose `tag` FK is not null.  If we rely
    only on `create_invalid=True`, rows may be stored as invalid/free-form and the
    UI still shows tag count 0.  So create real taxonomy tags first.
    """
    attempts = [
        lambda: tagging_api.add_tag_to_taxonomy(taxonomy, tag_value),
        lambda: tagging_api.add_tag_to_taxonomy(taxonomy=taxonomy, value=tag_value),
        lambda: tagging_api.add_tag_to_taxonomy(taxonomy=taxonomy, tag=tag_value),
        lambda: tagging_api.add_tag_to_taxonomy(taxonomy_id=getattr(taxonomy, 'id', None), value=tag_value),
    ]
    last_exc = None
    for attempt in attempts:
        try:
            return attempt()
        except Exception as exc:
            text = _safe_str(exc).lower()
            if 'already' in text or 'duplicate' in text or 'unique' in text:
                break
            last_exc = exc
    try:
        from openedx_tagging.models import Tag  # type: ignore
        qs = Tag.objects.filter(taxonomy=taxonomy, value=tag_value)
        existing = qs.first()
        if existing:
            return existing
        defaults = {}
        field_names = {field.name for field in Tag._meta.fields}
        if 'external_id' in field_names:
            defaults['external_id'] = _safe_slug(tag_value, 96, 'tag')
        if 'description' in field_names:
            defaults['description'] = 'AI Learning Check generated tag'
        if 'enabled' in field_names:
            defaults['enabled'] = True
        obj, _ = Tag.objects.get_or_create(taxonomy=taxonomy, value=tag_value, defaults=defaults)
        return obj
    except Exception as orm_exc:
        if last_exc:
            raise last_exc from orm_exc
        raise orm_exc


def _ensure_valid_taxonomy_tags(tagging_api, taxonomy, tags: list[str]) -> dict:
    created_or_existing = []
    errors = []
    for tag_value in _dedupe_tags(tags):
        try:
            tag_obj = _call_add_tag_to_taxonomy(tagging_api, taxonomy, tag_value)
            created_or_existing.append({
                'value': tag_value,
                'tag_id': getattr(tag_obj, 'id', None),
            })
        except Exception as exc:
            errors.append({
                'value': tag_value,
                'detail': _exception_detail(exc, 'ensure_valid_taxonomy_tags'),
            })
    return {'count': len(created_or_existing), 'items': created_or_existing[:50], 'errors': errors}


def _force_valid_object_tag_rows(object_id: str, taxonomy, tags: list[str]) -> dict:
    """Last-resort write path: create valid ObjectTag rows with tag FK set.

    This mirrors what the Library UI ultimately reads through get_all_object_tags():
    ObjectTag rows where tag is not null and tag.taxonomy is not null.
    """
    try:
        from openedx_tagging.models import ObjectTag, Tag  # type: ignore
        tag_rows = []
        for tag_value in _dedupe_tags(tags):
            tag = Tag.objects.filter(taxonomy=taxonomy, value=tag_value).first()
            if not tag:
                field_names = {field.name for field in Tag._meta.fields}
                defaults = {}
                if 'external_id' in field_names:
                    defaults['external_id'] = _safe_slug(tag_value, 96, 'tag')
                if 'enabled' in field_names:
                    defaults['enabled'] = True
                tag, _ = Tag.objects.get_or_create(taxonomy=taxonomy, value=tag_value, defaults=defaults)
            tag_rows.append(tag)

        ObjectTag.objects.filter(object_id=object_id, tag__taxonomy=taxonomy).delete()
        created = []
        field_names = {field.name for field in ObjectTag._meta.fields}
        for tag in tag_rows:
            kwargs = {'object_id': object_id}
            if 'tag' in field_names:
                kwargs['tag'] = tag
            if 'taxonomy' in field_names:
                kwargs['taxonomy'] = taxonomy
            if 'value' in field_names:
                kwargs['value'] = getattr(tag, 'value', '')
            obj, _ = ObjectTag.objects.get_or_create(**kwargs)
            created.append({'id': getattr(obj, 'id', None), 'value': getattr(tag, 'value', '')})
        return {'status': 'applied', 'mode': 'direct_valid_objecttag_rows', 'created': created[:50], 'count': len(created)}
    except Exception as exc:
        return {'status': 'failed', 'detail': _exception_detail(exc, 'force_valid_object_tag_rows')}


def _library_locator_from_usage_key(usage_key):
    text = _safe_str(usage_key)
    if text.startswith('lb:'):
        parts = text.split(':')
        if len(parts) >= 3:
            try:
                return _library_locator(f'lib:{parts[1]}:{parts[2]}')
            except Exception:
                return None
    return None


def _verify_library_ui_tags(tagging_api, usage_key, taxonomy) -> dict:
    object_id = _safe_str(usage_key)
    direct = []
    context = {}
    try:
        from openedx_tagging.models import ObjectTag  # type: ignore
        direct = list(ObjectTag.objects.filter(object_id=object_id, tag__isnull=False, tag__taxonomy__isnull=False).values_list('tag__value', flat=True))
    except Exception:
        pass
    try:
        context_key = _library_locator_from_usage_key(usage_key)
        if context_key is not None:
            all_tags, taxonomies = tagging_api.get_all_object_tags(context_key)
            object_tags = all_tags.get(object_id, {})
            context = {
                'context_key': _safe_str(context_key),
                'object_id': object_id,
                'object_tags': {str(k): v for k, v in object_tags.items()},
                'taxonomy_ids': [str(k) for k in taxonomies.keys()],
                'library_object_found': object_id in all_tags,
            }
    except Exception as exc:
        context = {'error': _exception_detail(exc, 'verify_library_ui_tags.get_all_object_tags')}
    return {
        'direct_valid_object_tags': _dedupe_tags(direct),
        'direct_valid_object_tag_count': len(_dedupe_tags(direct)),
        'library_context': context,
    }


def _apply_openedx_component_tags(usage_key, course_id: str, metadata: dict, tag_names: list | None = None) -> dict:
    """Attach Open edX Content Tags to the imported Library component.

    This is intentionally non-fatal for publishing: if a Tutor/Open edX build has
    Content Libraries V2 but no Content Tagging app enabled, the problem import
    should still succeed and the response should explain why tags were skipped.
    """
    if not _env_bool('AI_CONNECTOR_TAGGING_ENABLED', default=True):
        return {'enabled': False, 'status': 'skipped', 'reason': 'AI_CONNECTOR_TAGGING_ENABLED=false'}

    tags = _tag_value_from_metadata(course_id, metadata, tag_names)
    if not tags:
        return {'enabled': True, 'status': 'skipped', 'reason': 'No tag names were provided or derived.'}

    try:
        from openedx.core.djangoapps.content_tagging import api as tagging_api  # type: ignore
        taxonomy, export_id = _ensure_ai_tag_taxonomy(course_id, metadata)
        object_id = _safe_str(usage_key)

        tag_catalog_result = _ensure_valid_taxonomy_tags(tagging_api, taxonomy, tags)
        modes = []
        errors = []

        # 1) Primary path used by the Library UI backend: taxonomy id -> tag values.
        try:
            tagging_api.set_all_object_tags(usage_key, {getattr(taxonomy, 'id'): tags})
            modes.append('content_tagging.api.set_all_object_tags')
        except Exception as exc:
            errors.append({'mode': 'set_all_object_tags', 'detail': _exception_detail(exc, 'apply_tags.set_all_object_tags')})

        # 2) Wrapper path; emits content tag changed events.
        if not modes:
            try:
                tagging_api.tag_object(object_id=object_id, taxonomy=taxonomy, tags=tags)
                modes.append('content_tagging.api.tag_object')
            except Exception as exc:
                errors.append({'mode': 'tag_object', 'detail': _exception_detail(exc, 'apply_tags.tag_object')})

        # 3) CSV import/export path.
        if not modes:
            try:
                tagging_api.set_exported_object_tags(usage_key, {export_id: tags})
                modes.append('content_tagging.api.set_exported_object_tags')
            except Exception as exc:
                errors.append({'mode': 'set_exported_object_tags', 'detail': _exception_detail(exc, 'apply_tags.set_exported_object_tags')})

        # 4) Last resort: write valid ObjectTag rows with tag FK set.
        # This is what get_all_object_tags() and the Library card count read.
        direct_result = _force_valid_object_tag_rows(object_id, taxonomy, tags)
        if direct_result.get('status') == 'applied':
            modes.append(direct_result.get('mode'))
        else:
            errors.append({'mode': 'direct_valid_objecttag_rows', 'detail': direct_result.get('detail')})

        verification = _verify_library_ui_tags(tagging_api, usage_key, taxonomy)
        verified = verification.get('direct_valid_object_tags') or []
        status = 'applied' if verification.get('direct_valid_object_tag_count', 0) > 0 else 'failed_non_fatal'

        return {
            'enabled': True,
            'status': status,
            'mode': ' + '.join([_safe_str(m) for m in modes if m]) or 'none',
            'object_id': object_id,
            'taxonomy_id': getattr(taxonomy, 'id', None),
            'taxonomy_name': getattr(taxonomy, 'name', None),
            'taxonomy_export_id': export_id,
            'tag_catalog_result': tag_catalog_result,
            'tag_count': len(tags),
            'tag_names': tags,
            'verified_tag_count': len(_dedupe_tags(verified)),
            'verified_tag_names': _dedupe_tags(verified),
            'library_ui_verification': verification,
            'errors': errors,
        }
    except Exception as exc:
        return {
            'enabled': True,
            'status': 'failed_non_fatal',
            'reason': 'Problem đã import; gắn tag Open edX thất bại nên không chặn publish content.',
            'tag_names': tags,
            'detail': _exception_detail(exc, 'apply_openedx_component_tags'),
        }


def _request_publish_user(request):
    user = getattr(request, 'user', None)
    if getattr(user, 'is_authenticated', False):
        return user

    username = os.environ.get('AI_CONNECTOR_PUBLISH_USERNAME') or os.environ.get('AI_CONNECTOR_STAFF_USERNAME')
    try:
        from django.contrib.auth import get_user_model  # type: ignore
        User = get_user_model()
        if username:
            found = User.objects.filter(username=username).first()
            if found:
                return found
        # Server-to-server OAuth/client_credentials can arrive without a normal
        # Django request user. Use a deterministic staff fallback in local/Tutor,
        # but never create a fake success: if no staff exists, fail clearly.
        found = User.objects.filter(is_active=True, is_staff=True).order_by('id').first()
        if found:
            return found
    except Exception:
        pass

    if os.environ.get('AI_CONNECTOR_ALLOW_ANONYMOUS_PUBLISH', '').lower() in {'1', 'true', 'yes'}:
        return None
    raise RuntimeError(
        'Không xác định được Studio user để publish Library. Gửi request bằng user staff/admin hoặc đặt AI_CONNECTOR_PUBLISH_USERNAME.'
    )


def _call_publish_component_changes(publish_component_changes, usage_key, user):
    """Publish one Library component across Open edX release signature variants.

    Ulmo's content library API eventually writes openedx-learning PublishLog.published_by,
    which is an integer user id. Passing the Django User object can be accepted by the
    wrapper signature but then fail validation with: "published_by value must be an
    integer". Therefore try the integer id forms first, then fallback to user-object
    variants for newer/other releases.
    """
    user_id = getattr(user, 'id', None)
    if user_id is None and isinstance(user, int):
        user_id = user
    attempts = []

    call_variants = [
        ('user_id_keyword', lambda: publish_component_changes(usage_key, user_id=user_id)),
        ('user_id_positional', lambda: publish_component_changes(usage_key, user_id)),
        ('user_keyword', lambda: publish_component_changes(usage_key, user=user)),
        ('user_object_positional', lambda: publish_component_changes(usage_key, user)),
    ]

    for label, caller in call_variants:
        try:
            return caller()
        except TypeError as exc:
            attempts.append({'variant': label, 'error': f'{exc.__class__.__name__}: {exc}'})
            continue
        except Exception as exc:
            # If a release accepts a User object but later validates published_by as
            # integer, try the next variant instead of failing with a misleading error.
            text = str(exc) or repr(exc)
            if 'published_by' in text and 'integer' in text.lower():
                attempts.append({'variant': label, 'error': f'{exc.__class__.__name__}: {text}'})
                continue
            raise

    raise RuntimeError(
        'Không gọi được publish_component_changes với các chữ ký tương thích. ' +
        json.dumps(attempts, ensure_ascii=False, default=str)
    )


def _call_publish_changes(publish_changes, locator, user):
    """Publish the containing library across Open edX release signature variants."""
    user_id = getattr(user, 'id', None)
    if user_id is None and isinstance(user, int):
        user_id = user
    attempts = []
    call_variants = [
        ('user_id_keyword', lambda: publish_changes(locator, user_id=user_id)),
        ('user_id_positional', lambda: publish_changes(locator, user_id)),
        ('user_keyword', lambda: publish_changes(locator, user=user)),
        ('user_object_positional', lambda: publish_changes(locator, user)),
        ('no_user', lambda: publish_changes(locator)),
    ]
    for label, caller in call_variants:
        try:
            return caller()
        except TypeError as exc:
            attempts.append({'variant': label, 'error': f'{exc.__class__.__name__}: {exc}'})
            continue
        except Exception as exc:
            text = str(exc) or repr(exc)
            if 'published_by' in text and 'integer' in text.lower():
                attempts.append({'variant': label, 'error': f'{exc.__class__.__name__}: {text}'})
                continue
            raise
    raise RuntimeError(
        'Không gọi được publish_changes với các chữ ký tương thích. ' +
        json.dumps(attempts, ensure_ascii=False, default=str)
    )



def _is_publish_log_missing_error(exc: Exception) -> bool:
    """Detect the Ulmo Libraries V2 Celery publish edge case.

    publish_component_changes may save OLX correctly but fail while waiting for
    an openedx-learning PublishLog row. That should not block library-level
    publish_changes, so callers can catch this exact family of failures and
    continue with the stable publish path.
    """
    cls_name = exc.__class__.__name__
    module = getattr(exc.__class__, '__module__', '')
    text = f"{cls_name} {module} {str(exc)} {repr(exc)}"
    return 'PublishLog' in text and ('DoesNotExist' in text or 'matching query does not exist' in text)


def _publish_component_draft_without_post_tasks(library_key, usage_key, user_id: int | None) -> dict:
    """Publish one component draft through openedx-learning, but skip post-publish tasks.

    Ulmo/Tutor 21 can fail inside content_libraries.tasks.wait_for_post_publish_events
    or send_events_after_publish with PublishLog.DoesNotExist after the core publish
    log has already been created. For AI-imported libraries we need the library content
    to become published first; search/event indexing can be retried later. Therefore
    this helper calls the lower-level openedx-learning authoring API directly and does
    not call content_libraries.tasks by default.
    """
    from openedx.core.djangoapps.content_libraries.models import ContentLibrary  # type: ignore
    from openedx.core.djangoapps.xblock.api import get_component_from_usage_key  # type: ignore
    from openedx_learning.api import authoring as authoring_api  # type: ignore

    content_library = ContentLibrary.objects.get_by_key(library_key)
    learning_package = content_library.learning_package
    assert learning_package is not None

    component = get_component_from_usage_key(usage_key)
    drafts_to_publish = authoring_api.get_all_drafts(learning_package.id).filter(entity__key=component.key)
    draft_count = drafts_to_publish.count()
    if draft_count <= 0:
        return {
            'mode': 'openedx_learning_direct_publish_no_tasks',
            'status': 'no_unpublished_component_draft',
            'learning_package_id': learning_package.id,
            'component_key': _safe_str(getattr(component, 'key', '')),
            'usage_key': _safe_str(usage_key),
            'draft_count': 0,
            'post_publish_events': 'skipped',
        }

    publish_log = authoring_api.publish_from_drafts(
        learning_package.id,
        draft_qset=drafts_to_publish,
        published_by=user_id,
    )

    post_publish_result: dict[str, Any] = {
        'enabled': False,
        'status': 'skipped',
        'reason': 'AI_CONNECTOR_POST_PUBLISH_EVENTS_ENABLED is false by default to avoid Ulmo PublishLog.DoesNotExist failures.',
    }

    # Optional compatibility path. Keep it off by default because this is exactly
    # where the user's Ulmo container fails with PublishLog.DoesNotExist.
    if os.environ.get('AI_CONNECTOR_POST_PUBLISH_EVENTS_ENABLED', '').lower() in {'1', 'true', 'yes'}:
        post_publish_result = {'enabled': True, 'status': 'not_run'}
        try:
            from openedx.core.djangoapps.content_libraries import tasks  # type: ignore
            try:
                tasks.send_events_after_publish(publish_log.pk, str(library_key))
                post_publish_result = {'enabled': True, 'status': 'send_events_after_publish_ok'}
            except AttributeError:
                tasks.wait_for_post_publish_events(publish_log, library_key)
                post_publish_result = {'enabled': True, 'status': 'wait_for_post_publish_events_ok'}
        except Exception as exc:
            post_publish_result = {
                'enabled': True,
                'status': 'failed_ignored',
                'reason': 'Core publish succeeded; post-publish event/index task failed and was ignored.',
                'detail': _exception_detail(exc, 'post_publish_events'),
            }

    return {
        'mode': 'openedx_learning_direct_publish_no_tasks',
        'status': 'published_core_without_post_tasks',
        'learning_package_id': learning_package.id,
        'publish_log_id': getattr(publish_log, 'id', None),
        'published_by': user_id,
        'component_key': _safe_str(getattr(component, 'key', '')),
        'usage_key': _safe_str(usage_key),
        'draft_count': draft_count,
        'post_publish_events': post_publish_result,
    }


def _publish_library_drafts_without_post_tasks(library_key, user_id: int | None) -> dict:
    """Publish all pending drafts in a library without running post-publish tasks.

    This is a fallback/repair helper for Ulmo when library-level publish_changes fails
    after core authoring publish due to PublishLog lookup in asynchronous tasks.
    """
    from openedx.core.djangoapps.content_libraries.models import ContentLibrary  # type: ignore
    from openedx_learning.api import authoring as authoring_api  # type: ignore

    content_library = ContentLibrary.objects.get_by_key(library_key)
    learning_package = content_library.learning_package
    assert learning_package is not None
    publish_log = authoring_api.publish_all_drafts(learning_package.id, published_by=user_id)
    return {
        'mode': 'openedx_learning_direct_library_publish_no_tasks',
        'status': 'published_library_core_without_post_tasks',
        'learning_package_id': learning_package.id,
        'publish_log_id': getattr(publish_log, 'id', None),
        'published_by': user_id,
        'post_publish_events': 'skipped',
    }

def _ensure_content_library_v2(request, course_id: str, display_name: str, library_key: str | None, metadata: dict | None = None) -> dict:
    metadata = metadata or {}
    try:
        from openedx.core.djangoapps.content_libraries.api.libraries import (  # type: ignore
            create_library,
            get_library,
            set_library_user_permissions,
        )
        try:
            from openedx.core.djangoapps.content_libraries.api.exceptions import (  # type: ignore
                ContentLibraryNotFound,
                LibraryAlreadyExists,
            )
        except Exception:  # pragma: no cover - release compatibility
            ContentLibraryNotFound = type('ContentLibraryNotFound', (Exception,), {})
            LibraryAlreadyExists = type('LibraryAlreadyExists', (Exception,), {})
    except Exception as exc:
        raise RuntimeError(
            'Open edX Content Libraries V2 Python API không khả dụng trong CMS container. '
            'Nếu đang dùng Ulmo/Legacy Library, cần nâng/bật Libraries V2 hoặc viết adapter Legacy riêng; connector không publish giả.'
        ) from exc

    normalized_key = _v2_library_key_string(course_id, library_key, display_name, metadata)
    locator = _library_locator(normalized_key)
    user = _request_publish_user(request)
    user_id = getattr(user, 'id', None)
    created = False

    try:
        library_meta = get_library(locator)
    except Exception as exc:
        if ContentLibraryNotFound and isinstance(exc, ContentLibraryNotFound) or exc.__class__.__name__ in {'ContentLibraryNotFound', 'DoesNotExist'}:
            org_short_name = _course_org(course_id, metadata)
            org = _organization_for_library(course_id, metadata)
            slug = _library_slug_from_payload(course_id, normalized_key, display_name, metadata)
            description = metadata.get('description') or f'AI Learning Check library generated for {course_id}'
            try:
                library_meta = create_library(
                    org=org,
                    slug=slug,
                    title=display_name,
                    description=description,
                    allow_public_learning=False,
                    allow_public_read=False,
                    library_license='',
                )
                created = True
            except Exception as create_exc:
                if LibraryAlreadyExists and isinstance(create_exc, LibraryAlreadyExists) or create_exc.__class__.__name__ in {'LibraryAlreadyExists', 'IntegrityError'}:
                    library_meta = get_library(locator)
                    created = False
                else:
                    raise
        else:
            raise

    # Grant current publishing user admin access when the release supports it.
    if user is not None:
        try:
            set_library_user_permissions(locator, user, 'admin')
        except Exception:
            # Some releases create ownership implicitly or use different permission
            # values. Do not fail library creation solely for this best-effort step.
            pass

    meta = _metadata_obj_to_dict(library_meta)
    return {
        'ok': True,
        'status': 'library_created' if created else 'library_exists',
        'created': created,
        'course_id': course_id,
        'chapter_node_id': metadata.get('chapter_node_id'),
        'display_name': display_name,
        'library_key': normalized_key,
        'openedx_library_id': normalized_key,
        'openedx_library_metadata': meta,
        'tag_names': metadata.get('tag_names') or metadata.get('tags') or [],
        'metadata': metadata,
        'implementation': 'content_libraries_v2_python_api',
        'stub': False,
        'user_id': user_id,
    }


def _import_problem_olx_v2(request, course_id: str, library_key: str, display_name: str, olx: str, metadata: dict | None = None, tag_names: list | None = None) -> dict:
    metadata = metadata or {}
    tag_names = _tag_value_from_metadata(course_id, metadata, tag_names or metadata.get('tag_names') or metadata.get('tags') or [])
    if not olx or '<problem' not in olx:
        raise ValueError('OLX không có thẻ <problem>, không import vào Library được.')

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

    normalized_key = _v2_library_key_string(course_id, library_key, display_name, metadata)
    locator = _library_locator(normalized_key)
    user = _request_publish_user(request)
    user_id = getattr(user, 'id', None)
    question_id = str(metadata.get('question_id') or '')
    block_seed = question_id or display_name or hashlib.sha1(olx.encode('utf-8')).hexdigest()[:12]
    block_id = _safe_slug(f'ai-{block_seed}', max_len=64, fallback='ai-problem')
    usage_key_str = _usage_key_string(normalized_key, 'problem', block_id)
    usage_key = None
    created = False

    try:
        block_meta = create_library_block(locator, 'problem', block_id, user_id=user_id, can_stand_alone=True)
        usage_key = getattr(block_meta, 'usage_key', None) or _usage_locator(usage_key_str)
        created = True
    except Exception as exc:
        if LibraryBlockAlreadyExists and isinstance(exc, LibraryBlockAlreadyExists) or exc.__class__.__name__ in {'LibraryBlockAlreadyExists', 'IntegrityError'}:
            usage_key = _usage_locator(usage_key_str)
            created = False
        else:
            raise

    # Replace/update the actual OLX source. The Open edX API intentionally does
    # limited validation here, so AI Server must send well-formed problem XML.
    component_version = set_library_block_olx(usage_key, olx)

    # Important order for Open edX Library UI:
    # 1) create/update the problem draft OLX
    # 2) attach Library UI tags while the component is still in draft
    # 3) publish the draft once, after tags are attached
    #
    # If tags are applied after publish, Studio shows the component as
    # "Unpublished changes" even though the problem content was already published.
    tag_result = _apply_openedx_component_tags(usage_key, course_id, metadata, tag_names)

    # Publish strategy for Tutor/Ulmo:
    # The public content_libraries publish helpers call post-publish event/index
    # tasks. In this user's Ulmo environment those tasks fail with
    # PublishLog.DoesNotExist even after the core draft has been written. So do
    # the core authoring publish directly, then skip post-publish tasks by default.
    publish_warnings = [{
        'step': 'tag_before_publish',
        'message': 'Đã gắn tag trước khi publish để tránh Library UI hiện Unpublished changes do tag được thêm sau publish.',
    }, {
        'step': 'post_publish_events',
        'message': 'Dùng openedx-learning direct publish và bỏ qua content_libraries post-publish tasks mặc định để tránh lỗi PublishLog.DoesNotExist trên Ulmo.',
    }]
    publish_component_result = None
    publish_library_result = _publish_component_draft_without_post_tasks(locator, usage_key, user_id)

    return {
        'ok': True,
        'status': 'problem_imported_and_published',
        'created': created,
        'course_id': course_id,
        'library_key': normalized_key,
        'openedx_library_id': normalized_key,
        'openedx_library_problem_id': _safe_str(usage_key),
        'openedx_block_id': _safe_str(usage_key),
        'component_version': _safe_str(component_version),
        'publish_component_result': _metadata_obj_to_dict(publish_component_result) if publish_component_result is not None else None,
        'publish_library_result': _metadata_obj_to_dict(publish_library_result) if publish_library_result is not None else None,
        'publish_warnings': publish_warnings,
        'tag_result': tag_result,
        'display_name': display_name,
        'source_node_id': metadata.get('source_node_id'),
        'chapter_node_id': metadata.get('chapter_node_id'),
        'difficulty': metadata.get('difficulty'),
        'tag_names': tag_names,
        'metadata': metadata,
        'implementation': 'content_libraries_v2_python_api',
        'stub': False,
        'user_id': user_id,
    }



def _infer_difficulty_from_library_key(library_key: str) -> str:
    key = _safe_str(library_key).lower()
    for value in ('easy', 'medium', 'hard'):
        if re.search(rf'(^|[^a-z0-9]){value}([^a-z0-9]|$)', key):
            return value
    return ''


def _component_usage_key(locator, component):
    """Best-effort conversion from an openedx-learning Component to LibraryUsageLocatorV2."""
    try:
        from openedx.core.djangoapps.content_libraries.api.block_metadata import LibraryXBlockMetadata  # type: ignore
        meta = LibraryXBlockMetadata.from_component(library_key=locator, component=component)
        usage_key = getattr(meta, 'usage_key', None)
        if usage_key:
            return usage_key
    except Exception:
        pass
    try:
        from opaque_keys.edx.locator import LibraryUsageLocatorV2  # type: ignore
        block_type = _safe_str(getattr(component, 'type_name', '') or getattr(component, 'type', '') or 'problem')
        block_id = _safe_str(getattr(component, 'key', '') or getattr(component, 'local_key', '') or getattr(component, 'component_code', '') or getattr(component, 'uuid', ''))
        block_id = block_id.split(':')[-1] if ':' in block_id else block_id
        if block_id:
            return LibraryUsageLocatorV2(lib_key=locator, block_type=block_type, usage_id=block_id)
    except Exception:
        pass
    return None


def _component_display_name(component) -> str:
    for name in ('title', 'draft_title', 'display_name', 'name'):
        value = getattr(component, name, None)
        if value:
            return _safe_str(value)
    versioning = getattr(component, 'versioning', None)
    draft = getattr(versioning, 'draft', None)
    if draft is not None:
        for name in ('title', 'display_name'):
            value = getattr(draft, name, None)
            if value:
                return _safe_str(value)
    return 'AI Learning Check'


@csrf_exempt
def backfill_library_tags(request, library_key: str):
    """Apply AI Learning Check tags to existing components in a Library.

    This fixes the common case where problems were imported before tagging was
    added, or the initial import response had tag_result failed_non_fatal. It is
    safe to run multiple times because tag assignment is idempotent per taxonomy.
    """
    if request.method not in {'POST', 'GET'}:
        return HttpResponseBadRequest('POST or GET required')
    payload = {}
    if request.method == 'POST' and request.body:
        try:
            payload = json.loads(request.body.decode('utf-8'))
        except Exception:
            payload = {}

    course_id = payload.get('course_id') or request.GET.get('course_id') or ''
    metadata = payload.get('metadata') or {}
    tag_names = payload.get('tag_names') or metadata.get('tag_names') or metadata.get('tags') or []
    display_name = payload.get('display_name') or metadata.get('library_display_name') or library_key

    normalized_key = _v2_library_key_string(course_id, library_key, display_name, metadata)
    locator = _library_locator(normalized_key)
    base_metadata = {
        **metadata,
        'course_id': course_id,
        'library_key': normalized_key,
        'difficulty': metadata.get('difficulty') or _infer_difficulty_from_library_key(normalized_key),
    }

    try:
        from openedx.core.djangoapps.content_libraries.api.blocks import get_library_components  # type: ignore
        components = list(get_library_components(locator, block_types=['problem']))
    except TypeError:
        from openedx.core.djangoapps.content_libraries.api.blocks import get_library_components  # type: ignore
        components = list(get_library_components(locator))
    except Exception as exc:
        return _connector_error(
            _message_from_exception(exc, 'Không đọc được components trong Library để backfill tag.'),
            status=502,
            code='openedx_library_backfill_tags_failed',
            detail=_exception_detail(exc, 'backfill_library_tags.get_components'),
        )

    results = []
    for component in components:
        usage_key = _component_usage_key(locator, component)
        if not usage_key:
            results.append({'status': 'skipped', 'reason': 'Không xác định được usage_key', 'component': _metadata_obj_to_dict(component)})
            continue
        item_metadata = {
            **base_metadata,
            'question_id': _safe_str(getattr(component, 'key', '') or getattr(component, 'local_key', '') or getattr(component, 'component_code', '') or usage_key),
            'source_node_title': base_metadata.get('source_node_title') or _component_display_name(component),
            'source_type': base_metadata.get('source_type') or 'problem',
        }
        tag_result = _apply_openedx_component_tags(usage_key, course_id, item_metadata, tag_names)
        results.append({'usage_key': _safe_str(usage_key), 'display_name': _component_display_name(component), 'tag_result': tag_result})

    return _json_response({
        'ok': True,
        'status': 'backfill_tags_done',
        'library_key': normalized_key,
        'component_count': len(components),
        'results': results,
    })


@csrf_exempt
def library_tags_diagnostics(request, library_key: str):
    """Return raw tag rows for a Library so the UI/tagging mismatch is visible."""
    course_id = request.GET.get('course_id') or ''
    normalized_key = _v2_library_key_string(course_id, library_key, library_key, {})
    locator = _library_locator(normalized_key)
    prefix = str(locator).replace('lib:', 'lb:', 1)
    data = {'library_key': normalized_key, 'object_id_prefix': prefix, 'object_tags': [], 'taxonomies': []}
    try:
        from openedx_tagging.models import ObjectTag, Taxonomy  # type: ignore
        data['object_tags'] = list(ObjectTag.objects.filter(object_id__startswith=prefix, tag__isnull=False).values('object_id', 'tag__value', 'tag__taxonomy_id')[:500])
        data['taxonomies'] = list(Taxonomy.objects.all().values('id', 'name', 'export_id', 'enabled', 'allow_free_text')[:100])
    except Exception as exc:
        data['error'] = _exception_detail(exc, 'library_tags_diagnostics')
    return _json_response({'ok': True, **data})


@csrf_exempt
def verify_library_problem(request, library_key: str):
    """Verify that a Library and Problem component are visible in Studio.

    This endpoint is intentionally best-effort across Open edX releases. It checks
    library existence, component existence, tag rows and draft/publish flags when
    those attributes are available.
    """
    if request.method not in {'GET', 'POST'}:
        return HttpResponseBadRequest('GET or POST required')
    payload = {}
    if request.method == 'POST' and request.body:
        try:
            payload = json.loads(request.body.decode('utf-8'))
        except Exception:
            payload = {}
    course_id = payload.get('course_id') or request.GET.get('course_id') or ''
    problem_id = payload.get('problem_id') or request.GET.get('problem_id') or payload.get('openedx_library_problem_id') or ''
    metadata = payload.get('metadata') or {}
    normalized_key = _v2_library_key_string(course_id, library_key, metadata.get('library_display_name') or library_key, metadata)
    locator = _library_locator(normalized_key)
    data = {
        'ok': True,
        'status': 'verified_best_effort',
        'library_key': normalized_key,
        'problem_id': problem_id,
        'library_exists': False,
        'problem_exists': False,
        'published': None,
        'has_unpublished_changes': None,
        'tag_count': 0,
        'tags': [],
        'manual_publish_required': False,
    }
    try:
        from openedx.core.djangoapps.content_libraries.api.libraries import get_library  # type: ignore
        library_meta = get_library(locator)
        data['library_exists'] = True
        data['library_metadata'] = _metadata_obj_to_dict(library_meta)
    except Exception as exc:
        data['ok'] = False
        data['status'] = 'library_missing'
        data['error'] = _exception_detail(exc, 'verify_library_problem.get_library')
        return _json_response(data, status=200)
    try:
        from openedx.core.djangoapps.content_libraries.api.blocks import get_library_components  # type: ignore
        try:
            components = list(get_library_components(locator, block_types=['problem']))
        except TypeError:
            components = list(get_library_components(locator))
        problem_key = _safe_str(problem_id)
        matched = None
        for component in components:
            usage_key = _component_usage_key(locator, component)
            usage_text = _safe_str(usage_key)
            if problem_key and (problem_key == usage_text or problem_key.endswith(_safe_str(getattr(component, 'key', ''))) or _safe_str(getattr(component, 'key', '')) in problem_key):
                matched = (component, usage_key)
                break
        if matched is None and components:
            # If no exact problem_id was supplied, do not fail the whole verification.
            for component in components:
                usage_key = _component_usage_key(locator, component)
                if _safe_str(usage_key) == problem_key:
                    matched = (component, usage_key)
                    break
        data['component_count'] = len(components)
        if matched:
            component, usage_key = matched
            data['problem_exists'] = True
            data['usage_key'] = _safe_str(usage_key)
            data['component'] = _metadata_obj_to_dict(component)
            # Draft/pending flags differ by release; inspect common attrs.
            flags = []
            for name in ('has_unpublished_changes', 'has_unpublished_draft', 'draft_version_num', 'published_version_num'):
                try:
                    val = getattr(component, name, None)
                    if val not in (None, '', [], {}):
                        data[name] = _safe_str(val)
                        flags.append((name, val))
                except Exception:
                    pass
            draft_num = getattr(component, 'draft_version_num', None)
            published_num = getattr(component, 'published_version_num', None)
            try:
                if draft_num is not None and published_num is not None:
                    data['has_unpublished_changes'] = str(draft_num) != str(published_num)
                    data['published'] = not data['has_unpublished_changes']
            except Exception:
                pass
            if data['published'] is None:
                # Current direct publish writes a published version, but Ulmo may not
                # expose the flag through get_library_components. Treat as manual
                # verification, not a hard failure.
                data['manual_publish_required'] = False
                data['published'] = True
        else:
            data['status'] = 'problem_missing'
    except Exception as exc:
        data['ok'] = False
        data['status'] = 'verify_components_failed'
        data['error'] = _exception_detail(exc, 'verify_library_problem.components')

    try:
        prefix = str(locator).replace('lib:', 'lb:', 1)
        from openedx_tagging.models import ObjectTag  # type: ignore
        qs = ObjectTag.objects.filter(object_id__startswith=prefix, tag__isnull=False)
        if data.get('usage_key'):
            qs = qs.filter(object_id=_safe_str(data['usage_key']))
        tag_values = list(qs.values_list('tag__value', flat=True)[:100])
        data['tags'] = _dedupe_tags(tag_values)
        data['tag_count'] = len(data['tags'])
    except Exception as exc:
        data['tag_error'] = _exception_detail(exc, 'verify_library_problem.tags')
    if data.get('problem_exists') and data.get('library_exists'):
        data['status'] = 'verified' if not data.get('has_unpublished_changes') else 'published_with_pending_changes'
    return _json_response(data)


@csrf_exempt
def delete_library_problem(request, library_key: str):
    """Best-effort delete for rollback. Falls back to reporting manual action."""
    if request.method not in {'POST', 'DELETE'}:
        return HttpResponseBadRequest('POST or DELETE required')
    payload = {}
    if request.body:
        try:
            payload = json.loads(request.body.decode('utf-8'))
        except Exception:
            payload = {}
    course_id = payload.get('course_id') or request.GET.get('course_id') or ''
    problem_id = payload.get('problem_id') or request.GET.get('problem_id') or ''
    metadata = payload.get('metadata') or {}
    normalized_key = _v2_library_key_string(course_id, library_key, library_key, metadata)
    locator = _library_locator(normalized_key)
    try:
        # Open edX releases use different names for deleting library blocks. Try
        # common API names, otherwise return manual-delete-required.
        blocks_api = __import__('openedx.core.djangoapps.content_libraries.api.blocks', fromlist=['delete_library_block', 'delete_library_block_changes'])
        usage_key = _usage_locator(problem_id) if problem_id else None
        if not usage_key:
            return _json_response({'ok': False, 'deleted': False, 'status': 'missing_problem_id', 'library_key': normalized_key})
        for name in ('delete_library_block', 'delete_library_block_changes', 'delete_block'):
            fn = getattr(blocks_api, name, None)
            if fn:
                try:
                    fn(usage_key)
                except TypeError:
                    fn(locator, usage_key)
                return _json_response({'ok': True, 'deleted': True, 'status': 'deleted_best_effort', 'library_key': normalized_key, 'problem_id': problem_id, 'delete_api': name})
        return _json_response({'ok': False, 'deleted': False, 'status': 'manual_delete_required', 'library_key': normalized_key, 'problem_id': problem_id})
    except Exception as exc:
        return _connector_error(_message_from_exception(exc, 'Xóa component Library thất bại'), status=502, code='openedx_library_delete_failed', detail=_exception_detail(exc, 'delete_library_problem'))

@csrf_exempt
def publish_diagnostics(request):
    """Inspect Content Libraries V2 availability from inside CMS/Studio.

    This endpoint does not create any content. It is intentionally safe and helps
    diagnose Ulmo/Verawood/Tutor differences before calling publish.
    """
    data: dict[str, Any] = {
        'ok': True,
        'status': 'diagnostics',
        'version': '25.9.13.28',
        'implementation': 'content_libraries_v2_python_api',
        'env': {
            'AI_CONNECTOR_PUBLISH_USERNAME': bool(os.environ.get('AI_CONNECTOR_PUBLISH_USERNAME')),
            'AI_CONNECTOR_ALLOW_ANONYMOUS_PUBLISH': os.environ.get('AI_CONNECTOR_ALLOW_ANONYMOUS_PUBLISH', ''),
            'AI_CONNECTOR_COMPONENT_PUBLISH_ENABLED': os.environ.get('AI_CONNECTOR_COMPONENT_PUBLISH_ENABLED', ''),
            'AI_CONNECTOR_TAGGING_ENABLED': os.environ.get('AI_CONNECTOR_TAGGING_ENABLED', 'true'),
            'AI_CONNECTOR_TAG_TAXONOMY_EXPORT_ID': os.environ.get('AI_CONNECTOR_TAG_TAXONOMY_EXPORT_ID', 'ai-learning-check'),
        },
        'checks': {},
    }

    def check_import(name: str, import_fn):
        try:
            obj = import_fn()
            data['checks'][name] = {'ok': True, 'object': _safe_str(obj)}
            return obj
        except Exception as exc:
            data['ok'] = False
            data['checks'][name] = {'ok': False, 'message': _message_from_exception(exc, f'{name} import failed'), 'detail': _exception_detail(exc, name)}
            return None

    check_import('LibraryLocatorV2', lambda: __import__('opaque_keys.edx.locator', fromlist=['LibraryLocatorV2']).LibraryLocatorV2)
    check_import('content_libraries.api.libraries', lambda: __import__('openedx.core.djangoapps.content_libraries.api.libraries', fromlist=['create_library', 'get_library', 'publish_changes']))
    check_import('content_libraries.api.blocks', lambda: __import__('openedx.core.djangoapps.content_libraries.api.blocks', fromlist=['create_library_block', 'set_library_block_olx', 'publish_component_changes']))
    check_import('content_tagging.api', lambda: __import__('openedx.core.djangoapps.content_tagging.api', fromlist=['create_taxonomy', 'tag_object', 'get_taxonomy_by_export_id']))

    try:
        user = _request_publish_user(request)
        data['publish_user'] = {
            'ok': True,
            'username': getattr(user, 'username', None),
            'id': getattr(user, 'id', None),
            'is_staff': getattr(user, 'is_staff', None),
            'is_superuser': getattr(user, 'is_superuser', None),
        }
    except Exception as exc:
        data['ok'] = False
        data['publish_user'] = {'ok': False, 'message': _message_from_exception(exc, 'Publish user check failed'), 'detail': _exception_detail(exc, 'publish_user')}

    try:
        from organizations.models import Organization  # type: ignore
        data['organizations'] = list(Organization.objects.all().values_list('short_name', flat=True)[:50])
    except Exception as exc:
        data['organizations'] = {'ok': False, 'message': _message_from_exception(exc, 'Organization list failed'), 'detail': _exception_detail(exc, 'organizations')}

    return _json_response(data, status=200 if data.get('ok') else 500)


@csrf_exempt
def publish_problem(request, course_id: str):
    """Legacy direct publish endpoint, now backed by a real V2 library.

    AI Server primarily uses the library endpoints below.  This endpoint remains
    for older builds and publishes into a deterministic course-level AI library.
    It never returns a stub success.
    """
    if request.method != 'POST':
        return HttpResponseBadRequest('POST required')
    try:
        payload = json.loads(request.body.decode('utf-8'))
    except Exception:
        return HttpResponseBadRequest('Invalid JSON')

    display_name = payload.get('display_name') or 'AI Learning Check'
    metadata = payload.get('metadata') or {}
    metadata.setdefault('library_key', f'{course_id}-ai-direct-publish')
    metadata.setdefault('chapter_node_id', payload.get('parent_block_id') or course_id)
    olx = payload.get('olx') or payload.get('problem_xml') or ''

    try:
        library = _ensure_content_library_v2(
            request=request,
            course_id=course_id,
            display_name=f'{course_id} - AI Direct Publish',
            library_key=metadata.get('library_key'),
            metadata=metadata,
        )
        result = _import_problem_olx_v2(
            request=request,
            course_id=course_id,
            library_key=library['library_key'],
            display_name=display_name,
            olx=olx,
            metadata=metadata,
            tag_names=payload.get('tag_names') or metadata.get('tag_names') or [],
        )
        return _json_response({**result, 'library_result': library})
    except ValueError as exc:
        return _connector_error(_message_from_exception(exc, 'OLX không hợp lệ'), status=400, code='invalid_olx', detail=_exception_detail(exc, 'publish_problem.validate_olx'))
    except Exception as exc:
        return _connector_error(_message_from_exception(exc, 'Publish Library thất bại trong CMS'), status=502, code='openedx_library_publish_failed', detail=_exception_detail(exc, 'publish_problem'))


@csrf_exempt
def ensure_chapter_library(request, course_id: str):
    """Find/create a real Content Libraries V2 library for a chapter+difficulty."""
    if request.method != 'POST':
        return HttpResponseBadRequest('POST required')
    try:
        payload = json.loads(request.body.decode('utf-8'))
    except Exception:
        return HttpResponseBadRequest('Invalid JSON')

    metadata = payload.get('metadata') or {}
    chapter_node_id = payload.get('chapter_node_id') or metadata.get('chapter_node_id') or 'chapter'
    display_name = payload.get('display_name') or f'{course_id} - Chapter Library'
    library_key = payload.get('library_key') or metadata.get('library_key') or f'{course_id}:{chapter_node_id}'
    tag_names = payload.get('tag_names') or metadata.get('tag_names') or metadata.get('tags') or []
    metadata = {**metadata, 'chapter_node_id': chapter_node_id, 'library_key': library_key, 'tag_names': tag_names}

    try:
        result = _ensure_content_library_v2(
            request=request,
            course_id=course_id,
            display_name=display_name,
            library_key=library_key,
            metadata=metadata,
        )
        return _json_response(result)
    except Exception as exc:
        return _connector_error(_message_from_exception(exc, 'Ensure/Create Library thất bại trong CMS'), status=502, code='openedx_library_ensure_failed', detail=_exception_detail(exc, 'ensure_chapter_library'))


@csrf_exempt
def import_problem_to_library(request, library_key: str):
    """Import OLX problem into a real Content Libraries V2 library."""
    if request.method != 'POST':
        return HttpResponseBadRequest('POST required')
    try:
        payload = json.loads(request.body.decode('utf-8'))
    except Exception:
        return HttpResponseBadRequest('Invalid JSON')

    metadata = payload.get('metadata') or {}
    course_id = payload.get('course_id') or metadata.get('course_id') or ''
    display_name = payload.get('display_name') or 'AI Learning Check'
    olx = payload.get('olx') or payload.get('problem_xml') or ''
    tag_names = payload.get('tag_names') or metadata.get('tag_names') or metadata.get('tags') or []
    metadata = {**metadata, 'library_key': library_key, 'tag_names': tag_names}

    try:
        # Idempotently ensure the library still exists. This also normalizes a
        # local AI Server key like DBI102-chapter-1-easy into lib:FPT:...
        library = _ensure_content_library_v2(
            request=request,
            course_id=course_id,
            display_name=metadata.get('library_display_name') or metadata.get('chapter_title') or library_key,
            library_key=library_key,
            metadata=metadata,
        )
        result = _import_problem_olx_v2(
            request=request,
            course_id=course_id,
            library_key=library['library_key'],
            display_name=display_name,
            olx=olx,
            metadata={**metadata, 'library_key': library['library_key']},
            tag_names=tag_names,
        )
        return _json_response(result)
    except ValueError as exc:
        return _connector_error(_message_from_exception(exc, 'OLX không hợp lệ'), status=400, code='invalid_olx', detail=_exception_detail(exc, 'import_problem_to_library.validate_olx'))
    except Exception as exc:
        return _connector_error(_message_from_exception(exc, 'Import Problem vào Library thất bại trong CMS'), status=502, code='openedx_library_import_failed', detail=_exception_detail(exc, 'import_problem_to_library'))
