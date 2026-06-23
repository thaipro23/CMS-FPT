import json
import logging
from datetime import timedelta

from django.conf import settings
from django.db import transaction
from django.db.models import Max
from django.utils import timezone
from opaque_keys.edx.keys import CourseKey, UsageKey

from .models import UnitQuizSession, UnitQuizTimerConfig, UnitResetAudit, UnitResetControl

log = logging.getLogger(__name__)

COOLDOWN_ATTR_NAMES = (
    "submission_wait_seconds",
    "time_between_attempts",
    "wait_between_attempts",
    "waitattempts",
    "attempt_delay",
    "retry_delay",
)


class UnitResetError(Exception):
    code = "UNIT_RESET_ERROR"
    status_code = 400

    def __init__(self, message, code=None, status_code=None):
        super().__init__(message)
        if code:
            self.code = code
        if status_code:
            self.status_code = status_code


class ResetCooldownError(UnitResetError):
    code = "RESET_COOLDOWN"
    status_code = 429

    def __init__(self, wait_seconds, next_reset_allowed_at, reset_count, cooldown_seconds):
        super().__init__("Bạn cần chờ thêm trước khi làm lại bài.")
        self.wait_seconds = max(int(wait_seconds), 0)
        self.next_reset_allowed_at = next_reset_allowed_at
        self.reset_count = reset_count
        self.cooldown_seconds = cooldown_seconds


class ResetLimitExceededError(UnitResetError):
    code = "RESET_LIMIT_EXCEEDED"
    status_code = 429


def get_student_module_model():
    """
    Import StudentModule lazily.

    Important: never import LMS-only Open edX modules at module import time.
    Django loads plugin URLs during manage.py check/migrate/collectstatic; if one
    import path is different on an Open edX release, eager imports break the
    whole LMS before the app can even start.
    """
    candidates = (
        "lms.djangoapps.courseware.models",
        "courseware.models",  # legacy fallback
    )
    last_exc = None
    for module_path in candidates:
        try:
            module = __import__(module_path, fromlist=["StudentModule"])
            return module.StudentModule
        except Exception as exc:  # pragma: no cover - depends on Open edX version
            last_exc = exc
    raise UnitResetError(
        "Không import được StudentModule của Open edX. Kiểm tra bản edx-platform.",
        "STUDENTMODULE_IMPORT_FAILED",
        500,
    ) from last_exc


def get_modulestore():
    try:
        from xmodule.modulestore.django import modulestore
        return modulestore()
    except Exception as exc:  # pragma: no cover - depends on Open edX version
        raise UnitResetError(
            "Không import được modulestore của Open edX.",
            "MODULESTORE_IMPORT_FAILED",
            500,
        ) from exc


def get_course_enrollment_model():
    try:
        from common.djangoapps.student.models import CourseEnrollment
        return CourseEnrollment
    except Exception:  # pragma: no cover - fallback for unusual deployments
        return None


def get_client_ip(request):
    forwarded_for = request.META.get("HTTP_X_FORWARDED_FOR")
    if forwarded_for:
        return forwarded_for.split(",")[0].strip()
    return request.META.get("REMOTE_ADDR")


def parse_keys(course_id, unit_usage_key):
    try:
        course_key = CourseKey.from_string(course_id)
        unit_key = UsageKey.from_string(unit_usage_key)
    except Exception as exc:
        raise UnitResetError("course_id hoặc unit_usage_key không hợp lệ.", "INVALID_KEY", 400) from exc

    if getattr(settings, "UNIT_RESET_REQUIRE_UNIT_COURSE_MATCH", True):
        if getattr(unit_key, "course_key", None) != course_key:
            raise UnitResetError("Unit không thuộc khóa học đã gửi.", "UNIT_COURSE_MISMATCH", 403)

    return course_key, unit_key


