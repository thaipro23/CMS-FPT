"""Student Insight endpoints for AI Server <-> Open edX academic analytics.

This module is intentionally separate from `views.py` so course content/publish
logic does not share a 5k-line view file with class progress, enrollment and
user-resolution APIs.
"""

from __future__ import annotations

from datetime import datetime
from typing import Any

from django.http import JsonResponse
from django.views.decorators.csrf import csrf_exempt

from .auth import (
    _batch_too_large_response,
    _json_response,
    _read_json_body,
    _require_student_insight_hmac,
    _setting_or_env,
)
from .runtime import _load_openedx_modules


def _normalize_username_input(value: Any) -> str:
    return str(value or '').strip().lower()


def _student_payload_username(item: Any) -> str:
    if isinstance(item, dict):
        return _normalize_username_input(
            item.get('username')
            or item.get('ap_username')
            or item.get('apUsername')
            or item.get('openedx_username')
            or item.get('student_username')
            or item.get('teacher')
            or item.get('teacher_username')
        )
    return _normalize_username_input(item)


def _student_payload_code(item: Any) -> str:
    if not isinstance(item, dict):
        return ''
    return str(item.get('student_code') or item.get('studentCode') or item.get('ap_student_code') or '').strip()


def _clean_user_token(value: Any) -> str:
    return _normalize_username_input(value)


def _payload_person_type(item: Any) -> str:
    if not isinstance(item, dict):
        return 'student'
    raw = str(item.get('person_type') or item.get('entity_type') or item.get('role') or item.get('kind') or '').strip().lower()
    if raw in {'teacher', 'staff', 'instructor', 'giang_vien', 'lecturer'}:
        return 'teacher'
    return 'student'


def _payload_email(item: Any, username: str, person_type: str) -> str:
    if isinstance(item, dict):
        raw = str(item.get('email') or item.get('mail') or item.get('openedx_email') or '').strip().lower()
        if raw and '@' in raw:
            return raw[:254]
    # AP often gives only `teacher: ngocnb61`; use FPT mail by convention.
    if username:
        return f'{username}@fpt.edu.vn'[:254]
    return ''


def _split_display_name(item: Any, username: str, person_type: str) -> tuple[str, str, str]:
    if isinstance(item, dict):
        first = str(item.get('first_name') or item.get('given_name') or '').strip()
        last = str(item.get('last_name') or item.get('family_name') or '').strip()
        full = str(item.get('full_name') or item.get('name') or item.get('display_name') or '').strip()
    else:
        first = last = full = ''
    if person_type == 'teacher':
        # Requirement: teacher only syncs as `teacher: ngocnb61`, so use it for first/last/name.
        token = username or full
        return token[:150], token[:150], token[:255]
    if first or last:
        return (first or username)[:150], (last or username)[:150], (full or f'{last} {first}'.strip() or username)[:255]
    if full:
        parts = full.split()
        if len(parts) == 1:
            return parts[0][:150], parts[0][:150], full[:255]
        # Vietnamese display: keep family/middle name in last_name, given name in first_name.
        return parts[-1][:150], ' '.join(parts[:-1])[:150], full[:255]
    token = username or ''
    return token[:150], token[:150], token[:255]


def _ensure_cms_user(item: Any, username: str, person_type: str):
    """Create a CMS/Open edX auth user when the AP record is authoritative.

    The user is created inactive-password-wise (unusable password) but active in
    Django, so SSO/password-reset can manage the real authentication lifecycle.
    """
    from django.contrib.auth import get_user_model  # type: ignore
    from django.db import IntegrityError, transaction  # type: ignore

    User = get_user_model()
    username = _clean_user_token(username)
    if not username:
        return None, False, 'missing_username'
    first_name, last_name, full_name = _split_display_name(item, username, person_type)
    email = _payload_email(item, username, person_type)
    try:
        with transaction.atomic():
            user = User(username=username, email=email, first_name=first_name, last_name=last_name, is_active=True)
            if hasattr(user, 'set_unusable_password'):
                user.set_unusable_password()
            user.save()
            created = True
    except IntegrityError:
        try:
            user = User.objects.get(username__iexact=username)
            created = False
        except Exception:
            raise
    # Keep profile best-effort; Open edX releases differ here.
    try:
        from student.models import UserProfile  # type: ignore
        profile, _profile_created = UserProfile.objects.get_or_create(user=user, defaults={'name': full_name or username})
        if full_name and not getattr(profile, 'name', ''):
            profile.name = full_name
            profile.save()
    except Exception:
        pass
    return user, created, 'created' if created else 'exists'


