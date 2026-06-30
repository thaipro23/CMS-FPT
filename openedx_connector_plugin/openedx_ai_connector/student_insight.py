"""Academic runtime endpoints for AI Server <-> openedx_connector_plugin.

This module is intentionally separate from `views.py` so course content/publish
logic does not share a 5k-line view file with class progress, enrollment and
user-resolution APIs.
"""

from __future__ import annotations

from datetime import datetime
from zoneinfo import ZoneInfo
import importlib
import re
import secrets
from typing import Any

VN_TZ = ZoneInfo("Asia/Ho_Chi_Minh")

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

try:
    # Reuse the robust OLX helpers from the Studio connector module.  Earlier
    # student-insight builds referenced these names without importing them; the
    # broad exception guards then silently disabled Course completion / Detailed
    # grades fallback on UAT.
    from .studio import _safe_str, _block_type, _display_name, _get_item_best_effort, _children_locations  # type: ignore
except Exception:  # pragma: no cover - defensive fallback for stripped plugin installs
    def _safe_str(value: Any) -> str:
        if value is None or callable(value):
            return ''
        if isinstance(value, bytes):
            try:
                return value.decode('utf-8')
            except Exception:
                return value.decode('latin-1', errors='ignore')
        return str(value)

    def _block_type(block: Any) -> str:
        location = getattr(block, 'location', None)
        return _safe_str(getattr(location, 'block_type', None) or getattr(location, 'category', None) or getattr(block, 'category', None) or getattr(block, 'block_type', None) or 'unknown').lower()

    def _display_name(block: Any) -> str:
        return _safe_str(getattr(block, 'display_name', '') or getattr(block, 'name', '') or _block_type(block))

    def _get_item_best_effort(store: Any, usage_key: Any) -> Any | None:
        try:
            return store.get_item(usage_key)
        except Exception:
            try:
                from opaque_keys.edx.keys import UsageKey  # type: ignore
                return store.get_item(UsageKey.from_string(_safe_str(usage_key)))
            except Exception:
                return None

    def _children_locations(block: Any) -> list[Any]:
        try:
            return list(getattr(block, 'children', None) or [])
        except Exception:
            return []


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





def _import_attr_first(candidates: list[tuple[str, str]]) -> tuple[Any | None, str, str | None]:
    """Import the first available Open edX symbol across release-specific paths.

    Ulmo.3 uses the canonical common.djangoapps path for student models. Older
    integrations sometimes imported through student.models, which may not exist
    in newer Tutor/Open edX images. Connector endpoints must not silently assume
    one path.
    """
    errors: list[str] = []
    for module_path, attr_name in candidates:
        dotted = f'{module_path}.{attr_name}'
        try:
            module = importlib.import_module(module_path)
            return getattr(module, attr_name), dotted, None
        except Exception as exc:
            errors.append(f'{dotted}: {exc.__class__.__name__}: {exc}')
    return None, '', ' | '.join(errors[:6])


def _course_enrollment_model() -> tuple[Any | None, str, str | None]:
    return _import_attr_first([
        ('common.djangoapps.student.models', 'CourseEnrollment'),
        ('student.models', 'CourseEnrollment'),
    ])


def _course_staff_role_class() -> tuple[Any | None, str, str | None]:
    return _import_attr_first([
        ('common.djangoapps.student.roles', 'CourseStaffRole'),
        ('student.roles', 'CourseStaffRole'),
    ])


def _user_profile_model() -> tuple[Any | None, str, str | None]:
    return _import_attr_first([
        ('common.djangoapps.student.models', 'UserProfile'),
        ('student.models', 'UserProfile'),
    ])


def _ensure_user_profile(user: Any, *, full_name: str = '', username: str = '') -> dict[str, Any]:
    """Ensure Open edX has the mandatory student UserProfile row.

    Ulmo.3 enrollment code can fail with "User has no profile" even when
    auth_user exists. That happens when accounts are created programmatically
    without a matching UserProfile. The connector must create/repair this row
    before enrollment, and it must use the release-correct import path.
    """
    UserProfile, source, import_error = _user_profile_model()
    if UserProfile is None:
        return {
            'ok': False,
            'source': None,
            'created': False,
            'message': 'Không import được UserProfile trên Open edX LMS.',
            'import_error': import_error,
        }
    try:
        clean_name = (full_name or '').strip() or (username or getattr(user, 'username', '') or '').strip() or str(getattr(user, 'id', '') or '')
        profile, created = UserProfile.objects.get_or_create(user=user, defaults={'name': clean_name[:255]})
        changed = False
        if clean_name and not getattr(profile, 'name', ''):
            profile.name = clean_name[:255]
            changed = True
        if changed:
            profile.save()
        return {
            'ok': True,
            'source': source,
            'created': bool(created),
            'message': 'UserProfile đã tồn tại hoặc đã được tạo.',
        }
    except Exception as exc:
        return {
            'ok': False,
            'source': source,
            'created': False,
            'message': 'Không tạo/kiểm tra được UserProfile cho user CMS/Open edX.',
            'error': f'{exc.__class__.__name__}: {exc}',
        }


def _persistent_course_grade_model() -> tuple[Any | None, str, str | None]:
    return _import_attr_first([
        ('lms.djangoapps.grades.models', 'PersistentCourseGrade'),
        ('lms.djangoapps.grades.models.persistent_course_grade', 'PersistentCourseGrade'),
    ])


def _persistent_subsection_grade_model() -> tuple[Any | None, str, str | None]:
    return _import_attr_first([
        ('lms.djangoapps.grades.models', 'PersistentSubsectionGrade'),
        ('lms.djangoapps.grades.models.persistent_subsection_grade', 'PersistentSubsectionGrade'),
    ])


def _student_module_model() -> tuple[Any | None, str, str | None]:
    return _import_attr_first([
        ('courseware.models', 'StudentModule'),
        ('lms.djangoapps.courseware.models', 'StudentModule'),
    ])


def _connector_debug_errors_enabled() -> bool:
    return str(_setting_or_env('AI_CONNECTOR_DEBUG_ERRORS', 'false') or '').strip().lower() in {'1', 'true', 'yes', 'on'}