def assert_user_can_reset(request, course_key):
    user = request.user
    if not user or not user.is_authenticated:
        raise UnitResetError("Bạn cần đăng nhập để làm lại bài.", "AUTH_REQUIRED", 401)

    if user.is_staff or user.is_superuser:
        return

    if not getattr(settings, "UNIT_RESET_ALLOW_STUDENT_SELF_RESET", True):
        raise UnitResetError("Hệ thống không cho phép sinh viên tự làm lại bài.", "SELF_RESET_DISABLED", 403)

    if getattr(settings, "UNIT_RESET_REQUIRE_ENROLLMENT", True):
        CourseEnrollment = get_course_enrollment_model()
        if CourseEnrollment is not None and not CourseEnrollment.is_enrolled(user, course_key):
            raise UnitResetError("Bạn không thuộc khóa học này.", "NOT_ENROLLED", 403)


def collect_unit_usage_keys(unit_key):
    """Collect Unit root + descendant usage keys from modulestore."""
    store = get_modulestore()
    keys = set()

    def walk(key):
        if key in keys:
            return
        keys.add(key)
        try:
            block = store.get_item(key)
        except Exception as exc:
            # Root unit not found should be a hard error. Child not found can be ignored.
            if key == unit_key:
                raise UnitResetError("Không tìm thấy Unit trong modulestore.", "UNIT_NOT_FOUND", 404) from exc
            log.warning("Could not load child block while collecting unit keys: %s", key, exc_info=True)
            return

        child_keys = getattr(block, "children", None) or []
        for child_key in child_keys:
            walk(child_key)

    walk(unit_key)
    return keys


def extract_usage_keys_from_state(state_text):
    """Find selected randomized/library problem usage keys stored inside StudentModule.state."""
    keys = set()
    if not state_text:
        return keys
    try:
        data = json.loads(state_text)
    except Exception:
        return keys

    def scan(obj):
        if isinstance(obj, dict):
            for value in obj.values():
                scan(value)
        elif isinstance(obj, list):
            for item in obj:
                scan(item)
        elif isinstance(obj, str):
            if "block-v1:" in obj or "+type@" in obj:
                try:
                    keys.add(UsageKey.from_string(obj))
                except Exception:
                    pass

    scan(data)
    return keys


def expand_randomized_selected_keys(user, course_key, reset_keys):
    """
    Problem Bank / Randomized Content Block can store selected child problems in state.
    Expand reset_keys with those selected child keys before deleting StudentModule rows.
    """
    StudentModule = get_student_module_model()
    modules = StudentModule.objects.filter(
        student=user,
        course_id=course_key,
        module_state_key__in=list(reset_keys),
    )

    for module in modules:
        try:
            reset_keys.update(extract_usage_keys_from_state(module.state))
        except Exception:
            log.exception("Could not inspect StudentModule state for reset: %s", module.id)

    return reset_keys


def get_block_cooldown_seconds(usage_keys):
    """Return max cooldown seconds found in Unit descendants; fallback to setting default."""
    store = get_modulestore()
    max_seconds = 0

    for key in usage_keys:
        try:
            block = store.get_item(key)
        except Exception:
            continue

        for attr in COOLDOWN_ATTR_NAMES:
            value = getattr(block, attr, None)
            if value in (None, "", False):
                continue
            try:
                seconds = int(value)
            except Exception:
                continue
            if seconds > max_seconds:
                max_seconds = seconds

    if max_seconds <= 0:
        max_seconds = int(getattr(settings, "UNIT_RESET_DEFAULT_COOLDOWN_SECONDS", 60))

    return max_seconds


def get_latest_studentmodule_modified(user, course_key, reset_keys):
    StudentModule = get_student_module_model()
    result = StudentModule.objects.filter(
        student=user,
        course_id=course_key,
        module_state_key__in=list(reset_keys),
    ).aggregate(latest=Max("modified"))
    return result.get("latest")