def student_insight_resolve_users(request):
    """Resolve AP usernames against Open edX/CMS users.

    URL contract:
      POST /api/ai-student-insight/v1/users/resolve

    Input supports both compact and rich payloads:
      {"usernames": ["he173548"]}
      {"students": [{"username": "he173548", "student_code": "HE173548"}]}

    Matching is intentionally exact by username only.  Name/email fuzzy matching is
    avoided because student identity errors are more harmful than a visible
    "Chưa có trên CMS" state.
    """
    if request.method != 'POST':
        return _json_response({'ok': False, 'message': 'Method not allowed'}, status=405)
    auth_error = _require_student_insight_hmac(request)
    if auth_error:
        return auth_error
    data, error = _read_json_body(request)
    if error:
        return error
    data = data or {}
    raw_students = data.get('students')
    if raw_students is None:
        raw_students = data.get('usernames') or data.get('users') or []
    if not isinstance(raw_students, list):
        return _json_response({'ok': False, 'message': 'students/usernames phải là danh sách'}, status=400)
    max_batch = int(_setting_or_env('AI_STUDENT_INSIGHT_MAX_BATCH_SIZE', '5000') or '5000')
    max_batch = max(1, min(max_batch, 10000))
    raw_students = raw_students[:max_batch]

    create_missing = bool(data.get('create_missing') is True or data.get('create_missing_users') is True)
    requested: list[dict[str, Any]] = []
    usernames: list[str] = []
    for item in raw_students:
        username = _student_payload_username(item)
        student_code = _student_payload_code(item)
        person_type = _payload_person_type(item)
        if not username and not student_code:
            continue
        requested.append({'raw': item, 'username': username, 'student_code': student_code, 'person_type': person_type})
        if username:
            usernames.append(username)
    unique_usernames = sorted(set(usernames))

    found_by_username: dict[str, Any] = {}
    if unique_usernames:
        try:
            from django.contrib.auth import get_user_model  # type: ignore
            from django.db.models.functions import Lower  # type: ignore

            User = get_user_model()
            users = User.objects.annotate(username_l=Lower('username')).filter(username_l__in=unique_usernames)
            for user in users:
                key = _normalize_username_input(getattr(user, 'username', ''))
                if key:
                    found_by_username[key] = user
        except Exception as exc:
            return _json_response({'ok': False, 'message': f'Không truy vấn được auth_user CMS/Open edX: {exc}'}, status=500)

    results: list[dict[str, Any]] = []
    found: list[dict[str, Any]] = []
    missing: list[str] = []
    for item in requested:
        username = item['username']
        student_code = item['student_code']
        user = found_by_username.get(username) if username else None
        if user is not None:
            full_name = user.get_full_name() if hasattr(user, 'get_full_name') else ''
            row = {
                'student_code': student_code or None,
                'ap_username': username,
                'username': username,
                'exists': True,
                'match_status': 'matched' if getattr(user, 'is_active', True) else 'inactive',
                'match_method': 'exact_ap_username',
                'openedx_user_id': str(getattr(user, 'id', '') or getattr(user, 'pk', '')),
                'openedx_username': getattr(user, 'username', None),
                'openedx_email': getattr(user, 'email', None),
                'openedx_is_active': bool(getattr(user, 'is_active', True)),
                'is_active': bool(getattr(user, 'is_active', True)),
                'full_name': full_name,
                'note': 'Khớp chính xác AP username = CMS/Open edX username',
            }
            found.append(row)
        else:
            created_user = None
            created = False
            create_message = 'Không tìm thấy user CMS/Open edX theo AP username'
            if create_missing and username:
                try:
                    created_user, created, _create_status = _ensure_cms_user(item.get('raw'), username, item.get('person_type') or 'student')
                except Exception as exc:
                    created_user = None
                    create_message = 'Không tạo được user CMS/Open edX'
            if created_user is not None:
                full_name = created_user.get_full_name() if hasattr(created_user, 'get_full_name') else ''
                row = {
                    'student_code': student_code or None,
                    'ap_username': username,
                    'username': username,
                    'person_type': item.get('person_type') or 'student',
                    'exists': True,
                    'created': created,
                    'match_status': 'matched',
                    'match_method': 'created_from_ap' if created else 'exact_ap_username',
                    'openedx_user_id': str(getattr(created_user, 'id', '') or getattr(created_user, 'pk', '')),
                    'openedx_username': getattr(created_user, 'username', None),
                    'openedx_email': getattr(created_user, 'email', None),
                    'openedx_is_active': bool(getattr(created_user, 'is_active', True)),
                    'is_active': bool(getattr(created_user, 'is_active', True)),
                    'full_name': full_name,
                    'note': 'Đã tạo mới user CMS/Open edX từ dữ liệu AP' if created else 'Khớp chính xác AP username = CMS/Open edX username',
                }
                found.append(row)
            else:
                row = {
                    'student_code': student_code or None,
                    'ap_username': username,
                    'username': username,
                    'person_type': item.get('person_type') or 'student',
                    'exists': False,
                    'created': False,
                    'match_status': 'missing',
                    'match_method': 'not_found',
                    'openedx_user_id': None,
                    'openedx_username': None,
                    'openedx_email': None,
                    'openedx_is_active': None,
                    'is_active': None,
                    'note': create_message,
                }
                if username:
                    missing.append(username)
        results.append(row)
    return _json_response({
        'ok': True,
        'results': results,
        'found': found,
        'missing': missing,
        'total': len(results),
        'found_count': len(found),
        'missing_count': len(missing),
    })


def _course_item(course: Any) -> dict[str, Any]:
    course_id = str(getattr(course, 'id', '') or getattr(course, 'course_id', '') or '')
    return {
        'course_id': course_id,
        'id': course_id,
        'display_name': str(getattr(course, 'display_name', '') or getattr(course, 'name', '') or ''),
        'name': str(getattr(course, 'display_name', '') or getattr(course, 'name', '') or ''),
        'org': str(getattr(course, 'org', '') or ''),
        'number': str(getattr(course, 'number', '') or ''),
        'run': str(getattr(course, 'run', '') or ''),
    }


