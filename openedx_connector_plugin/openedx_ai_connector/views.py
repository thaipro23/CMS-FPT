"""CMS/Studio connector for AI Learning Check Generator.

The endpoints in this file are intentionally thin and defensive.  They run inside
Open edX/Tutor, so they can use Studio/modulestore APIs that the external Course
Blocks API cannot expose, especially draft HTML, old problem XML and course
assets.  When an internal API differs between Open edX releases, this connector
returns the best available data instead of failing the whole sync.
"""

from __future__ import annotations

import base64
import json
import mimetypes
import re
from html import unescape
from typing import Any
from urllib.parse import quote, unquote, urljoin
from urllib.request import Request, urlopen

from django.http import JsonResponse, HttpResponseBadRequest
from django.views.decorators.csrf import csrf_exempt


# Starter in-memory stores for local connector testing only.  Library creation and
# OLX import are still stubs until the production CMS library API/taxonomy wiring
# is completed.  The Studio content endpoint below is a real best-effort reader.
_LIBRARIES_BY_KEY: dict[str, dict] = {}
_PROBLEMS_BY_KEY: dict[str, dict] = {}


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


@csrf_exempt
def publish_problem(request, course_id: str):
    """Stable publish endpoint expected by AI Server.

    Production implementation should create a problem XBlock/OLX unit through
    Studio/modulestore.  The starter returns a deterministic response so the AI
    Server publish flow can be tested without mutating a real course.
    """
    if request.method != 'POST':
        return HttpResponseBadRequest('POST required')
    try:
        payload = json.loads(request.body.decode('utf-8'))
    except Exception:
        return HttpResponseBadRequest('Invalid JSON')
    display_name = payload.get('display_name') or 'AI Learning Check'
    return _json_response({
        'course_id': course_id,
        'display_name': display_name,
        'parent_block_id': payload.get('parent_block_id'),
        'openedx_block_id': f'block-v1:{course_id}+type@problem+block@ai-{abs(hash(display_name)) % 100000}',
        'status': 'accepted_by_connector_stub',
    })


@csrf_exempt
def ensure_chapter_library(request, course_id: str):
    """Find existing library by key, create it only when missing."""
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

    existing = _LIBRARIES_BY_KEY.get(library_key)
    if existing:
        existing['tag_names'] = sorted(set([*(existing.get('tag_names') or []), *tag_names]))
        existing['metadata'] = {**(existing.get('metadata') or {}), **metadata}
        existing['status'] = 'library_exists'
        return _json_response({**existing, 'created': False})

    library = {
        'course_id': course_id,
        'chapter_node_id': chapter_node_id,
        'display_name': display_name,
        'library_key': library_key,
        'openedx_library_id': f'lib-v1:{abs(hash(library_key)) % 100000}',
        'tag_names': tag_names,
        'metadata': metadata,
        'status': 'library_created',
    }
    _LIBRARIES_BY_KEY[library_key] = library
    return _json_response({**library, 'created': True})


@csrf_exempt
def import_problem_to_library(request, library_key: str):
    """Import OLX problem into a Chapter/Module library with filter tags."""
    if request.method != 'POST':
        return HttpResponseBadRequest('POST required')
    try:
        payload = json.loads(request.body.decode('utf-8'))
    except Exception:
        return HttpResponseBadRequest('Invalid JSON')

    metadata = payload.get('metadata') or {}
    display_name = payload.get('display_name') or 'AI Learning Check'
    tag_names = payload.get('tag_names') or metadata.get('tag_names') or metadata.get('tags') or []
    problem_key = f'{library_key}:{metadata.get("question_id") or display_name}'
    existing = _PROBLEMS_BY_KEY.get(problem_key)
    if existing:
        return _json_response({**existing, 'created': False, 'status': 'problem_already_imported'})

    problem = {
        'library_key': library_key,
        'display_name': display_name,
        'source_node_id': metadata.get('source_node_id'),
        'chapter_node_id': metadata.get('chapter_node_id'),
        'difficulty': metadata.get('difficulty'),
        'tag_names': tag_names,
        'metadata': metadata,
        'openedx_library_problem_id': f'lib-problem-v1:{abs(hash(problem_key)) % 100000}',
        'openedx_block_id': f'bank-item-v1:{abs(hash(display_name)) % 100000}',
        'status': 'imported_by_connector_stub',
    }
    _PROBLEMS_BY_KEY[problem_key] = problem
    return _json_response({**problem, 'created': True})