def clear_user_grade_cache(user, course_key):
    """
    Clear persistent grade cache for this learner/course.

    Reset StudentModule only clears answers/randomized state. The Progress tab
    can still display old scores from persistent grade tables, so clear them and
    let Open edX recalculate from current StudentModule rows.
    """
    result = {
        "persistent_subsection_grade": 0,
        "persistent_course_grade": 0,
    }

    try:
        from lms.djangoapps.grades.models import (
            PersistentCourseGrade,
            PersistentSubsectionGrade,
        )
    except Exception:
        log.exception("Could not import persistent grade models")
        return result

    try:
        subsection_count, _ = PersistentSubsectionGrade.objects.filter(
            user_id=user.id,
            course_id=course_key,
        ).delete()
        result["persistent_subsection_grade"] = subsection_count
    except Exception:
        log.exception(
            "Could not delete PersistentSubsectionGrade user_id=%s course_id=%s",
            user.id,
            course_key,
        )

    try:
        course_count, _ = PersistentCourseGrade.objects.filter(
            user_id=user.id,
            course_id=course_key,
        ).delete()
        result["persistent_course_grade"] = course_count
    except Exception:
        log.exception(
            "Could not delete PersistentCourseGrade user_id=%s course_id=%s",
            user.id,
            course_key,
        )

    return result


def audit_reset(request, course_key, unit_key, **kwargs):
    if not getattr(settings, "UNIT_RESET_AUDIT_LOG_ENABLED", True):
        return

    try:
        UnitResetAudit.objects.create(
            user=request.user,
            course_id=str(course_key),
            unit_usage_key=str(unit_key),
            action=kwargs.get("action", "reset_unit"),
            success=kwargs.get("success", False),
            code=kwargs.get("code", ""),
            message=kwargs.get("message", ""),
            wait_seconds=kwargs.get("wait_seconds", 0),
            cooldown_seconds=kwargs.get("cooldown_seconds", 0),
            deleted_count=kwargs.get("deleted_count", 0),
            reset_keys_count=kwargs.get("reset_keys_count", 0),
            ip_address=get_client_ip(request),
            user_agent=(request.META.get("HTTP_USER_AGENT", "") or "")[:2000],
        )
    except Exception:
        log.exception("Could not write UnitResetAudit")


def get_or_create_control(user, course_key, unit_key, cooldown_seconds):
    return UnitResetControl.objects.get_or_create(
        user=user,
        course_id=str(course_key),
        unit_usage_key=str(unit_key),
        defaults={"cooldown_seconds": cooldown_seconds},
    )


def get_status_for_current_user(request, course_id, unit_usage_key):
    course_key, unit_key = parse_keys(course_id, unit_usage_key)
    assert_user_can_reset(request, course_key)

    reset_keys = collect_unit_usage_keys(unit_key)
    reset_keys = expand_randomized_selected_keys(request.user, course_key, reset_keys)
    cooldown_seconds = get_block_cooldown_seconds(reset_keys)

    record, _ = get_or_create_control(request.user, course_key, unit_key, cooldown_seconds)
    now = timezone.now()

    latest_attempt_at = None
    if getattr(settings, "UNIT_RESET_USE_LATEST_STUDENTMODULE_MODIFIED_AS_ATTEMPT_TIME", True):
        latest_attempt_at = get_latest_studentmodule_modified(request.user, course_key, reset_keys)

    baseline = max([d for d in (record.last_reset_at, record.last_attempt_at, latest_attempt_at) if d], default=None)
    next_allowed_at = record.next_reset_allowed_at

    if baseline and not next_allowed_at:
        next_allowed_at = baseline + timedelta(seconds=cooldown_seconds)

    can_reset = not next_allowed_at or now >= next_allowed_at
    wait_seconds = 0 if can_reset else int((next_allowed_at - now).total_seconds())

    return {
        "success": True,
        "can_reset": can_reset,
        "wait_seconds": max(wait_seconds, 0),
        "cooldown_seconds": cooldown_seconds,
        "reset_count": record.reset_count,
        "last_attempt_at": latest_attempt_at.isoformat() if latest_attempt_at else None,
        "last_reset_at": record.last_reset_at.isoformat() if record.last_reset_at else None,
        "next_reset_allowed_at": next_allowed_at.isoformat() if next_allowed_at else None,
        "reset_keys_count": len(reset_keys),
    }