def student_insight_course_search(request):
    """Search courses for AI Server academic subject-course auto mapping."""
    if request.method != 'POST':
        return _json_response({'ok': False, 'message': 'Method not allowed'}, status=405)
    auth_error = _require_student_insight_hmac(request)
    if auth_error:
        return auth_error
    data, error = _read_json_body(request)
    if error:
        return error
    data = data or {}
    query = str(data.get('query') or data.get('search') or '').strip()
    exact_course_id = str(data.get('exact_course_id') or data.get('course_id') or '').strip()
    limit = max(1, min(int(data.get('limit') or 20), 100))
    needle = (exact_course_id or query).lower()
    if not needle:
        return _json_response({'ok': True, 'results': [], 'total': 0})
    try:
        from openedx.core.djangoapps.content.course_overviews.models import CourseOverview  # type: ignore
    except Exception as exc:
        return _json_response({'ok': False, 'message': f'CourseOverview không khả dụng trong app này: {exc}', 'results': []}, status=501)

    candidates: list[dict[str, Any]] = []
    try:
        from django.db.models import Q  # type: ignore

        if exact_course_id:
            # Exact lookup first: this is the path used by auto-map and avoids
            # scanning all courses in deployments with 1,500+ course runs.
            exact_qs = CourseOverview.objects.filter(id=exact_course_id).order_by('id')[:limit]
            for course in exact_qs:
                candidates.append(_course_item(course))
        if not candidates and query:
            q = Q(id__icontains=query) | Q(display_name__icontains=query)
            # Some older Open edX CourseOverview models may not expose org/number/run
            # as concrete fields. Add them only if the model has them.
            field_names = {field.name for field in CourseOverview._meta.fields}
            if 'org' in field_names:
                q |= Q(org__icontains=query)
            if 'number' in field_names:
                q |= Q(number__icontains=query)
            if 'run' in field_names:
                q |= Q(run__icontains=query)
            qs = CourseOverview.objects.filter(q).order_by('id')[:limit]
            for course in qs:
                row = _course_item(course)
                haystack = ' '.join([row.get('course_id') or '', row.get('display_name') or '', row.get('org') or '', row.get('number') or '', row.get('run') or '']).lower()
                if query.lower() in haystack:
                    candidates.append(row)
    except Exception as exc:
        return _json_response({'ok': False, 'message': f'Không tìm kiếm được course CMS/Open edX: {exc}', 'results': []}, status=500)
    return _json_response({'ok': True, 'results': candidates[:limit], 'courses': candidates[:limit], 'total': len(candidates[:limit])})


def _student_insight_requested_students(data: dict[str, Any]) -> list[dict[str, Any]]:
    raw_students = data.get('students')
    if raw_students is None:
        raw_students = data.get('usernames') or data.get('users') or []
    if not isinstance(raw_students, list):
        return []
    max_batch = int(_setting_or_env('AI_STUDENT_INSIGHT_MAX_BATCH_SIZE', '5000') or '5000')
    max_batch = max(1, min(max_batch, 10000))
    requested: list[dict[str, Any]] = []
    for item in raw_students[:max_batch]:
        username = _student_payload_username(item)
        student_code = _student_payload_code(item)
        openedx_user_id = ''
        if isinstance(item, dict):
            openedx_user_id = str(item.get('openedx_user_id') or item.get('user_id') or '').strip()
        if username or student_code or openedx_user_id:
            raw = item if isinstance(item, dict) else {}
            person_type = _payload_person_type(item)
            requested.append({
                'raw': item,
                'username': username,
                'student_code': student_code,
                'openedx_user_id': openedx_user_id,
                'person_type': person_type,
                'role': raw.get('role') or ('teacher' if person_type == 'teacher' else 'student'),
                'email': raw.get('email') or raw.get('mail'),
                'full_name': raw.get('full_name') or raw.get('name') or raw.get('display_name'),
                'first_name': raw.get('first_name'),
                'last_name': raw.get('last_name'),
                'create_missing': raw.get('create_missing') is True or raw.get('create_missing_users') is True,
            })
    return requested


def _student_insight_user_map(requested: list[dict[str, Any]]) -> dict[str, Any]:
    usernames = sorted({item['username'] for item in requested if item.get('username')})
    ids = sorted({item['openedx_user_id'] for item in requested if item.get('openedx_user_id')})
    found: dict[str, Any] = {}
    if not usernames and not ids:
        return found
    try:
        from django.contrib.auth import get_user_model  # type: ignore
        from django.db.models import Q  # type: ignore
        from django.db.models.functions import Lower  # type: ignore
        User = get_user_model()
        q = Q()
        has_filter = False
        if usernames:
            q |= Q(username_l__in=usernames)
            has_filter = True
        numeric_ids = [int(value) for value in ids if str(value).isdigit()]
        if numeric_ids:
            q |= Q(id__in=numeric_ids)
            has_filter = True
        if not has_filter:
            return {}
        users = User.objects.annotate(username_l=Lower('username')).filter(q)
        for user in users:
            username = _normalize_username_input(getattr(user, 'username', ''))
            if username:
                found[username] = user
            found[f'id:{getattr(user, "id", "")}'] = user
    except Exception:
        return {}
    return found


def _course_key_from_string(course_id: str):
    try:
        from opaque_keys.edx.keys import CourseKey  # type: ignore
        return CourseKey.from_string(str(course_id or '').strip())
    except Exception:
        return None