def _created_user_password_config() -> tuple[str, str, str]:
    """Return configured password policy for auto-created Open edX users.

    Default is intentionally `unusable`: the created user can be enrolled and
    tracked by Open edX, but cannot authenticate with a local password until SSO
    or password reset sets one. Operators may opt into a fixed temporary password
    for UAT/import windows only by setting AI_CONNECTOR_CREATED_USER_PASSWORD_MODE=fixed
    and AI_CONNECTOR_CREATED_USER_DEFAULT_PASSWORD. The password is never returned
    by the API response and is masked by AI Server audit logging.
    """
    mode = str(_setting_or_env('AI_CONNECTOR_CREATED_USER_PASSWORD_MODE', 'unusable') or 'unusable').strip().lower()
    default_password = str(_setting_or_env('AI_CONNECTOR_CREATED_USER_DEFAULT_PASSWORD', '') or '')
    if mode not in {'unusable', 'fixed', 'random'}:
        mode = 'unusable'
    if mode == 'fixed' and len(default_password) < 8:
        # Do not silently create weak-password accounts. Fall back to unusable.
        return 'unusable', '', 'fixed_password_missing_or_too_short'
    return mode, default_password, 'configured'


def _apply_created_user_password(user: Any) -> dict[str, Any]:
    mode, default_password, config_status = _created_user_password_config()
    if mode == 'fixed' and default_password:
        user.set_password(default_password)
        return {
            'password_policy': 'fixed_env_password',
            'password_login_enabled': True,
            'password_config_status': config_status,
            'password_note': 'User được tạo với mật khẩu tạm lấy từ AI_CONNECTOR_CREATED_USER_DEFAULT_PASSWORD; API không trả mật khẩu ra response.',
        }
    if mode == 'random':
        # Random local password prevents login by guessing/default password. Since it
        # is not returned or logged, real access still uses SSO/password reset.
        user.set_password(secrets.token_urlsafe(32))
        return {
            'password_policy': 'random_not_returned',
            'password_login_enabled': False,
            'password_config_status': config_status,
            'password_note': 'User có mật khẩu ngẫu nhiên không trả ra response; đăng nhập bằng SSO hoặc reset password.',
        }
    if hasattr(user, 'set_unusable_password'):
        user.set_unusable_password()
    return {
        'password_policy': 'unusable_password',
        'password_login_enabled': False,
        'password_config_status': config_status,
        'password_note': 'User không có mật khẩu đăng nhập local; dùng SSO hoặc reset password để đặt mật khẩu.',
    }


def _existing_user_password_state(user: Any) -> dict[str, Any]:
    has_usable = None
    try:
        has_usable = bool(user.has_usable_password()) if hasattr(user, 'has_usable_password') else None
    except Exception:
        has_usable = None
    if has_usable is True:
        return {
            'password_policy': 'existing_user_password',
            'password_login_enabled': True,
            'password_note': 'User đã tồn tại và có mật khẩu khả dụng hoặc phương thức xác thực hiện có.',
        }
    if has_usable is False:
        return {
            'password_policy': 'existing_unusable_password',
            'password_login_enabled': False,
            'password_note': 'User đã tồn tại nhưng không có mật khẩu local khả dụng; dùng SSO hoặc reset password.',
        }
    return {
        'password_policy': 'existing_unknown',
        'password_login_enabled': None,
        'password_note': 'Không xác định được trạng thái mật khẩu của user đã tồn tại.',
    }


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
        return None, False, 'missing_username', {'password_policy': 'not_created', 'password_login_enabled': None, 'password_note': 'Thiếu username nên không tạo user.'}
    first_name, last_name, full_name = _split_display_name(item, username, person_type)
    email = _payload_email(item, username, person_type)
    try:
        with transaction.atomic():
            user = User(username=username, email=email, first_name=first_name, last_name=last_name, is_active=True)
            password_state = _apply_created_user_password(user)
            user.save()
            created = True
    except IntegrityError:
        try:
            user = User.objects.get(username__iexact=username)
            password_state = _existing_user_password_state(user)
            created = False
        except Exception:
            raise
    profile_state = _ensure_user_profile(user, full_name=full_name, username=username)
    if not profile_state.get('ok'):
        # Do not hide this. Enrollment on Ulmo.3 fails later with "User has no
        # profile" if this row is missing, so user creation/resolve must expose
        # the actual problem immediately.
        message = profile_state.get('message') or 'Không tạo/kiểm tra được UserProfile'
        if _connector_debug_errors_enabled():
            extra = profile_state.get('import_error') or profile_state.get('error')
            if extra:
                message = f'{message}: {extra}'
        raise RuntimeError(message)
    password_state = {**password_state, 'user_profile_ok': True, 'user_profile_created': bool(profile_state.get('created')), 'user_profile_model_source': profile_state.get('source')}
    return user, created, 'created' if created else 'exists', password_state