def reset_unit_for_current_user(request, course_id, unit_usage_key):
    course_key, unit_key = parse_keys(course_id, unit_usage_key)
    assert_user_can_reset(request, course_key)

    base_keys = collect_unit_usage_keys(unit_key)
    reset_keys = expand_randomized_selected_keys(request.user, course_key, base_keys)
    cooldown_seconds = get_block_cooldown_seconds(reset_keys)

    now = timezone.now()
    max_resets = int(getattr(settings, "UNIT_RESET_MAX_RESETS_PER_UNIT", 0) or 0)

    with transaction.atomic():
        record, _ = UnitResetControl.objects.select_for_update().get_or_create(
            user=request.user,
            course_id=str(course_key),
            unit_usage_key=str(unit_key),
            defaults={"cooldown_seconds": cooldown_seconds},
        )

        latest_attempt_at = None
        if getattr(settings, "UNIT_RESET_USE_LATEST_STUDENTMODULE_MODIFIED_AS_ATTEMPT_TIME", True):
            latest_attempt_at = get_latest_studentmodule_modified(request.user, course_key, reset_keys)

        if latest_attempt_at and (not record.last_attempt_at or latest_attempt_at > record.last_attempt_at):
            record.last_attempt_at = latest_attempt_at

        baseline = max([d for d in (record.last_reset_at, record.last_attempt_at) if d], default=None)
        computed_next_allowed_at = record.next_reset_allowed_at
        if baseline:
            candidate = baseline + timedelta(seconds=cooldown_seconds)
            if not computed_next_allowed_at or candidate > computed_next_allowed_at:
                computed_next_allowed_at = candidate

        if max_resets > 0 and record.reset_count >= max_resets:
            raise ResetLimitExceededError("Bạn đã vượt quá số lần làm lại cho Unit này.")

        if getattr(settings, "UNIT_RESET_REQUIRE_COOLDOWN", True):
            if computed_next_allowed_at and now < computed_next_allowed_at:
                wait_seconds = int((computed_next_allowed_at - now).total_seconds())
                record.cooldown_seconds = cooldown_seconds
                record.next_reset_allowed_at = computed_next_allowed_at
                record.last_ip = get_client_ip(request)
                record.last_user_agent = (request.META.get("HTTP_USER_AGENT", "") or "")[:2000]
                record.save(update_fields=[
                    "cooldown_seconds",
                    "next_reset_allowed_at",
                    "last_attempt_at",
                    "last_ip",
                    "last_user_agent",
                    "updated_at",
                ])
                raise ResetCooldownError(wait_seconds, computed_next_allowed_at, record.reset_count, cooldown_seconds)

        StudentModule = get_student_module_model()
        deleted_count, _ = StudentModule.objects.filter(
            student=request.user,
            course_id=course_key,
            module_state_key__in=list(reset_keys),
        ).delete()

        grade_cache_deleted = clear_user_grade_cache(request.user, course_key)

        record.reset_count += 1
        record.last_reset_at = now
        record.cooldown_seconds = cooldown_seconds
        record.next_reset_allowed_at = now + timedelta(seconds=cooldown_seconds)
        record.last_ip = get_client_ip(request)
        record.last_user_agent = (request.META.get("HTTP_USER_AGENT", "") or "")[:2000]
        record.save()

    audit_reset(
        request,
        course_key,
        unit_key,
        success=True,
        code="RESET_OK",
        message="Đã reset Unit. Hệ thống sẽ random lại bộ câu hỏi mới.",
        cooldown_seconds=cooldown_seconds,
        deleted_count=deleted_count,
        reset_keys_count=len(reset_keys),
    )

    log.warning(
        "Unit reset OK user_id=%s course_id=%s unit=%s deleted=%s grade_cache_deleted=%s reset_keys=%s cooldown=%s reset_count=%s",
        request.user.id,
        course_key,
        unit_key,
        deleted_count,
        grade_cache_deleted,
        len(reset_keys),
        cooldown_seconds,
        record.reset_count,
    )

    return {
        "success": True,
        "code": "RESET_OK",
        "message": "Đã reset Unit. Hệ thống sẽ random lại bộ câu hỏi mới.",
        "deleted_count": deleted_count,
        "grade_cache_deleted": grade_cache_deleted,
        "reset_keys_count": len(reset_keys),
        "cooldown_seconds": cooldown_seconds,
        "next_reset_allowed_at": record.next_reset_allowed_at.isoformat(),
        "reset_count": record.reset_count,
    }