def _enrollment_snapshot(course_key: Any, users: list[Any]) -> dict[int, dict[str, Any]]:
    result: dict[int, dict[str, Any]] = {}
    if not course_key or not users:
        return result
    try:
        from student.models import CourseEnrollment  # type: ignore
        user_ids = [getattr(user, 'id', None) for user in users if getattr(user, 'id', None) is not None]
        enrollments = CourseEnrollment.objects.filter(course_id=course_key, user_id__in=user_ids).select_related('user')
        for enrollment in enrollments:
            uid = int(getattr(enrollment, 'user_id', 0) or 0)
            active = bool(getattr(enrollment, 'is_active', False))
            result[uid] = {
                'status': 'enrolled' if active else 'inactive',
                'is_enrolled': active,
                'mode': str(getattr(enrollment, 'mode', '') or ''),
                'created': getattr(enrollment, 'created', None).isoformat() if getattr(enrollment, 'created', None) else None,
            }
    except Exception:
        return result
    return result


def _persistent_grade_snapshot(course_key: Any, users: list[Any]) -> dict[int, dict[str, Any]]:
    result: dict[int, dict[str, Any]] = {}
    if not course_key or not users:
        return result
    try:
        from lms.djangoapps.grades.models import PersistentCourseGrade  # type: ignore
        user_ids = [getattr(user, 'id', None) for user in users if getattr(user, 'id', None) is not None]
        rows = PersistentCourseGrade.objects.filter(course_id=course_key, user_id__in=user_ids)
        for row in rows:
            uid = int(getattr(row, 'user_id', 0) or 0)
            percent = getattr(row, 'percent_grade', None)
            passed_timestamp = getattr(row, 'passed_timestamp', None)
            result[uid] = {
                'percent': float(percent) if percent is not None else None,
                'letter_grade': getattr(row, 'letter_grade', None),
                'passed': bool(passed_timestamp) if passed_timestamp is not None else None,
                'passed_timestamp': passed_timestamp.isoformat() if passed_timestamp else None,
                'modified': getattr(row, 'modified', None).isoformat() if getattr(row, 'modified', None) else None,
            }
    except Exception:
        # Some Open edX installs keep grades computable but not persisted yet.
        # Do not fail the whole class sync: user existence/enrollment remains useful.
        return result
    return result


def _component_grade_snapshot(course_key: Any, users: list[Any]) -> dict[int, list[dict[str, Any]]]:
    """Best-effort subsection/component grade breakdown.

    Open edX keeps course-level grades in PersistentCourseGrade and, on most
    modern installs, subsection grades in PersistentSubsectionGrade. Field names
    vary by release, so this function only reads with getattr and never fails the
    whole class analytics response.
    """
    result: dict[int, list[dict[str, Any]]] = {}
    if not course_key or not users:
        return result
    try:
        from lms.djangoapps.grades.models import PersistentSubsectionGrade  # type: ignore
        user_ids = [getattr(user, 'id', None) for user in users if getattr(user, 'id', None) is not None]
        rows = PersistentSubsectionGrade.objects.filter(course_id=course_key, user_id__in=user_ids)
    except Exception:
        return result

    display_cache: dict[str, str] = {}
    def _display_name_for_usage(usage_key: Any) -> str:
        raw = _safe_str(usage_key)
        if not raw:
            return 'Điểm thành phần'
        if raw in display_cache:
            return display_cache[raw]
        display = raw
        try:
            CourseKey, modulestore = _load_openedx_modules()
            block = _get_item_best_effort(modulestore(), usage_key)
            block_name = getattr(block, 'display_name', None) or getattr(block, 'display_name_with_default', None)
            if callable(block_name):
                block_name = block_name()
            if block_name:
                display = _safe_str(block_name)
        except Exception:
            pass
        display_cache[raw] = display
        return display

    for row in rows:
        try:
            uid = int(getattr(row, 'user_id', 0) or 0)
            usage_key = getattr(row, 'usage_key', None) or getattr(row, 'subsection_usage_key', None) or getattr(row, 'block_key', None)
            earned_graded = getattr(row, 'earned_graded', None)
            possible_graded = getattr(row, 'possible_graded', None)
            earned_all = getattr(row, 'earned_all', None)
            possible_all = getattr(row, 'possible_all', None)
            earned = earned_graded if earned_graded is not None else earned_all
            possible = possible_graded if possible_graded is not None else possible_all
            percent = None
            try:
                if possible is not None and float(possible) > 0 and earned is not None:
                    percent = float(earned) / float(possible) * 100.0
            except Exception:
                percent = None
            result.setdefault(uid, []).append({
                'key': _safe_str(usage_key),
                'usage_key': _safe_str(usage_key),
                'name': _display_name_for_usage(usage_key),
                'category': 'subsection',
                'earned': float(earned) if earned is not None else None,
                'possible': float(possible) if possible is not None else None,
                'percent': round(percent, 2) if percent is not None else None,
                'source': 'PersistentSubsectionGrade',
            })
        except Exception:
            continue
    for items in result.values():
        items.sort(key=lambda item: _safe_str(item.get('name') or item.get('key')))
    # Ulmo deployments do not always have PersistentSubsectionGrade populated.
    # When subsection rows are missing for a learner, fall back to StudentModule
    # problem scores grouped by the nearest sequential/subsection. This keeps
    # Student Progress useful without fabricating a score when Open edX really
    # has no saved grade state.
    try:
        fallback = _student_module_problem_grade_snapshot(course_key, users)
        for uid, items in fallback.items():
            if not result.get(uid):
                result[uid] = items
    except Exception:
        pass
    return result