@csrf_exempt
def student_insight_resolve_users(request):
    """Resolve AP usernames against Open edX/CMS users.

    URL contract:
      POST /api/ai-connector/v1/users/resolve

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
    max_batch = int(_setting_or_env('AI_CONNECTOR_MAX_BATCH_SIZE', _setting_or_env('AI_STUDENT_INSIGHT_MAX_BATCH_SIZE', '5000')) or '5000')
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
            profile_state = _ensure_user_profile(user, full_name=full_name, username=username)
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
                **_existing_user_password_state(user),
                'user_profile_ok': bool(profile_state.get('ok')),
                'user_profile_created': bool(profile_state.get('created')),
                'user_profile_model_source': profile_state.get('source'),
                'user_profile_message': profile_state.get('message'),
                'note': 'Khớp chính xác AP username = CMS/Open edX username' if profile_state.get('ok') else 'Khớp user nhưng chưa tạo/kiểm tra được UserProfile',
            }
            found.append(row)
        else:
            created_user = None
            created = False
            password_state = {'password_policy': 'not_created', 'password_login_enabled': None, 'password_note': ''}
            create_message = 'Không tìm thấy user CMS/Open edX theo AP username'
            if create_missing and username:
                try:
                    created_user, created, _create_status, password_state = _ensure_cms_user(item.get('raw'), username, item.get('person_type') or 'student')
                except Exception as exc:
                    created_user = None
                    create_message = 'Không tạo được user CMS/Open edX' + (f': {exc}' if _connector_debug_errors_enabled() else '')
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
                    **password_state,
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
                    **password_state,
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


@csrf_exempt
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
            # On Open edX Ulmo.3, CourseOverview.id is a CourseKeyField; comparing
            # it to a raw string can silently return no rows on some deployments.
            # Parse the opaque key explicitly so course-v1:FPT+WEB107+SU26 maps
            # even when the MFE URL already exists.
            exact_qs = []
            try:
                from opaque_keys.edx.keys import CourseKey  # type: ignore
                exact_key = CourseKey.from_string(exact_course_id)
                exact_qs = list(CourseOverview.objects.filter(id=exact_key).order_by('id')[:limit])
            except Exception:
                exact_qs = []
            if not exact_qs:
                # Fallback for older/custom CourseOverview implementations where
                # id is stored as a string-compatible column.
                exact_qs = list(CourseOverview.objects.filter(id=exact_course_id).order_by('id')[:limit])
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
    max_batch = int(_setting_or_env('AI_CONNECTOR_MAX_BATCH_SIZE', _setting_or_env('AI_STUDENT_INSIGHT_MAX_BATCH_SIZE', '5000')) or '5000')
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
        CourseEnrollment, _source, _import_error = _course_enrollment_model()
        if CourseEnrollment is None:
            return result
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
        PersistentCourseGrade, _source, _import_error = _persistent_course_grade_model()
        if PersistentCourseGrade is None:
            return result
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
                'passed_timestamp': _datetime_iso(passed_timestamp),
                'modified': _datetime_iso(getattr(row, 'modified', None)),
            }
    except Exception:
        # Some Open edX installs keep grades computable but not persisted yet.
        # Do not fail the whole class sync: user existence/enrollment remains useful.
        return result
    return result




def _datetime_iso(value: Any) -> str | None:
    if value is None or value == '':
        return None
    try:
        if isinstance(value, datetime):
            dt = value
            if dt.tzinfo is not None:
                return dt.astimezone(VN_TZ).isoformat()
            return dt.replace(tzinfo=VN_TZ).isoformat()
        if hasattr(value, 'isoformat'):
            return value.isoformat()
    except Exception:
        pass
    raw = _safe_str(value)
    if not raw:
        return None
    try:
        dt = datetime.fromisoformat(raw.replace('Z', '+00:00'))
        if dt.tzinfo is not None:
            return dt.astimezone(VN_TZ).isoformat()
        return dt.replace(tzinfo=VN_TZ).isoformat()
    except Exception:
        return raw


def _latest_datetime_iso(left: Any, right: Any) -> Any:
    if left is None:
        return right
    if right is None:
        return left
    try:
        return right if right > left else left
    except Exception:
        return right or left


def _quiz_numbers_from_label(value: Any) -> list[int]:
    raw = _safe_str(value).lower()
    if not raw:
        return []
    numbers: set[int] = set()
    for token in re.findall(r'(?:quiz|learning\s*check|lc)\s*#?\s*(\d{1,3})', raw, flags=re.I):
        try:
            n = int(token)
            if 1 <= n <= 200:
                numbers.add(n)
        except Exception:
            pass
    return sorted(numbers)


def _looks_like_quiz_component(name: Any, block: Any | None = None) -> bool:
    text = _safe_str(name).strip().lower()
    if any(token in text for token in ('quiz', 'learning check', 'lc ')):
        return True
    if block is not None:
        for attr in ('graded', 'has_score', 'weight'):
            try:
                value = getattr(block, attr, None)
                if value is True:
                    return True
            except Exception:
                pass
        try:
            fmt = _safe_str(getattr(block, 'format', '') or getattr(block, 'due', '')).lower()
            if 'quiz' in fmt:
                return True
        except Exception:
            pass
    return False


def _course_outline_quiz_components(course_key: Any) -> list[dict[str, Any]]:
    """Return planned quiz/graded components from course outline even if no grades exist yet.

    This is intentionally best-effort. It exists so AI Server can render the
    dynamic Quiz columns from the course itself instead of waiting for
    PersistentSubsectionGrade to be populated.
    """
    if not course_key:
        return []
    try:
        CourseKey, modulestore = _load_openedx_modules()
        store = modulestore()
        course = store.get_course(course_key)
    except Exception:
        return []
    components: list[dict[str, Any]] = []
    seen: set[str] = set()
    stack: list[Any] = list(getattr(course, 'children', []) or [])
    while stack:
        key = stack.pop(0)
        raw_key = _safe_str(key)
        if not raw_key or raw_key in seen:
            continue
        seen.add(raw_key)
        block = _get_item_best_effort(store, key)
        if block is None:
            continue
        block_type = _block_type(block)
        children = _children_locations(block)
        if children:
            stack[0:0] = list(children)
        display = _display_name(block) or raw_key
        if block_type in {'sequential', 'vertical', 'problem'} and _looks_like_quiz_component(display, block):
            name = (display or '').strip()
            category = 'quiz' if 'quiz' in name.lower() or block_type == 'sequential' else block_type
            components.append({
                'key': raw_key,
                'usage_key': raw_key,
                'name': name[:255] if name else '',
                'category': category,
                'earned': None,
                'possible': None,
                'percent': None,
                'planned': True,
                'source': 'course_outline',
                'block_type': block_type,
            })
    # Prefer real quiz containers (sequential/vertical) over individual problem-bank
    # children. Problem-bank/randomized items can have keys like `quiz-14` and would
    # otherwise create phantom Quiz columns.
    higher = [item for item in components if item.get('block_type') in {'sequential', 'vertical'}]
    usable = higher or components
    usable.sort(key=lambda item: (_safe_str(item.get('name')), _safe_str(item.get('key'))))
    fixed: list[dict[str, Any]] = []
    for index, item in enumerate(usable[:80], start=1):
        row = dict(item)
        numbers = _quiz_numbers_from_label(row.get('name'))
        quiz_number = numbers[0] if numbers else index
        row['quiz_number'] = quiz_number
        row['order'] = quiz_number
        name = _safe_str(row.get('name')).strip()
        if name.lower() in {'quiz', 'learning check', 'lc', ''}:
            name = f'Quiz {quiz_number}'
        row['name'] = name[:255]
        fixed.append(row)
    return fixed

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
    planned_components = _course_outline_quiz_components(course_key)
    try:
        PersistentSubsectionGrade, _source, _import_error = _persistent_subsection_grade_model()
        if PersistentSubsectionGrade is None:
            return result
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
                'submitted_at': _datetime_iso(getattr(row, 'modified', None) or getattr(row, 'updated_at', None) or getattr(row, 'created', None)),
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
    if planned_components:
        user_ids = [int(getattr(user, 'id', 0) or 0) for user in users if getattr(user, 'id', None) is not None]
        for uid in user_ids:
            existing = result.get(uid) or []
            if existing:
                # If CMS/Open edX already returned actual Detailed grades for the
                # learner, do not supplement with planned outline shells because
                # that can create phantom columns (for example Quiz 14 when the
                # gradebook currently exposes only Quiz 1 and Quiz 2).
                result[uid] = existing[:80]
                continue
            result[uid] = [dict(item) for item in planned_components[:80]]
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
        StudentModule, _source, _import_error = _student_module_model()
        if StudentModule is None:
            return result
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
                'submitted_at_raw': None,
                'source': 'StudentModule.problem_grade',
            })
            bucket['earned'] += earned
            bucket['possible'] += possible
            bucket['problem_count'] += 1
            bucket['submitted_at_raw'] = _latest_datetime_iso(bucket.get('submitted_at_raw'), getattr(row, 'modified', None) or getattr(row, 'updated_at', None) or getattr(row, 'created', None))
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
            'submitted_at': _datetime_iso(bucket.get('submitted_at_raw')),
            'problem_count': int(bucket.get('problem_count') or 0),
            'source': bucket['source'],
        })
    for items in result.values():
        items.sort(key=lambda item: _safe_str(item.get('name') or item.get('key')))
    return result





def _extract_completion_percent_from_payload(value: Any) -> float | None:
    """Extract Course completion percent from nested official progress payloads.

    Open edX releases differ: some Course Home APIs return ``percent`` directly,
    others return completed/total counts or complete/incomplete summary objects.
    This extractor intentionally avoids grade-only keys and is used only on
    payloads that came from Course Home / completion APIs.
    """
    seen: set[int] = set()
    preferred_keys = {
        'course_completion_percent', 'courseCompletionPercent', 'completion_percent',
        'completionPercent', 'percent_complete', 'percentComplete', 'completion_rate',
        'completionRate', 'course_completion', 'courseCompletion', 'completed_percent',
        'completedPercent', 'progress_percent', 'progressPercent', 'percent',
        'percentage', 'value',
    }

    def as_number(raw: Any) -> float | None:
        try:
            return float(raw)
        except Exception:
            return None

    def as_percent(raw: Any) -> float | None:
        number = as_number(raw)
        if number is None:
            return None
        if 0 <= number <= 1:
            number *= 100.0
        if 0 <= number <= 100:
            return round(number, 2)
        return None

    def percent_from_counts(node: dict[str, Any]) -> float | None:
        completed = (
            node.get('completed_blocks')
            or node.get('complete_count')
            or node.get('completed_count')
            or node.get('completed')
            or node.get('complete')
            or node.get('done')
            or node.get('visited')
        )
        total = (
            node.get('total_blocks')
            or node.get('total_count')
            or node.get('block_count')
            or node.get('total')
            or node.get('required')
            or node.get('possible')
        )
        completed_number = as_number(completed)
        total_number = as_number(total)
        if completed_number is not None and total_number and total_number > 0:
            return round(max(0.0, min(100.0, completed_number / total_number * 100.0)), 2)

        incomplete = (
            node.get('incomplete_blocks')
            or node.get('incomplete_count')
            or node.get('incomplete')
            or node.get('not_completed')
            or node.get('remaining')
            or node.get('todo')
        )
        incomplete_number = as_number(incomplete)
        if completed_number is not None and incomplete_number is not None and completed_number + incomplete_number > 0:
            return round(max(0.0, min(100.0, completed_number / (completed_number + incomplete_number) * 100.0)), 2)
        return None

    def walk(node: Any, depth: int = 0) -> float | None:
        if depth > 8:
            return None
        if isinstance(node, dict):
            marker = id(node)
            if marker in seen:
                return None
            seen.add(marker)
            for key in preferred_keys:
                if key not in node:
                    continue
                value = node.get(key)
                if isinstance(value, dict):
                    nested = walk(value, depth + 1)
                    if nested is not None:
                        return nested
                percent = as_percent(value)
                if percent is not None:
                    return percent
            counted = percent_from_counts(node)
            if counted is not None:
                return counted
            for child_key in (
                'completion', 'course_completion', 'courseCompletion', 'progress',
                'course_progress', 'courseProgress', 'summary', 'data', 'result',
                'completion_summary', 'completionSummary', 'progress_summary',
                'progressSummary', 'progressTab', 'courseware', 'home', 'body',
            ):
                if child_key in node:
                    nested = walk(node.get(child_key), depth + 1)
                    if nested is not None:
                        return nested
        elif isinstance(node, list):
            for item in node:
                nested = walk(item, depth + 1)
                if nested is not None:
                    return nested
        return None

    return walk(value)


def _payload_has_completion_summary(value: Any, depth: int = 0) -> bool:
    if depth > 6:
        return False
    if isinstance(value, dict):
        summary = value.get('completion_summary') or value.get('completionSummary')
        if isinstance(summary, dict) and (
            'complete_count' in summary
            or 'completed_count' in summary
            or 'incomplete_count' in summary
        ):
            return True
        return any(_payload_has_completion_summary(child, depth + 1) for child in value.values())
    if isinstance(value, list):
        return any(_payload_has_completion_summary(item, depth + 1) for item in value)
    return False

def _serialize_response_payload(response: Any) -> Any:
    try:
        data = getattr(response, 'data', None)
        if data is not None:
            return data
    except Exception:
        pass
    try:
        import json
        raw = getattr(response, 'content', b'') or b''
        if raw:
            return json.loads(raw.decode('utf-8'))
    except Exception:
        pass
    return {}


def _course_home_view_response(view_cls: Any, request: Any, course_key: Any, course_id_text: str) -> Any | None:
    """Call Course Home progress views across Open edX signature variants."""
    try:
        view = view_cls.as_view()
    except Exception:
        return None
    call_variants = [
        {'course_id': course_id_text},
        {'course_key_string': course_id_text},
        {'course_key': course_key},
        {'course_key': course_id_text},
        {'course_id': course_key},
        {'course_key_string': course_key},
        {},
    ]
    for kwargs in call_variants:
        try:
            return view(request, **kwargs)
        except TypeError:
            continue
        except Exception:
            continue
    return None


def _authenticate_synthetic_request(request: Any, user: Any) -> None:
    """Attach learner identity to RequestFactory requests for DRF/Django views.

    Course Home progress is exposed by the exact LMS URL
    ``/api/course_home/progress/<course_id>`` and resolves the learner from
    ``request.user``.  DRF views may ignore a bare ``request.user`` unless the
    request is force-authenticated, so set both forms when available.
    """
    try:
        request.user = user
    except Exception:
        pass
    try:
        from rest_framework.test import force_authenticate  # type: ignore
        force_authenticate(request, user=user)
    except Exception:
        pass


def _course_home_resolved_response(request: Any, course_id_text: str) -> tuple[Any | None, str | None]:
    """Call the actual Course Home progress route registered in this LMS.

    Ulmo deployments can name the backing class differently from upstream
    examples.  The browser-proven source on this system is
    ``/api/course_home/progress/<course_id>``.  Resolving and invoking the
    registered callback avoids guessing class names such as ProgressTabView.
    """
    try:
        from django.urls import resolve  # type: ignore
    except Exception:
        return None, None
    paths = [
        f'/api/course_home/progress/{course_id_text}',
        f'/api/course_home/progress/{course_id_text}/',
        f'/api/course_home/v1/progress/{course_id_text}',
        f'/api/course_home/v1/progress/{course_id_text}/',
    ]
    for path in paths:
        try:
            match = resolve(path)
        except Exception:
            continue
        try:
            return match.func(request, *match.args, **match.kwargs), path
        except TypeError:
            try:
                return match.func(request, **match.kwargs), path
            except Exception:
                continue
        except Exception:
            continue
    return None, None


def _completion_api_progress_snapshot(course_key: Any, users: list[Any]) -> dict[int, dict[str, Any]]:
    """Try Open edX completion APIs before falling back to diagnostic counts.

    We use only functions exposed by the Open edX completion/course-home stack.
    If a deployment exposes a summary API with completed/total counts, this gives
    the same source family as the learner Course Home completion card instead of
    our old unsafe StudentModule ratio.
    """
    result: dict[int, dict[str, Any]] = {}
    if not course_key or not users:
        return result
    try:
        completion_api = importlib.import_module('completion.api')
    except Exception:
        return result

    candidate_names = [
        'get_course_completion_summary',
        'get_course_blocks_completion_summary',
        'get_course_completion',
        'get_completion_summary',
        'get_course_progress',
        'get_progress_summary',
    ]

    for user in users:
        uid = int(getattr(user, 'id', 0) or 0)
        if uid <= 0:
            continue
        for name in candidate_names:
            func = getattr(completion_api, name, None)
            if not callable(func):
                continue
            call_variants = [
                lambda f=func: f(user, course_key),
                lambda f=func: f(course_key, user),
                lambda f=func: f(user=user, course_key=course_key),
                lambda f=func: f(course_key=course_key, user=user),
                lambda f=func: f(user=user, context_key=course_key),
                lambda f=func: f(context_key=course_key, user=user),
                lambda f=func: f(user.id, course_key),
                lambda f=func: f(course_key, user.id),
            ]
            for call in call_variants:
                try:
                    payload = call()
                except TypeError:
                    continue
                except Exception:
                    continue
                percent = _extract_completion_percent_from_payload(payload)
                if percent is not None:
                    result[uid] = {
                        'percent': percent,
                        'source': f'CompletionAPI:{name}',
                        'payload': payload if isinstance(payload, dict) else {'value': _safe_str(payload)},
                    }
                    break
            if uid in result:
                break
    return result

def _course_home_progress_snapshot(course_key: Any, users: list[Any]) -> dict[int, dict[str, Any]]:
    """Best-effort read of the learner Course Home completion value.

    Earlier builds tried a single kwarg name and silently returned no progress on
    Ulmo/Indigo variants where the view expects ``course_key_string`` or a course
    key object. This version tries the common view classes and signature variants
    before falling back to completion.api.
    """
    result: dict[int, dict[str, Any]] = {}
    if not course_key or not users:
        return result
    view_candidates = [
        ('lms.djangoapps.course_home_api.progress.views', 'CourseProgressView'),
        ('lms.djangoapps.course_home_api.progress.views', 'ProgressTabView'),
        ('lms.djangoapps.course_home_api.progress.views', 'CourseHomeProgressView'),
        ('openedx.features.course_experience.views.course_home', 'CourseHomeProgressView'),
        ('openedx.features.course_experience.views.course_home', 'CourseHomeProgressTabView'),
    ]
    view_classes: list[Any] = []
    for module_name, attr in view_candidates:
        try:
            module = importlib.import_module(module_name)
            view_cls = getattr(module, attr)
            if view_cls not in view_classes:
                view_classes.append(view_cls)
        except Exception:
            continue
    try:
        from django.test import RequestFactory  # type: ignore
    except Exception:
        view_classes = []
        RequestFactory = None  # type: ignore

    if RequestFactory is not None:
        factory = RequestFactory()
        course_id_text = _safe_str(course_key)
        for user in users:
            uid = int(getattr(user, 'id', 0) or 0)
            if uid <= 0:
                continue

            # First use the exact route that the browser calls on Ulmo:
            # /api/course_home/progress/<course_id>.  This is more reliable
            # than guessing the view class name because deployments can patch or
            # wrap course_home_api views.
            try:
                request = factory.get(f'/api/course_home/progress/{course_id_text}')
                _authenticate_synthetic_request(request, user)
                response, resolved_path = _course_home_resolved_response(request, course_id_text)
                if response is not None:
                    content = _serialize_response_payload(response)
                    percent = _extract_completion_percent_from_payload(content)
                    if percent is not None:
                        source = 'CourseHomeProgressRoute:completion_summary' if _payload_has_completion_summary(content) else 'CourseHomeProgressRoute'
                        result[uid] = {
                            'percent': percent,
                            'source': source,
                            'payload': content if isinstance(content, dict) else {'value': _safe_str(content)},
                            'resolved_path': resolved_path,
                        }
                        continue
            except Exception:
                pass

            for view_cls in view_classes:
                try:
                    request = factory.get(f'/api/course_home/progress/{course_id_text}')
                    _authenticate_synthetic_request(request, user)
                    response = _course_home_view_response(view_cls, request, course_key, course_id_text)
                    if response is None:
                        continue
                    content = _serialize_response_payload(response)
                    percent = _extract_completion_percent_from_payload(content)
                    if percent is not None:
                        source = 'CourseHomeProgress:completion_summary' if _payload_has_completion_summary(content) else f'CourseHomeAPI:{getattr(view_cls, "__name__", "view")}'
                        result[uid] = {
                            'percent': percent,
                            'source': source,
                            'payload': content if isinstance(content, dict) else {'value': _safe_str(content)},
                        }
                        break
                except Exception:
                    continue
            # If any Course Home view worked for this learner, do not try less
            # specific APIs for the same learner.
            if uid in result:
                continue
    if len(result) < len([u for u in users if int(getattr(u, 'id', 0) or 0) > 0]):
        api_result = _completion_api_progress_snapshot(course_key, users)
        for uid, item in api_result.items():
            result.setdefault(uid, item)
    return result

def _completion_snapshot(course_key: Any, users: list[Any]) -> tuple[dict[int, dict[str, Any]], int | None]:
    result: dict[int, dict[str, Any]] = {}
    total_blocks: int | None = None
    if not course_key or not users:
        return result, total_blocks
    user_ids = [getattr(user, 'id', None) for user in users if getattr(user, 'id', None) is not None]
    official_progress = _course_home_progress_snapshot(course_key, users)
    if official_progress:
        result.update(official_progress)
    try:
        from completion.models import BlockCompletion  # type: ignore
        from django.db.models import Count, Max  # type: ignore
        rows = BlockCompletion.objects.filter(context_key=course_key, user_id__in=user_ids).values('user_id').annotate(completed=Count('id'), last_activity=Max('modified'))
        for row in rows:
            uid = int(row.get('user_id') or 0)
            completed = int(row.get('completed') or 0)
            last_activity = row.get('last_activity')
            bucket = result.setdefault(uid, {'source': 'BlockCompletion'})
            bucket.setdefault('source', 'BlockCompletion')
            bucket['completed_blocks'] = completed
            bucket['last_activity_at'] = _datetime_iso(last_activity) or bucket.get('last_activity_at')
    except Exception:
        # Ulmo/Indigo deployments may not have the completion app populated.
        # Do not give up: fall back to StudentModule interaction counts below.
        pass
    try:
        CourseKey, modulestore = _load_openedx_modules()
        store = modulestore()
        course = store.get_course(course_key)
        visited: set[str] = set()
        stack = list(getattr(course, 'children', []) or [])
        count = 0
        problem_count = 0
        while stack:
            key = stack.pop()
            raw_key = str(key)
            if raw_key in visited:
                continue
            visited.add(raw_key)
            block = _get_item_best_effort(store, key)
            if block is None:
                continue
            block_type = _block_type(block)
            children = _children_locations(block)
            if children:
                stack.extend(children)
            if block_type not in {'course', 'chapter', 'sequential', 'vertical'}:
                count += 1
            if block_type == 'problem':
                problem_count += 1
        total_blocks = count or problem_count or None
    except Exception:
        total_blocks = None
    if not result:
        try:
            StudentModule, _source, _import_error = _student_module_model()
            if StudentModule is None:
                rows = []
            else:
                from django.db.models import Count, Max  # type: ignore
                rows = StudentModule.objects.filter(course_id=course_key, student_id__in=user_ids).values('student_id').annotate(completed=Count('id'), last_activity=Max('modified'))
            for row in rows:
                uid = int(row.get('student_id') or 0)
                completed = int(row.get('completed') or 0)
                last_activity = row.get('last_activity')
                result[uid] = {
                    'completed_blocks': completed,
                    'last_activity_at': _datetime_iso(last_activity),
                    'source': 'StudentModule',
                }
        except Exception:
            pass
    if total_blocks:
        for item in result.values():
            completed = item.get('completed_blocks') or 0
            item['total_blocks'] = total_blocks
            # Do not synthesize Course completion percent from BlockCompletion or
            # StudentModule counts. The learner dashboard completion card is the
            # source of truth; fallback counts are kept only for diagnostics/activity.
            if _safe_str(item.get('source')).lower() == 'coursehomeapi' and item.get('percent') is None:
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
            'progress_source': progress.get('source'),
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


def _learning_connector_diagnostics() -> dict[str, Any]:
    CourseEnrollment, ce_source, ce_error = _course_enrollment_model()
    PersistentCourseGrade, pcg_source, pcg_error = _persistent_course_grade_model()
    PersistentSubsectionGrade, psg_source, psg_error = _persistent_subsection_grade_model()
    course_home_views: list[str] = []
    for module_name, attr in [
        ('lms.djangoapps.course_home_api.progress.views', 'CourseProgressView'),
        ('lms.djangoapps.course_home_api.progress.views', 'ProgressTabView'),
        ('lms.djangoapps.course_home_api.progress.views', 'CourseHomeProgressView'),
        ('openedx.features.course_experience.views.course_home', 'CourseHomeProgressView'),
        ('openedx.features.course_experience.views.course_home', 'CourseHomeProgressTabView'),
    ]:
        try:
            module = importlib.import_module(module_name)
            getattr(module, attr)
            course_home_views.append(f'{module_name}.{attr}')
        except Exception:
            continue
    completion_api_functions: list[str] = []
    try:
        completion_api = importlib.import_module('completion.api')
        for name in [
            'get_course_completion_summary',
            'get_course_blocks_completion_summary',
            'get_course_completion',
            'get_completion_summary',
            'get_course_progress',
            'get_progress_summary',
        ]:
            if callable(getattr(completion_api, name, None)):
                completion_api_functions.append(name)
    except Exception:
        pass
    return {
        'course_enrollment_model_available': CourseEnrollment is not None,
        'course_enrollment_model_source': ce_source or None,
        'course_enrollment_import_error': ce_error,
        'persistent_course_grade_model_available': PersistentCourseGrade is not None,
        'persistent_course_grade_model_source': pcg_source or None,
        'persistent_course_grade_import_error': pcg_error,
        'persistent_subsection_grade_model_available': PersistentSubsectionGrade is not None,
        'persistent_subsection_grade_model_source': psg_source or None,
        'persistent_subsection_grade_import_error': psg_error,
        'course_home_progress_views': course_home_views,
        'completion_api_functions': completion_api_functions,
    }


@csrf_exempt
def student_insight_class_analytics(request):
    """Return enrollment/progress/grade snapshots for a class in one call.

    URL contract:
      POST /api/ai-connector/v1/class-analytics
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
    learning_counts = {
        'enrolled': sum(1 for item in results if bool((item.get('enrollment') or {}).get('is_enrolled'))),
        'with_progress': sum(1 for item in results if item.get('progress_percent') is not None or item.get('completed_blocks') is not None),
        'with_total_grade': sum(1 for item in results if item.get('grade_percent') is not None),
        'with_component_grades': sum(1 for item in results if item.get('component_scores')),
    }
    return _json_response({'ok': True, 'course_id': course_id, 'total': len(results), 'counts': counts, 'learning_counts': learning_counts, 'diagnostics': _learning_connector_diagnostics(), 'results': results})


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
    clean_mode = str(mode or _setting_or_env('AI_CONNECTOR_DEFAULT_ENROLLMENT_MODE', _setting_or_env('AI_STUDENT_INSIGHT_DEFAULT_ENROLLMENT_MODE', 'audit')) or 'audit').strip() or 'audit'
    results: list[dict[str, Any]] = []
    CourseEnrollment, course_enrollment_source, course_enrollment_import_error = _course_enrollment_model()
    if CourseEnrollment is None:
        return [{
            'student_code': item.get('student_code') or None,
            'ap_username': item.get('username') or '',
            'username': item.get('username') or '',
            'exists': False,
            'status': 'failed',
            'enrollment_status': 'failed',
            'is_enrolled': False,
            'verified_after_write': False,
            'message': 'Không import được CourseEnrollment trên Open edX LMS. Đã thử common.djangoapps.student.models.CourseEnrollment và student.models.CourseEnrollment.',
            'diagnostics': {
                'course_enrollment_model_source': None,
                'course_enrollment_import_error': course_enrollment_import_error,
                'course_id': course_id,
            },
        } for item in requested]

    for item in requested:
        username = item.get('username') or ''
        person_type = str(item.get('person_type') or item.get('role') or 'student').strip().lower()
        is_teacher = person_type in {'teacher', 'staff', 'instructor'}
        user = found_by_key.get(username) if username else None
        if user is None and item.get('openedx_user_id'):
            user = found_by_key.get(f"id:{item['openedx_user_id']}")
        created_user = False
        password_state = _existing_user_password_state(user) if user is not None else {'password_policy': 'not_created', 'password_login_enabled': None, 'password_note': ''}
        if user is None and (create_missing or item.get('create_missing')) and username:
            try:
                user, created_user, _create_status, password_state = _ensure_cms_user(item.get('raw') if item.get('raw') else item, username, 'teacher' if is_teacher else 'student')
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
                    'message': 'Không tạo được user CMS/Open edX' + (f": {exc}" if _connector_debug_errors_enabled() else ''),
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
            **password_state,
            'enrollment_mode': clean_mode,
            'course_enrollment_model_source': course_enrollment_source,
        }
        if user is None:
            results.append({**base, 'status': 'missing_user', 'enrollment_status': 'missing_user', 'is_enrolled': False, 'message': 'Không tìm thấy user CMS/Open edX'})
            continue
        if getattr(user, 'is_active', True) is False:
            results.append({**base, 'status': 'inactive_user', 'enrollment_status': 'inactive_user', 'is_enrolled': False, 'message': 'User CMS/Open edX inactive'})
            continue

        profile_full_name = user.get_full_name() if hasattr(user, 'get_full_name') else ''
        profile_state = _ensure_user_profile(user, full_name=profile_full_name, username=username)
        base = {
            **base,
            'user_profile_ok': bool(profile_state.get('ok')),
            'user_profile_created': bool(profile_state.get('created')),
            'user_profile_model_source': profile_state.get('source'),
        }
        if not profile_state.get('ok'):
            results.append({
                **base,
                'status': 'user_profile_failed',
                'enrollment_status': 'failed',
                'is_enrolled': False,
                'verified_after_write': False,
                'message': 'User CMS thiếu profile và connector không tạo/kiểm tra được UserProfile. Enrollment trên Ulmo.3 sẽ fail với lỗi User has no profile.',
                'diagnostics': {
                    'user_profile_model_source': profile_state.get('source'),
                    'user_profile_import_error': profile_state.get('import_error'),
                    'user_profile_error': profile_state.get('error'),
                },
            })
            continue

        if is_teacher:
            try:
                CourseStaffRole, course_staff_role_source, course_staff_role_import_error = _course_staff_role_class()
                if CourseStaffRole is None:
                    results.append({
                        **base,
                        'status': 'course_staff_import_failed',
                        'enrollment_status': 'failed',
                        'is_enrolled': False,
                        'course_role': 'staff',
                        'verified_after_write': False,
                        'message': 'Không import được CourseStaffRole trên Open edX LMS.',
                        'diagnostics': {'course_staff_role_import_error': course_staff_role_import_error},
                    })
                    continue
                role = CourseStaffRole(course_key)
                already_staff = False
                try:
                    already_staff = bool(role.has_user(user))
                except Exception:
                    already_staff = False
                if not already_staff or force:
                    role.add_users(user)
                try:
                    verified_staff = bool(role.has_user(user))
                except Exception:
                    verified_staff = already_staff or not force
                if not verified_staff:
                    results.append({
                        **base,
                        'status': 'course_staff_not_verified',
                        'enrollment_status': 'failed',
                        'is_enrolled': False,
                        'course_role': 'staff',
                        'verified_after_write': False,
                        'message': 'Đã gọi CourseStaffRole.add_users nhưng chưa xác nhận được giảng viên trong Course Staff',
                    })
                    continue
                results.append({
                    **base,
                    'status': 'already_course_staff' if already_staff else 'course_staff_added',
                    'enrollment_status': 'course_staff',
                    'is_enrolled': True,
                    'course_role': 'staff',
                    'course_staff_role_model_source': course_staff_role_source,
                    'verified_after_write': True,
                    'message': 'Giảng viên đã được xác nhận Course Staff',
                })
            except Exception as exc:
                results.append({**base, 'status': 'course_staff_failed', 'enrollment_status': 'failed', 'is_enrolled': False, 'message': 'Không gán được Course Staff cho giảng viên'})
            continue
        try:
            enrollment = CourseEnrollment.objects.filter(user=user, course_id=course_key).first()
            if enrollment and getattr(enrollment, 'is_active', False) and not force:
                results.append({
                    **base,
                    'status': 'already_enrolled',
                    'enrollment_status': 'enrolled',
                    'is_enrolled': True,
                    'verified_after_write': True,
                    'enrollment_id': str(getattr(enrollment, 'id', '') or ''),
                    'enrollment': {
                        'status': 'enrolled',
                        'is_enrolled': True,
                        'mode': getattr(enrollment, 'mode', clean_mode),
                        'id': str(getattr(enrollment, 'id', '') or ''),
                    },
                })
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

            # Hard verification: never report success unless the enrollment row
            # really exists and is active after the write. Earlier builds could
            # return success when CourseEnrollment.enroll() did not create a row
            # in this Open edX release, which made AI Server mark enrollment as
            # successful even though LMS had no enrollment.
            enrollment = CourseEnrollment.objects.filter(user=user, course_id=course_key).first()
            if not enrollment:
                results.append({
                    **base,
                    'status': 'enrollment_not_created',
                    'enrollment_status': 'failed',
                    'is_enrolled': False,
                    'verified_after_write': False,
                    'message': 'CourseEnrollment không được tạo sau khi gọi Open edX enroll API',
                })
                continue
            mode_value = getattr(enrollment, 'mode', clean_mode) or clean_mode
            active = bool(getattr(enrollment, 'is_active', False))
            if not active:
                results.append({
                    **base,
                    'status': 'enrollment_inactive_after_write',
                    'enrollment_status': 'inactive',
                    'is_enrolled': False,
                    'verified_after_write': False,
                    'enrollment_id': str(getattr(enrollment, 'id', '') or ''),
                    'enrollment_mode': mode_value,
                    'enrollment': {'status': 'inactive', 'is_enrolled': False, 'mode': mode_value, 'id': str(getattr(enrollment, 'id', '') or '')},
                    'message': 'Enrollment được tạo nhưng đang inactive trong Open edX',
                })
                continue
            results.append({
                **base,
                'status': status_value,
                'enrollment_status': 'enrolled',
                'is_enrolled': True,
                'verified_after_write': True,
                'enrollment_id': str(getattr(enrollment, 'id', '') or ''),
                'enrollment_mode': mode_value,
                'enrollment': {'status': 'enrolled', 'is_enrolled': True, 'mode': mode_value, 'id': str(getattr(enrollment, 'id', '') or '')},
            })
        except Exception as exc:
            results.append({
                **base,
                'status': 'failed',
                'enrollment_status': 'failed',
                'is_enrolled': False,
                'verified_after_write': False,
                'message': 'Không enroll được sinh viên vào Course CMS' + (f': {exc}' if _connector_debug_errors_enabled() else ''),
            })
    return results