# ---------------------------------------------------------------------------
# Custom timed practice quiz service
# ---------------------------------------------------------------------------

def _iso(dt):
    return dt.isoformat() if dt else None


def _seconds_left(target_dt):
    if not target_dt:
        return 0
    return max(int((target_dt - timezone.now()).total_seconds()), 0)


def _serialize_timer_config(config):
    return {
        'id': config.id,
        'course_id': config.course_id,
        'sequence_usage_key': config.sequence_usage_key,
        'unit_usage_key': config.unit_usage_key,
        'title': config.title,
        'enabled': config.enabled,
        'duration_seconds': config.duration_seconds,
        'cooldown_seconds': config.cooldown_seconds,
        'auto_submit_on_timeout': config.auto_submit_on_timeout,
        'lock_after_timeout': config.lock_after_timeout,
        'plugin_version': getattr(settings, 'OPENEDX_UNIT_RESET_PLUGIN_VERSION', '0.4.14'),
        'native_timed_exam': config.native_timed_exam,
        'metadata_json': config.metadata_json or {},
        'created_at': _iso(config.created_at),
        'updated_at': _iso(config.updated_at),
    }


def _serialize_quiz_session(session, config=None):
    now = timezone.now()
    config = config or session.config
    remaining_seconds = max(int((session.expires_at - now).total_seconds()), 0)
    reset_wait_seconds = _seconds_left(session.reset_available_at)
    status = session.status
    if status == UnitQuizSession.STATUS_ACTIVE and remaining_seconds <= 0:
        status = UnitQuizSession.STATUS_EXPIRED
    if status == UnitQuizSession.STATUS_EXPIRED and session.reset_available_at and now >= session.reset_available_at:
        status = UnitQuizSession.STATUS_RESET_READY
    return {
        'id': session.id,
        'config': _serialize_timer_config(config),
        'course_id': session.course_id,
        'sequence_usage_key': session.sequence_usage_key,
        'unit_usage_key': session.unit_usage_key,
        'attempt_no': session.attempt_no,
        'duration_seconds': session.duration_seconds,
        'cooldown_seconds': session.cooldown_seconds,
        'started_at': _iso(session.started_at),
        'expires_at': _iso(session.expires_at),
        'status': status,
        'stored_status': session.status,
        'remaining_seconds': remaining_seconds,
        'auto_submitted_at': _iso(session.auto_submitted_at),
        'locked_at': _iso(session.locked_at),
        'reset_available_at': _iso(session.reset_available_at),
        'reset_wait_seconds': reset_wait_seconds,
        'can_reset': status == UnitQuizSession.STATUS_RESET_READY or (session.reset_available_at and now >= session.reset_available_at),
        'auto_submit_on_timeout': config.auto_submit_on_timeout,
        'lock_after_timeout': config.lock_after_timeout,
        'message': _quiz_session_message(status, remaining_seconds, reset_wait_seconds),
    }


def _quiz_session_message(status, remaining_seconds, reset_wait_seconds):
    if status == UnitQuizSession.STATUS_ACTIVE:
        return f'Còn {remaining_seconds} giây để làm bài.'
    if status == UnitQuizSession.STATUS_SUBMITTING:
        return 'Đã hết giờ. Hệ thống đang tự nộp các câu đã chọn.'
    if status == UnitQuizSession.STATUS_RESET_READY:
        return 'Bạn có thể làm lại bài.'
    if reset_wait_seconds > 0:
        return f'Đã hết giờ. Bạn cần chờ {reset_wait_seconds} giây để làm lại bài.'
    return 'Đã hết giờ. Hệ thống đã khóa lượt làm này.'