def _subsection_problem_index(course_key: Any) -> dict[str, dict[str, Any]]:
    """Map problem usage keys to their nearest subsection/sequential."""
    index: dict[str, dict[str, Any]] = {}
    if not course_key:
        return index
    try:
        CourseKey, modulestore = _load_openedx_modules()
        store = modulestore()
        course = store.get_course(course_key)
    except Exception:
        return index
    stack: list[tuple[Any, dict[str, Any] | None]] = [(child, None) for child in list(getattr(course, 'children', []) or [])]
    visited: set[str] = set()
    while stack:
        key, current_subsection = stack.pop()
        raw_key = _safe_str(key)
        if not raw_key or raw_key in visited:
            continue
        visited.add(raw_key)
        block = _get_item_best_effort(store, key)
        if block is None:
            continue
        block_type = _block_type(block)
        next_subsection = current_subsection
        if block_type == 'sequential':
            next_subsection = {
                'key': raw_key,
                'name': _display_name(block) or raw_key,
                'category': 'subsection',
            }
        if block_type == 'problem':
            if next_subsection:
                index[raw_key] = dict(next_subsection)
            else:
                index[raw_key] = {'key': raw_key, 'name': _display_name(block) or raw_key, 'category': 'problem'}
        children = _children_locations(block)
        for child in reversed(children):
            stack.append((child, next_subsection))
    return index


def _student_module_problem_grade_snapshot(course_key: Any, users: list[Any]) -> dict[int, list[dict[str, Any]]]:
    """Fallback component grades from StudentModule problem grade/max_grade."""
    result: dict[int, list[dict[str, Any]]] = {}
    if not course_key or not users:
        return result
    try:
        from courseware.models import StudentModule  # type: ignore
        user_ids = [getattr(user, 'id', None) for user in users if getattr(user, 'id', None) is not None]
        rows = StudentModule.objects.filter(course_id=course_key, student_id__in=user_ids, module_type='problem')
    except Exception:
        return result
    problem_index = _subsection_problem_index(course_key)
    buckets: dict[tuple[int, str], dict[str, Any]] = {}
    for row in rows:
        try:
            uid = int(getattr(row, 'student_id', 0) or 0)
            problem_key = _safe_str(getattr(row, 'module_state_key', None) or getattr(row, 'usage_key', None) or '')
            grade = getattr(row, 'grade', None)
            max_grade = getattr(row, 'max_grade', None)
            if uid <= 0 or grade is None or max_grade is None:
                continue
            possible = float(max_grade)
            if possible <= 0:
                continue
            earned = float(grade)
            group = problem_index.get(problem_key) or {'key': problem_key or 'problem_grade', 'name': problem_key or 'Problem grades', 'category': 'problem'}
            bucket_key = (uid, _safe_str(group.get('key') or group.get('name')))
            bucket = buckets.setdefault(bucket_key, {
                'key': _safe_str(group.get('key') or group.get('name')),
                'usage_key': _safe_str(group.get('key') or group.get('name')),
                'name': _safe_str(group.get('name') or group.get('key') or 'Điểm thành phần'),
                'category': group.get('category') or 'subsection',
                'earned': 0.0,
                'possible': 0.0,
                'problem_count': 0,
                'source': 'StudentModule.problem_grade',
            })
            bucket['earned'] += earned
            bucket['possible'] += possible
            bucket['problem_count'] += 1
        except Exception:
            continue
    for (uid, _key), bucket in buckets.items():
        possible = float(bucket.get('possible') or 0.0)
        earned = float(bucket.get('earned') or 0.0)
        percent = round((earned / possible) * 100.0, 2) if possible > 0 else None
        result.setdefault(uid, []).append({
            'key': bucket['key'],
            'usage_key': bucket['usage_key'],
            'name': bucket['name'],
            'category': bucket['category'],
            'earned': round(earned, 2),
            'possible': round(possible, 2),
            'percent': percent,
            'problem_count': int(bucket.get('problem_count') or 0),
            'source': bucket['source'],
        })
    for items in result.values():
        items.sort(key=lambda item: _safe_str(item.get('name') or item.get('key')))
    return result