@csrf_exempt
def student_insight_course_enrollment_enroll(request):
    """Enroll resolved CMS/Open edX users into a course.

    URL contract:
      POST /api/ai-connector/v1/course-enrollment/enroll
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
    mode = str(data.get('mode') or _setting_or_env('AI_CONNECTOR_DEFAULT_ENROLLMENT_MODE', _setting_or_env('AI_STUDENT_INSIGHT_DEFAULT_ENROLLMENT_MODE', 'audit')) or 'audit')
    force = bool(data.get('force') is True)
    create_missing = bool(data.get('create_missing') is True or data.get('create_missing_users') is True)
    results = _student_insight_enroll_results(course_id, requested, mode=mode, force=force, create_missing=create_missing)
    counts: dict[str, int] = {}
    for item in results:
        status = str(item.get('status') or item.get('enrollment_status') or 'unknown')
        counts[status] = counts.get(status, 0) + 1
    return _json_response({'ok': True, 'course_id': course_id, 'total': len(results), 'counts': counts, 'results': results})


@csrf_exempt
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


@csrf_exempt
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
    return _json_response({'ok': True, 'course_id': course_id, 'results': [{'username': item.get('username'), 'student_code': item.get('student_code'), 'progress': item.get('progress'), 'progress_percent': item.get('progress_percent'), 'progress_source': item.get('progress_source'), 'completed_blocks': item.get('completed_blocks'), 'total_blocks': item.get('total_blocks')} for item in results], 'total': len(results)})


@csrf_exempt
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