def upsert_unit_quiz_timer_config(
    *,
    course_id,
    unit_usage_key,
    sequence_usage_key='',
    title='Quiz tự luyện',
    duration_seconds=None,
    cooldown_seconds=None,
    enabled=True,
    auto_submit_on_timeout=True,
    lock_after_timeout=True,
    native_timed_exam=False,
    actor='',
    metadata_json=None,
):
    """Create/update timer config for a Unit.

    This can be called from the AI connector running in CMS after it creates the
    Unit. The config is later used by LMS APIs and middleware.
    """
    if not course_id or not unit_usage_key:
        raise UnitResetError('Thiếu course_id hoặc unit_usage_key để lưu timer.', 'MISSING_TIMER_CONFIG_FIELDS', 400)
    duration_seconds = int(duration_seconds or getattr(settings, 'UNIT_RESET_QUIZ_TIMER_DEFAULT_DURATION_SECONDS', 900))
    cooldown_seconds = int(cooldown_seconds if cooldown_seconds is not None else getattr(settings, 'UNIT_RESET_QUIZ_TIMER_DEFAULT_COOLDOWN_SECONDS', 300))
    if duration_seconds <= 0:
        raise UnitResetError('Thời gian làm bài phải lớn hơn 0 giây.', 'INVALID_DURATION_SECONDS', 400)
    if cooldown_seconds < 0:
        raise UnitResetError('Thời gian chờ làm lại không hợp lệ.', 'INVALID_COOLDOWN_SECONDS', 400)

    config, created = UnitQuizTimerConfig.objects.update_or_create(
        course_id=str(course_id),
        unit_usage_key=str(unit_usage_key),
        defaults={
            'sequence_usage_key': str(sequence_usage_key or ''),
            'title': str(title or 'Quiz tự luyện')[:255],
            'enabled': bool(enabled),
            'duration_seconds': duration_seconds,
            'cooldown_seconds': cooldown_seconds,
            'auto_submit_on_timeout': bool(auto_submit_on_timeout),
            'lock_after_timeout': bool(lock_after_timeout),
            'native_timed_exam': bool(native_timed_exam),
            'updated_by': str(actor or '')[:255],
            'metadata_json': metadata_json or {},
        },
    )
    if created and actor:
        config.created_by = str(actor)[:255]
        config.save(update_fields=['created_by'])
    return {'success': True, 'created': created, 'config': _serialize_timer_config(config)}


def _get_timer_config(course_id, unit_usage_key):
    try:
        return UnitQuizTimerConfig.objects.get(course_id=str(course_id), unit_usage_key=str(unit_usage_key), enabled=True)
    except UnitQuizTimerConfig.DoesNotExist as exc:
        raise UnitResetError('Unit này chưa được cấu hình thời gian làm quiz.', 'QUIZ_TIMER_CONFIG_NOT_FOUND', 404) from exc


def _latest_quiz_session(user, config):
    return UnitQuizSession.objects.filter(user=user, config=config).order_by('-attempt_no', '-created_at').first()


def _update_expired_session(session):
    if session.status == UnitQuizSession.STATUS_ACTIVE and timezone.now() >= session.expires_at:
        session.status = UnitQuizSession.STATUS_EXPIRED
        if not session.locked_at:
            session.locked_at = session.expires_at
        if not session.reset_available_at:
            session.reset_available_at = session.expires_at + timedelta(seconds=session.cooldown_seconds)
        session.save(update_fields=['status', 'locked_at', 'reset_available_at', 'updated_at'])
    if session.status == UnitQuizSession.STATUS_EXPIRED and session.reset_available_at and timezone.now() >= session.reset_available_at:
        session.status = UnitQuizSession.STATUS_RESET_READY
        session.save(update_fields=['status', 'updated_at'])
    return session


def get_quiz_session_status_for_current_user(request, course_id, unit_usage_key):
    course_key, unit_key = parse_keys(course_id, unit_usage_key)
    assert_user_can_reset(request, course_key)
    config = _get_timer_config(str(course_key), str(unit_key))
    session = _latest_quiz_session(request.user, config)
    if not session:
        return {
            'success': True,
            'has_session': False,
            'config': _serialize_timer_config(config),
            'status': 'NOT_STARTED',
            'message': 'Chưa bắt đầu lượt làm quiz.',
        }
    session = _update_expired_session(session)
    data = _serialize_quiz_session(session, config)
    data['success'] = True
    data['has_session'] = True
    return data