def _completion_snapshot(course_key: Any, users: list[Any]) -> tuple[dict[int, dict[str, Any]], int | None]:
    result: dict[int, dict[str, Any]] = {}
    total_blocks: int | None = None
    if not course_key or not users:
        return result, total_blocks
    try:
        from completion.models import BlockCompletion  # type: ignore
        from django.db.models import Count, Max  # type: ignore
        user_ids = [getattr(user, 'id', None) for user in users if getattr(user, 'id', None) is not None]
        rows = BlockCompletion.objects.filter(context_key=course_key, user_id__in=user_ids).values('user_id').annotate(completed=Count('id'), last_activity=Max('modified'))
        for row in rows:
            uid = int(row.get('user_id') or 0)
            completed = int(row.get('completed') or 0)
            last_activity = row.get('last_activity')
            result[uid] = {
                'completed_blocks': completed,
                'last_activity_at': last_activity.isoformat() if last_activity else None,
            }
    except Exception:
        return result, total_blocks
    try:
        CourseKey, modulestore = _load_openedx_modules()
        store = modulestore()
        course = store.get_course(course_key)
        visited: set[str] = set()
        stack = list(getattr(course, 'children', []) or [])
        count = 0
        while stack:
            key = stack.pop()
            if str(key) in visited:
                continue
            visited.add(str(key))
            block = _get_item_best_effort(store, key)
            if block is None:
                continue
            block_type = _block_type(block)
            children = _children_locations(block)
            if children:
                stack.extend(children)
            if block_type not in {'course', 'chapter', 'sequential', 'vertical'}:
                count += 1
        total_blocks = count or None
    except Exception:
        total_blocks = None
    if total_blocks:
        for item in result.values():
            completed = item.get('completed_blocks') or 0
            item['total_blocks'] = total_blocks
            item['percent'] = float(completed) / float(total_blocks) if total_blocks else None
    return result, total_blocks


def _student_learning_results(course_id: str, requested: list[dict[str, Any]]) -> list[dict[str, Any]]:
    course_key = _course_key_from_string(course_id)
    found_by_key = _student_insight_user_map(requested)
    users = []
    for item in requested:
        user = None
        if item.get('username'):
            user = found_by_key.get(item['username'])
        if user is None and item.get('openedx_user_id'):
            user = found_by_key.get(f"id:{item['openedx_user_id']}")
        if user is not None:
            users.append(user)
    enrollment = _enrollment_snapshot(course_key, users)
    grades = _persistent_grade_snapshot(course_key, users)
    component_grades = _component_grade_snapshot(course_key, users)
    completion, total_blocks = _completion_snapshot(course_key, users)
    results: list[dict[str, Any]] = []
    for item in requested:
        username = item.get('username') or ''
        user = found_by_key.get(username) if username else None
        if user is None and item.get('openedx_user_id'):
            user = found_by_key.get(f"id:{item['openedx_user_id']}")
        uid = int(getattr(user, 'id', 0) or 0) if user is not None else 0
        enroll = enrollment.get(uid) or {'status': 'not_enrolled' if user is not None else 'missing_user', 'is_enrolled': False}
        grade = grades.get(uid) or {}
        progress = completion.get(uid) or {}
        components = component_grades.get(uid) or []
        results.append({
            'student_code': item.get('student_code') or None,
            'ap_username': username,
            'username': _normalize_username_input(getattr(user, 'username', username)) if user is not None else username,
            'openedx_username': getattr(user, 'username', None) if user is not None else None,
            'openedx_user_id': str(getattr(user, 'id', '') or '') if user is not None else None,
            'exists': user is not None,
            'enrollment_status': enroll.get('status'),
            'enrollment_mode': enroll.get('mode'),
            'progress_percent': progress.get('percent'),
            'grade_percent': grade.get('percent'),
            'passed': grade.get('passed'),
            'completed_blocks': progress.get('completed_blocks'),
            'total_blocks': progress.get('total_blocks') or total_blocks,
            'last_activity_at': progress.get('last_activity_at') or grade.get('modified'),
            'enrollment': enroll,
            'progress': progress,
            'grade': {**grade, 'components': components},
            'component_scores': components,
            'component_grades': components,
        })
    return results


def student_insight_class_analytics(request):
    """Return enrollment/progress/grade snapshots for a class in one call.

    URL contract:
      POST /api/ai-student-insight/v1/class-analytics
      {course_id, students:[{username, student_code, openedx_user_id?}]}
    """
    if request.method != 'POST':
        return _json_response({'ok': False, 'message': 'Method not allowed'}, status=405)
    auth_error = _require_student_insight_hmac(request)
    if auth_error:
        return auth_error
    data, error = _read_json_body(request)
    if error:
        return error
    data = data or {}
    course_id = str(data.get('course_id') or '').strip()
    if not course_id:
        return _json_response({'ok': False, 'message': 'Thiếu course_id'}, status=400)
    requested = _student_insight_requested_students(data)
    batch_error = _batch_too_large_response(len(requested))
    if batch_error:
        return batch_error
    if not requested:
        return _json_response({'ok': True, 'course_id': course_id, 'results': [], 'total': 0})
    try:
        results = _student_learning_results(course_id, requested)
    except Exception:
        return _json_response({'ok': False, 'code': 'class_analytics_failed', 'message': 'Không lấy được dữ liệu học tập CMS/Open edX', 'results': []}, status=500)
    counts: dict[str, int] = {}
    for item in results:
        status = str(item.get('enrollment_status') or 'unknown')
        counts[status] = counts.get(status, 0) + 1
    return _json_response({'ok': True, 'course_id': course_id, 'total': len(results), 'counts': counts, 'results': results})


def _student_insight_enroll_results(course_id: str, requested: list[dict[str, Any]], *, mode: str = 'audit', force: bool = False, create_missing: bool = False) -> list[dict[str, Any]]:
    """Enroll AP students and add AP teachers to the mapped Open edX course.

    Students are enrolled as learners. Teachers are created if needed and granted
    Course Staff role. User creation is exact by AP username only.
    """
    course_key = _course_key_from_string(course_id)
    if course_key is None:
        return [{
            'student_code': item.get('student_code') or None,
            'ap_username': item.get('username') or '',
            'username': item.get('username') or '',
            'exists': False,
            'status': 'failed',
            'enrollment_status': 'failed',
            'is_enrolled': False,
            'message': 'Course ID không hợp lệ',
        } for item in requested]
    found_by_key = _student_insight_user_map(requested)
    clean_mode = str(mode or _setting_or_env('AI_STUDENT_INSIGHT_DEFAULT_ENROLLMENT_MODE', 'audit') or 'audit').strip() or 'audit'
    results: list[dict[str, Any]] = []
    try:
        from student.models import CourseEnrollment  # type: ignore
    except Exception as exc:
        return [{
            'student_code': item.get('student_code') or None,
            'ap_username': item.get('username') or '',
            'username': item.get('username') or '',
            'exists': False,
            'status': 'failed',
            'enrollment_status': 'failed',
            'is_enrolled': False,
            'message': 'Không import được CourseEnrollment',
        } for item in requested]

    for item in requested:
        username = item.get('username') or ''
        person_type = str(item.get('person_type') or item.get('role') or 'student').strip().lower()
        is_teacher = person_type in {'teacher', 'staff', 'instructor'}
        user = found_by_key.get(username) if username else None
        if user is None and item.get('openedx_user_id'):
            user = found_by_key.get(f"id:{item['openedx_user_id']}")
        created_user = False
        if user is None and (create_missing or item.get('create_missing')) and username:
            try:
                user, created_user, _create_status = _ensure_cms_user(item.get('raw') if item.get('raw') else item, username, 'teacher' if is_teacher else 'student')
            except Exception as exc:
                results.append({
                    'student_code': item.get('student_code') or None,
                    'ap_username': username,
                    'username': username,
                    'person_type': 'teacher' if is_teacher else 'student',
                    'exists': False,
                    'created_user': False,
                    'status': 'create_user_failed',
                    'enrollment_status': 'failed',
                    'is_enrolled': False,
                    'message': 'Không tạo được user CMS/Open edX',
                })
                continue
        base = {
            'student_code': item.get('student_code') or None,
            'ap_username': username,
            'username': username,
            'person_type': 'teacher' if is_teacher else 'student',
            'openedx_username': getattr(user, 'username', None) if user is not None else None,
            'openedx_user_id': str(getattr(user, 'id', '') or '') if user is not None else None,
            'openedx_email': getattr(user, 'email', None) if user is not None else None,
            'exists': user is not None,
            'created_user': created_user,
            'enrollment_mode': clean_mode,
        }
        if user is None:
            results.append({**base, 'status': 'missing_user', 'enrollment_status': 'missing_user', 'is_enrolled': False, 'message': 'Không tìm thấy user CMS/Open edX'})
            continue
        if getattr(user, 'is_active', True) is False:
            results.append({**base, 'status': 'inactive_user', 'enrollment_status': 'inactive_user', 'is_enrolled': False, 'message': 'User CMS/Open edX inactive'})
            continue
        if is_teacher:
            try:
                from common.djangoapps.student.roles import CourseStaffRole  # type: ignore
                role = CourseStaffRole(course_key)
                already_staff = False
                try:
                    already_staff = bool(role.has_user(user))
                except Exception:
                    already_staff = False
                if not already_staff or force:
                    role.add_users(user)
                results.append({**base, 'status': 'already_course_staff' if already_staff else 'course_staff_added', 'enrollment_status': 'course_staff', 'is_enrolled': True, 'course_role': 'staff', 'message': 'Giảng viên đã được gán Course Staff'})
            except Exception as exc:
                results.append({**base, 'status': 'course_staff_failed', 'enrollment_status': 'failed', 'is_enrolled': False, 'message': 'Không gán được Course Staff cho giảng viên'})
            continue
        try:
            enrollment = CourseEnrollment.objects.filter(user=user, course_id=course_key).first()
            if enrollment and getattr(enrollment, 'is_active', False) and not force:
                results.append({**base, 'status': 'already_enrolled', 'enrollment_status': 'enrolled', 'is_enrolled': True, 'enrollment': {'status': 'enrolled', 'is_enrolled': True, 'mode': getattr(enrollment, 'mode', clean_mode)}})
                continue
            if enrollment:
                try:
                    enrollment.is_active = True
                    if clean_mode and getattr(enrollment, 'mode', None) != clean_mode:
                        enrollment.mode = clean_mode
                    enrollment.save()
                    status_value = 'reactivated'
                except Exception:
                    # Fall back to model helper below.
                    status_value = ''
            else:
                status_value = ''
            if not enrollment or not status_value:
                enroll_method = getattr(CourseEnrollment, 'enroll', None)
                if not enroll_method:
                    raise RuntimeError('CourseEnrollment.enroll không khả dụng')
                try:
                    enroll_method(user, course_key, mode=clean_mode, check_access=False)
                except TypeError:
                    try:
                        enroll_method(user, course_key, mode=clean_mode)
                    except TypeError:
                        enroll_method(user, course_key)
                status_value = 'created'
            enrollment = CourseEnrollment.objects.filter(user=user, course_id=course_key).first()
            mode_value = getattr(enrollment, 'mode', clean_mode) if enrollment else clean_mode
            active = bool(getattr(enrollment, 'is_active', True)) if enrollment else True
            results.append({**base, 'status': status_value, 'enrollment_status': 'enrolled' if active else 'inactive', 'is_enrolled': active, 'enrollment_mode': mode_value, 'enrollment': {'status': 'enrolled' if active else 'inactive', 'is_enrolled': active, 'mode': mode_value}})
        except Exception as exc:
            results.append({**base, 'status': 'failed', 'enrollment_status': 'failed', 'is_enrolled': False, 'message': 'Không enroll được sinh viên vào Course CMS'})
    return results