def start_quiz_session_for_current_user(request, course_id, unit_usage_key):
    course_key, unit_key = parse_keys(course_id, unit_usage_key)
    assert_user_can_reset(request, course_key)
    config = _get_timer_config(str(course_key), str(unit_key))
    now = timezone.now()
    with transaction.atomic():
        session = UnitQuizSession.objects.select_for_update().filter(user=request.user, config=config).order_by('-attempt_no', '-created_at').first()
        if session:
            session = _update_expired_session(session)
            if session.status in (UnitQuizSession.STATUS_ACTIVE, UnitQuizSession.STATUS_SUBMITTING):
                data = _serialize_quiz_session(session, config)
                data['success'] = True
                data['has_session'] = True
                return data
            if session.reset_available_at and now < session.reset_available_at:
                wait = int((session.reset_available_at - now).total_seconds())
                raise ResetCooldownError(wait, session.reset_available_at, session.attempt_no, session.cooldown_seconds)
            attempt_no = session.attempt_no + 1
        else:
            attempt_no = 1
        new_session = UnitQuizSession.objects.create(
            user=request.user,
            config=config,
            course_id=str(course_key),
            sequence_usage_key=config.sequence_usage_key,
            unit_usage_key=str(unit_key),
            attempt_no=attempt_no,
            duration_seconds=config.duration_seconds,
            cooldown_seconds=config.cooldown_seconds,
            started_at=now,
            expires_at=now + timedelta(seconds=config.duration_seconds),
            status=UnitQuizSession.STATUS_ACTIVE,
            last_ip=get_client_ip(request),
            last_user_agent=(request.META.get('HTTP_USER_AGENT', '') or '')[:2000],
        )
    audit_reset(request, course_key, unit_key, action='quiz_session_start', success=True, code='QUIZ_SESSION_STARTED', message='Bắt đầu lượt làm quiz có thời gian.', cooldown_seconds=config.cooldown_seconds)
    data = _serialize_quiz_session(new_session, config)
    data['success'] = True
    data['has_session'] = True
    return data


def timeout_quiz_session_for_current_user(request, course_id, unit_usage_key, payload=None):
    course_key, unit_key = parse_keys(course_id, unit_usage_key)
    assert_user_can_reset(request, course_key)
    config = _get_timer_config(str(course_key), str(unit_key))
    now = timezone.now()
    with transaction.atomic():
        session = UnitQuizSession.objects.select_for_update().filter(user=request.user, config=config).order_by('-attempt_no', '-created_at').first()
        if not session:
            raise UnitResetError('Chưa có lượt làm quiz để timeout.', 'QUIZ_SESSION_NOT_STARTED', 400)
        if session.status not in (UnitQuizSession.STATUS_ACTIVE, UnitQuizSession.STATUS_SUBMITTING):
            data = _serialize_quiz_session(session, config)
            data['success'] = True
            return data
        session.status = UnitQuizSession.STATUS_SUBMITTING
        session.auto_submitted_at = now
        # v0.4.13/v0.4.14: reset cooldown is computed from expires_at, never from lock_at.
        if not session.reset_available_at:
            session.reset_available_at = session.expires_at + timedelta(seconds=session.cooldown_seconds)
        session.timeout_payload = payload or {}
        session.last_ip = get_client_ip(request)
        session.last_user_agent = (request.META.get('HTTP_USER_AGENT', '') or '')[:2000]
        session.save(update_fields=['status', 'auto_submitted_at', 'reset_available_at', 'timeout_payload', 'last_ip', 'last_user_agent', 'updated_at'])
    audit_reset(request, course_key, unit_key, action='quiz_session_timeout', success=True, code='QUIZ_TIMEOUT', message='Hết giờ, bắt đầu tự nộp các câu đã chọn.', cooldown_seconds=config.cooldown_seconds)
    data = _serialize_quiz_session(session, config)
    data['success'] = True
    return data


def lock_quiz_session_for_current_user(request, course_id, unit_usage_key, payload=None):
    course_key, unit_key = parse_keys(course_id, unit_usage_key)
    assert_user_can_reset(request, course_key)
    config = _get_timer_config(str(course_key), str(unit_key))
    now = timezone.now()
    with transaction.atomic():
        session = UnitQuizSession.objects.select_for_update().filter(user=request.user, config=config).order_by('-attempt_no', '-created_at').first()
        if not session:
            raise UnitResetError('Chưa có lượt làm quiz để khóa.', 'QUIZ_SESSION_NOT_STARTED', 400)
        session.status = UnitQuizSession.STATUS_EXPIRED
        if not session.auto_submitted_at:
            session.auto_submitted_at = now
        session.locked_at = now
        # v0.4.13/v0.4.14 rule: cooldown must be based on quiz expiry, not lock time.
        # If auto-submit/lock processing is slow, reset_available_at must remain stable.
        expiry_base = session.expires_at or now
        session.reset_available_at = expiry_base + timedelta(seconds=session.cooldown_seconds)
        merged = session.timeout_payload or {}
        merged.update(payload or {})
        session.timeout_payload = merged
        session.last_ip = get_client_ip(request)
        session.last_user_agent = (request.META.get('HTTP_USER_AGENT', '') or '')[:2000]
        session.save(update_fields=['status', 'auto_submitted_at', 'locked_at', 'reset_available_at', 'timeout_payload', 'last_ip', 'last_user_agent', 'updated_at'])
    audit_reset(request, course_key, unit_key, action='quiz_session_lock', success=True, code='QUIZ_LOCKED', message='Đã khóa lượt làm quiz sau khi hết giờ.', cooldown_seconds=config.cooldown_seconds)
    data = _serialize_quiz_session(session, config)
    data['success'] = True
    return data


def reset_quiz_session_for_current_user(request, course_id, unit_usage_key):
    course_key, unit_key = parse_keys(course_id, unit_usage_key)
    assert_user_can_reset(request, course_key)
    config = _get_timer_config(str(course_key), str(unit_key))
    latest = _latest_quiz_session(request.user, config)
    if latest:
        latest = _update_expired_session(latest)
        if latest.reset_available_at and timezone.now() < latest.reset_available_at:
            wait = int((latest.reset_available_at - timezone.now()).total_seconds())
            raise ResetCooldownError(wait, latest.reset_available_at, latest.attempt_no, latest.cooldown_seconds)
    reset_result = reset_unit_for_current_user(request, str(course_key), str(unit_key))
    start_result = start_quiz_session_for_current_user(request, str(course_key), str(unit_key))
    start_result['reset_result'] = reset_result
    start_result['message'] = 'Đã làm lại bài. Hệ thống đã random lại câu hỏi và bắt đầu lượt mới.'
    return start_result


def is_late_submit_blocked(user, course_id, problem_usage_key):
    """Best-effort server-side guard for problem_check after timer expired.

    Returns (blocked, session). This intentionally errs on the side of allowing
    submissions when it cannot prove the problem belongs to an expired timed Unit.
    """
    if not getattr(settings, 'UNIT_RESET_QUIZ_TIMER_SERVER_GUARD_ENABLED', True):
        return False, None
    if not user or not user.is_authenticated or not course_id or not problem_usage_key:
        return False, None
    expired = UnitQuizSession.objects.filter(
        user=user,
        course_id=str(course_id),
        status__in=[UnitQuizSession.STATUS_EXPIRED, UnitQuizSession.STATUS_RESET_WAIT, UnitQuizSession.STATUS_RESET_READY],
    ).select_related('config').order_by('-created_at')[:10]
    if not expired:
        return False, None
    try:
        problem_key = UsageKey.from_string(str(problem_usage_key))
    except Exception:
        problem_key = None
    for session in expired:
        try:
            unit_key = UsageKey.from_string(session.unit_usage_key)
            reset_keys = collect_unit_usage_keys(unit_key)
            reset_keys = expand_randomized_selected_keys(user, CourseKey.from_string(str(course_id)), reset_keys)
            if problem_key is not None and problem_key in reset_keys:
                return True, session
            if str(problem_usage_key) in {str(k) for k in reset_keys}:
                return True, session
        except Exception:
            log.exception('Could not evaluate timed quiz submit guard')
            continue
    return False, None