def student_insight_course_enrollment_enroll(request):
    """Enroll resolved CMS/Open edX users into a course.

    URL contract:
      POST /api/ai-student-insight/v1/course-enrollment/enroll
      {course_id, mode:'audit', force:false, students:[{username, openedx_user_id?}]}
    """
    if request.method != 'POST':
        return _json_response({'ok': False, 'message': 'Method not allowed'}, status=405)
    auth_error = _require_student_insight_hmac(request)
    if auth_error:
        return auth_error
    data, error = _read_json_body(request)
    if error:
        return error
    data = data or {}
    course_id = str(data.get('course_id') or '').strip()
    if not course_id:
        return _json_response({'ok': False, 'message': 'Thiếu course_id'}, status=400)
    requested = _student_insight_requested_students(data)
    raw_teachers = data.get('teachers') or []
    if isinstance(raw_teachers, list):
        for teacher in raw_teachers:
            if isinstance(teacher, str):
                teacher = {'username': teacher, 'person_type': 'teacher', 'role': 'teacher'}
            elif isinstance(teacher, dict):
                teacher = {**teacher, 'person_type': 'teacher', 'role': teacher.get('role') or 'teacher'}
            else:
                continue
            extra = _student_insight_requested_students({'students': [teacher]})
            requested.extend(extra)
    batch_error = _batch_too_large_response(len(requested))
    if batch_error:
        return batch_error
    if not requested:
        return _json_response({'ok': True, 'course_id': course_id, 'results': [], 'total': 0, 'counts': {}})
    mode = str(data.get('mode') or _setting_or_env('AI_STUDENT_INSIGHT_DEFAULT_ENROLLMENT_MODE', 'audit') or 'audit')
    force = bool(data.get('force') is True)
    create_missing = bool(data.get('create_missing') is True or data.get('create_missing_users') is True)
    results = _student_insight_enroll_results(course_id, requested, mode=mode, force=force, create_missing=create_missing)
    counts: dict[str, int] = {}
    for item in results:
        status = str(item.get('status') or item.get('enrollment_status') or 'unknown')
        counts[status] = counts.get(status, 0) + 1
    return _json_response({'ok': True, 'course_id': course_id, 'total': len(results), 'counts': counts, 'results': results})


def student_insight_course_enrollment_batch(request):
    if request.method != 'POST':
        return _json_response({'ok': False, 'message': 'Method not allowed'}, status=405)
    auth_error = _require_student_insight_hmac(request)
    if auth_error:
        return auth_error
    data, error = _read_json_body(request)
    if error:
        return error
    data = data or {}
    course_id = str(data.get('course_id') or '').strip()
    requested = _student_insight_requested_students(data)
    batch_error = _batch_too_large_response(len(requested))
    if batch_error:
        return batch_error
    results = _student_learning_results(course_id, requested) if course_id and requested else []
    return _json_response({'ok': True, 'course_id': course_id, 'results': [{'username': item.get('username'), 'student_code': item.get('student_code'), 'enrollment': item.get('enrollment'), 'enrollment_status': item.get('enrollment_status'), 'enrollment_mode': item.get('enrollment_mode')} for item in results], 'total': len(results)})


def student_insight_course_progress_batch(request):
    if request.method != 'POST':
        return _json_response({'ok': False, 'message': 'Method not allowed'}, status=405)
    auth_error = _require_student_insight_hmac(request)
    if auth_error:
        return auth_error
    data, error = _read_json_body(request)
    if error:
        return error
    data = data or {}
    course_id = str(data.get('course_id') or '').strip()
    requested = _student_insight_requested_students(data)
    batch_error = _batch_too_large_response(len(requested))
    if batch_error:
        return batch_error
    results = _student_learning_results(course_id, requested) if course_id and requested else []
    return _json_response({'ok': True, 'course_id': course_id, 'results': [{'username': item.get('username'), 'student_code': item.get('student_code'), 'progress': item.get('progress'), 'progress_percent': item.get('progress_percent'), 'completed_blocks': item.get('completed_blocks'), 'total_blocks': item.get('total_blocks')} for item in results], 'total': len(results)})


def student_insight_quiz_grades_batch(request):
    if request.method != 'POST':
        return _json_response({'ok': False, 'message': 'Method not allowed'}, status=405)
    auth_error = _require_student_insight_hmac(request)
    if auth_error:
        return auth_error
    data, error = _read_json_body(request)
    if error:
        return error
    data = data or {}
    course_id = str(data.get('course_id') or '').strip()
    requested = _student_insight_requested_students(data)
    batch_error = _batch_too_large_response(len(requested))
    if batch_error:
        return batch_error
    results = _student_learning_results(course_id, requested) if course_id and requested else []
    return _json_response({'ok': True, 'course_id': course_id, 'results': [{'username': item.get('username'), 'student_code': item.get('student_code'), 'grade': item.get('grade'), 'grade_percent': item.get('grade_percent'), 'passed': item.get('passed')} for item in results], 'total': len(results)})
