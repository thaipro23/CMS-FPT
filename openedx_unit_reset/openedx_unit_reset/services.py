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


# ---------------------------------------------------------------------------
# Open edX submissions cleanup
# ---------------------------------------------------------------------------

def get_submissions_models():
    """Import edx-submissions models lazily.

    CAPA/problem state can survive a StudentModule delete because graded answer
    submissions and scores are stored in the edx-submissions app.  The reset
    operation must remove those rows for the current learner + course + problem
    usage keys so a retake renders like a fresh attempt, without green feedback
    or a previously revealed answer.
    """
    try:
        from submissions.models import StudentItem, Submission, Score
        return StudentItem, Submission, Score
    except Exception as exc:  # pragma: no cover - depends on edx-platform install
        log.warning("Could not import edx-submissions models; reset will continue without submissions cleanup", exc_info=True)
        return None, None, None


def _field_names(model):
    try:
        return {field.name for field in model._meta.get_fields()}
    except Exception:
        return set()


def get_anonymous_student_ids(user, course_key):
    """Return possible submissions.StudentItem.student_id values for this user.

    Open edX submissions normally uses the course-scoped anonymous id, not
    auth_user.id.  Different releases expose anonymous_id_for_user from slightly
    different modules/signatures, so this is intentionally defensive.
    """
    values = []

    for module_path in ("common.djangoapps.student.models", "student.models"):
        try:
            module = __import__(module_path, fromlist=["anonymous_id_for_user"])
            fn = getattr(module, "anonymous_id_for_user", None)
            if not fn:
                continue
            for args in ((user, course_key), (user, str(course_key)), (user.id, course_key), (user.id, str(course_key)), (user,)):
                try:
                    value = fn(*args)
                except TypeError:
                    continue
                except Exception:
                    continue
                if value:
                    values.append(str(value))
        except Exception:
            continue

    # Conservative fallbacks for non-standard deployments.  They are only used
    # together with exact course_id + item_id filters.
    for value in (
        getattr(user, "anonymous_id", None),
        getattr(user, "username", None),
        getattr(user, "email", None),
        str(getattr(user, "id", "") or ""),
    ):
        if value:
            values.append(str(value))

    deduped = []
    seen = set()
    for value in values:
        if value and value not in seen:
            seen.add(value)
            deduped.append(value)
    return deduped


def clear_user_submissions(user, course_key, reset_keys):
    """Delete edx-submissions rows for this learner and the reset problem keys.

    Returns counts and diagnostics.  If submissions models are unavailable the
    reset continues because StudentModule is still the primary state store, but
    the caller receives a clear diagnostic.
    """
    result = {
        "student_items": 0,
        "submissions": 0,
        "scores": 0,
        "student_id_candidates": [],
        "skipped": False,
        "message": "",
    }

    StudentItem, Submission, Score = get_submissions_models()
    if not StudentItem or not Submission or not Score:
        result["skipped"] = True
        result["message"] = "Không import được submissions.models; chỉ xóa StudentModule/grade cache."
        return result

    item_ids = [str(key) for key in reset_keys]
    if not item_ids:
        result["skipped"] = True
        result["message"] = "Không có problem usage key để xóa submissions."
        return result

    student_ids = get_anonymous_student_ids(user, course_key)
    result["student_id_candidates"] = student_ids
    if not student_ids:
        result["skipped"] = True
        result["message"] = "Không tìm được anonymous student_id để xóa submissions an toàn."
        return result

    try:
        item_fields = _field_names(StudentItem)
        filters = {}
        if "course_id" in item_fields:
            filters["course_id"] = str(course_key)
        if "item_id" in item_fields:
            filters["item_id__in"] = item_ids
        elif "item" in item_fields:
            filters["item__in"] = item_ids
        else:
            result["skipped"] = True
            result["message"] = "StudentItem không có item_id/item field phù hợp."
            return result
        if "student_id" in item_fields:
            filters["student_id__in"] = student_ids
        elif "student" in item_fields:
            filters["student__in"] = student_ids
        else:
            result["skipped"] = True
            result["message"] = "StudentItem không có student_id/student field phù hợp."
            return result

        student_items = StudentItem.objects.filter(**filters)
        student_item_ids = list(student_items.values_list("id", flat=True))
        if not student_item_ids:
            result["message"] = "Không tìm thấy StudentItem tương ứng để xóa."
            return result

        # Delete Score first, then Submission, then StudentItem.  Try the common
        # relationship paths used by edx-submissions across releases.
        score_deleted = 0
        for kwargs in (
            {"submission__student_item_id__in": student_item_ids},
            {"student_item_id__in": student_item_ids},
        ):
            try:
                count, _ = Score.objects.filter(**kwargs).delete()
                score_deleted += count
                break
            except Exception:
                continue
        result["scores"] = score_deleted

        submission_deleted = 0
        for kwargs in (
            {"student_item_id__in": student_item_ids},
            {"student_item__id__in": student_item_ids},
        ):
            try:
                count, _ = Submission.objects.filter(**kwargs).delete()
                submission_deleted += count
                break
            except Exception:
                continue
        result["submissions"] = submission_deleted

        item_deleted, _ = StudentItem.objects.filter(id__in=student_item_ids).delete()
        result["student_items"] = item_deleted
        result["message"] = "Đã xóa submissions/score của learner cho các problem trong Unit."
        return result
    except Exception:
        log.exception("Could not delete edx-submissions rows during unit reset user_id=%s course_id=%s", user.id, course_key)
        result["skipped"] = True
        result["message"] = "Lỗi khi xóa submissions; xem LMS log để biết chi tiết."
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


def reset_unit_for_current_user(request, course_id, unit_usage_key, cooldown_seconds_override=None, bypass_cooldown=False):
    course_key, unit_key = parse_keys(course_id, unit_usage_key)
    assert_user_can_reset(request, course_key)

    base_keys = collect_unit_usage_keys(unit_key)
    reset_keys = expand_randomized_selected_keys(request.user, course_key, base_keys)
    if cooldown_seconds_override is None:
        cooldown_seconds = get_block_cooldown_seconds(reset_keys)
    else:
        cooldown_seconds = int(cooldown_seconds_override)

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

        if getattr(settings, "UNIT_RESET_REQUIRE_COOLDOWN", True) and not bypass_cooldown:
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
        submissions_deleted = clear_user_submissions(request.user, course_key, reset_keys)

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
        "Unit reset OK user_id=%s course_id=%s unit=%s deleted=%s grade_cache_deleted=%s submissions_deleted=%s reset_keys=%s cooldown=%s reset_count=%s",
        request.user.id,
        course_key,
        unit_key,
        deleted_count,
        grade_cache_deleted,
        submissions_deleted,
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
        "submissions_deleted": submissions_deleted,
        "reset_keys_count": len(reset_keys),
        "reload_required": True,
        "reload_unit": True,
        "force_problem_reload": True,
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


def _reset_available_at_from_quiz_expiry(session, fallback_now=None):
    """Return the canonical retake time for a timed quiz attempt.

    The retake cooldown is configured on UnitQuizTimerConfig / UnitQuizSession.
    Its base time must be the planned quiz expiry, not the later server lock
    timestamp. Otherwise auto-submit grace windows or delayed lock calls add
    hidden extra wait time for learners.
    """
    base_time = session.expires_at or fallback_now or timezone.now()
    return base_time + timedelta(seconds=int(session.cooldown_seconds or 0))


def _set_reset_available_at_from_expiry(session, fallback_now=None):
    """Set reset_available_at from expires_at + cooldown if it is missing.

    Do not overwrite an existing value because earlier timeout/status handling may
    already have committed the correct canonical cooldown boundary.
    """
    if not session.reset_available_at:
        session.reset_available_at = _reset_available_at_from_quiz_expiry(session, fallback_now=fallback_now)
        return True
    return False


def _auto_submit_grace_seconds():
    """Server-side grace window for the automatic timeout submit.

    The browser can only start Open edX problem_check API requests when the
    countdown reaches zero. Those individual problem_check requests may arrive
    a few seconds after expires_at. Without a grace window the submit guard
    blocks the system's own auto-submit.
    """
    try:
        return max(int(getattr(settings, 'UNIT_RESET_QUIZ_AUTOSUBMIT_GRACE_SECONDS', 60) or 60), 0)
    except Exception:
        return 60


def _session_allows_auto_submit_grace(session, now=None):
    """Return True while the system auto-submit is still allowed to finish.

    This is intentionally short-lived and only applies before the final
    quiz-session/lock call marks auto_submit_done. It prevents the server-side
    submit guard from blocking problem_check requests produced by runtime.js.
    """
    now = now or timezone.now()
    grace_seconds = _auto_submit_grace_seconds()
    if grace_seconds <= 0:
        return False

    payload = session.timeout_payload or {}
    if payload.get('auto_submit_done') is True:
        return False

    # SUBMITTING means quiz-session/timeout already ran and runtime.js is
    # submitting selected answers through problem_check APIs. Allow it only during
    # the configured grace window; never allow a stuck SUBMITTING session to
    # accept problem_check forever.
    if session.status == UnitQuizSession.STATUS_SUBMITTING:
        grace_base = session.auto_submitted_at or session.expires_at
        if not grace_base:
            return False
        return now <= grace_base + timedelta(seconds=grace_seconds)

    if session.status != UnitQuizSession.STATUS_EXPIRED:
        return False

    grace_base = session.auto_submitted_at or session.expires_at
    if not grace_base:
        return False
    return now <= grace_base + timedelta(seconds=grace_seconds)


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
        'auto_submit_on_timeout': True,
        'lock_after_timeout': bool(config.lock_after_timeout),
        'stored_lock_after_timeout': config.lock_after_timeout,
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
        'server_now': _iso(now),
        'can_reset': status == UnitQuizSession.STATUS_RESET_READY or (session.reset_available_at and now >= session.reset_available_at),
        'auto_submit_grace_seconds': _auto_submit_grace_seconds(),
        'auto_submit_grace_active': _session_allows_auto_submit_grace(session, now),
        'auto_submit_on_timeout': True,
        'lock_after_timeout': bool(config.lock_after_timeout),
        'stored_lock_after_timeout': config.lock_after_timeout,
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
    return 'Đã hết giờ. Bạn có thể làm lại bài.'


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
            'auto_submit_on_timeout': True if enabled else bool(auto_submit_on_timeout),
            'lock_after_timeout': True if enabled else bool(lock_after_timeout),
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


def get_timer_config_or_none(course_id, unit_usage_key):
    try:
        return UnitQuizTimerConfig.objects.get(course_id=str(course_id), unit_usage_key=str(unit_usage_key), enabled=True)
    except UnitQuizTimerConfig.DoesNotExist:
        return None


def get_legacy_compatible_timer_status_for_current_user(request, course_id, unit_usage_key):
    """Return timer status using the old reset/status response shape.

    This prevents the old reset UI/endpoint from showing the legacy block
    cooldown (for example 10 minutes) when a Unit is controlled by the custom
    timed-practice quiz config (for example 3 or 4 minutes).
    """
    data = get_quiz_session_status_for_current_user(request, course_id, unit_usage_key)
    config = data.get('config') or {}
    wait_seconds = int(data.get('reset_wait_seconds') or data.get('wait_seconds') or 0)
    can_reset = bool(data.get('can_reset', wait_seconds <= 0 and data.get('status') in ('RESET_READY', 'NOT_STARTED')))
    return {
        'success': True,
        'timer_managed': True,
        'can_reset': can_reset,
        'wait_seconds': max(wait_seconds, 0),
        'cooldown_seconds': int(config.get('cooldown_seconds') or data.get('cooldown_seconds') or 0),
        'reset_count': int(data.get('attempt_no') or 0),
        'last_attempt_at': data.get('started_at'),
        'last_reset_at': None,
        'next_reset_allowed_at': data.get('reset_available_at'),
        'reset_keys_count': 0,
        'quiz_session': data,
    }


def _latest_quiz_session(user, config):
    return UnitQuizSession.objects.filter(user=user, config=config).order_by('-attempt_no', '-created_at').first()


def _update_expired_session(session):
    now = timezone.now()
    if session.status == UnitQuizSession.STATUS_ACTIVE and now >= session.expires_at:
        session.status = UnitQuizSession.STATUS_EXPIRED
        # Cooldown is defined by quiz config and starts from the scheduled
        # quiz expiry, not from the later lock/status request timestamp.
        _set_reset_available_at_from_expiry(session, fallback_now=now)
        session.save(update_fields=['status', 'reset_available_at', 'updated_at'])
    if session.status == UnitQuizSession.STATUS_SUBMITTING and not _session_allows_auto_submit_grace(session, now):
        # Recovery for a browser/tab that started auto-submit but never called
        # quiz-session/lock. After the grace window, stop allowing problem_check
        # and move the attempt into the normal expired cooldown path.
        session.status = UnitQuizSession.STATUS_EXPIRED
        if not session.locked_at:
            session.locked_at = now
        # Even when a stuck SUBMITTING session is finalized after the grace
        # window, the retake clock remains expires_at + cooldown_seconds. Do not
        # add grace/lock delay to the learner's cooldown.
        _set_reset_available_at_from_expiry(session, fallback_now=now)
        session.save(update_fields=['status', 'locked_at', 'reset_available_at', 'updated_at'])
    if session.status == UnitQuizSession.STATUS_EXPIRED and session.reset_available_at and now >= session.reset_available_at:
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
            'server_now': _iso(timezone.now()),
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
            # Important: page refresh must not silently create a new timed
            # attempt. A new attempt is allowed only through quiz-session/reset,
            # because reset must clear StudentModule/randomized state first.
            if session.status in (UnitQuizSession.STATUS_EXPIRED, UnitQuizSession.STATUS_RESET_WAIT, UnitQuizSession.STATUS_RESET_READY):
                data = _serialize_quiz_session(session, config)
                data['success'] = True
                data['has_session'] = True
                data['requires_reset'] = True
                data['message'] = 'Lượt làm đã kết thúc. Bấm Làm lại bài sau khi hết thời gian chờ để random lại câu.'
                return data
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
            # Race-safe path: if status polling marked the attempt EXPIRED just
            # before the browser could call /timeout, reopen it briefly as
            # SUBMITTING so the system auto-submit can finish.
            if not _session_allows_auto_submit_grace(session, now):
                data = _serialize_quiz_session(session, config)
                data['success'] = True
                return data
        session.status = UnitQuizSession.STATUS_SUBMITTING
        session.auto_submitted_at = session.auto_submitted_at or now
        # Chốt thời điểm được làm lại ngay khi timeout, lấy từ quiz expiry +
        # cooldown config. Không lấy từ lock time để tránh cộng thêm grace delay.
        _set_reset_available_at_from_expiry(session, fallback_now=now)
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

        # v0.4.13 policy: keep v0.4.12 all-at-once auto-submit, but never finalize-lock the
        # attempt from this endpoint. This avoids the race where /lock arrives
        # before the last Open edX problem_check request has finished.
        merged = session.timeout_payload or {}
        merged.update(payload or {})
        merged['lock_call_skipped_no_lock_policy'] = True
        merged['lock_call_skipped_at'] = _iso(now)
        merged['cooldown_base'] = 'expires_at'
        session.timeout_payload = merged
        # After v0.4.15 API auto-submit finishes, it is safe to close the
        # SUBMITTING grace window. We still do not depend on DOM button state or
        # native Timed Exam; this only marks the server attempt as expired so late
        # manual submits are rejected and cooldown UI is stable.
        finalize_after_api_submit = bool((payload or {}).get('auto_submit_done'))
        if finalize_after_api_submit and session.status == UnitQuizSession.STATUS_SUBMITTING:
            session.status = UnitQuizSession.STATUS_EXPIRED
            session.locked_at = session.locked_at or now
        # Lock is skipped in v0.4.12+/v0.4.13 policy, but if an old MFE still
        # calls this endpoint before timeout/status has set reset_available_at,
        # set it from quiz expiry. Never overwrite an existing value.
        reset_changed = _set_reset_available_at_from_expiry(session, fallback_now=now)
        session.last_ip = get_client_ip(request)
        session.last_user_agent = (request.META.get('HTTP_USER_AGENT', '') or '')[:2000]
        update_fields = ['timeout_payload', 'last_ip', 'last_user_agent', 'updated_at']
        if finalize_after_api_submit and session.status == UnitQuizSession.STATUS_EXPIRED:
            update_fields.extend(['status', 'locked_at'])
        if reset_changed:
            update_fields.append('reset_available_at')
        session.save(update_fields=update_fields)

    audit_reset(request, course_key, unit_key, action='quiz_session_lock_skipped', success=True, code='QUIZ_LOCK_SKIPPED_NO_LOCK_POLICY', message='Đã bỏ qua khóa ngay sau auto-submit theo chính sách v0.4.13.', cooldown_seconds=config.cooldown_seconds)
    data = _serialize_quiz_session(session, config)
    data['success'] = True
    data['lock_skipped'] = True
    data['code'] = 'QUIZ_LOCK_SKIPPED_NO_LOCK_POLICY'
    data['message'] = 'Đã nhận tín hiệu hết giờ/tự nộp, nhưng không khóa ngay theo chính sách v0.4.13.'
    return data


def reset_quiz_session_for_current_user(request, course_id, unit_usage_key):
    course_key, unit_key = parse_keys(course_id, unit_usage_key)
    assert_user_can_reset(request, course_key)
    config = _get_timer_config(str(course_key), str(unit_key))
    latest = _latest_quiz_session(request.user, config)
    now = timezone.now()

    if latest:
        latest = _update_expired_session(latest)
        if latest.status in (UnitQuizSession.STATUS_ACTIVE, UnitQuizSession.STATUS_SUBMITTING):
            next_allowed = latest.reset_available_at or _reset_available_at_from_quiz_expiry(latest, fallback_now=now)
            wait = max(int((next_allowed - now).total_seconds()), 0)
            if wait > 0:
                raise ResetCooldownError(wait, next_allowed, latest.attempt_no, latest.cooldown_seconds)
            # Race-safe cleanup: if the browser/user asks to reset exactly at the
            # cooldown boundary, do not return the confusing "chờ 0 giây" error.
            # Mark the old attempt reset-ready and continue with the reset below.
            latest.status = UnitQuizSession.STATUS_RESET_READY
            latest.reset_available_at = latest.reset_available_at or now
            latest.save(update_fields=['status', 'reset_available_at', 'updated_at'])
        if latest.reset_available_at and now < latest.reset_available_at:
            wait = max(int((latest.reset_available_at - now).total_seconds()), 1)
            raise ResetCooldownError(wait, latest.reset_available_at, latest.attempt_no, latest.cooldown_seconds)

    reset_result = reset_unit_for_current_user(
        request,
        str(course_key),
        str(unit_key),
        cooldown_seconds_override=config.cooldown_seconds,
        bypass_cooldown=True,
    )

    # Mark older timer sessions as superseded/ready so start creates the next
    # attempt after the Unit state has been reset.
    UnitQuizSession.objects.filter(user=request.user, config=config).exclude(status=UnitQuizSession.STATUS_ACTIVE).update(
        status=UnitQuizSession.STATUS_RESET_READY,
        reset_available_at=now,
        updated_at=now,
    )

    # Now create a fresh timed attempt. Because previous sessions are not ACTIVE
    # and their reset_available_at is in the past, this creates attempt_no + 1.
    # Temporarily bypass the refresh guard by creating directly.
    previous = _latest_quiz_session(request.user, config)
    attempt_no = (previous.attempt_no + 1) if previous else 1
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
    audit_reset(request, course_key, unit_key, action='quiz_session_reset', success=True, code='QUIZ_SESSION_RESET', message='Đã reset Unit và bắt đầu lượt quiz mới.', cooldown_seconds=config.cooldown_seconds)
    data = _serialize_quiz_session(new_session, config)
    data['success'] = True
    data['has_session'] = True
    data['reset_result'] = reset_result
    data['reload_required'] = True
    data['reload_unit'] = True
    data['force_problem_reload'] = True
    data['message'] = 'Đã làm lại bài. Hệ thống đã reset sạch trạng thái bài và bắt đầu lượt mới. Vui lòng tải lại Unit nếu giao diện chưa tự cập nhật.'
    return data

def is_late_submit_blocked(user, course_id, problem_usage_key):
    """Best-effort server-side guard for problem_check after timer expired.

    Returns (blocked, session). This intentionally errs on the side of allowing
    submissions when it cannot prove the problem belongs to an expired timed Unit.

    Important reset/timer rule:
    - old expired/reset-ready sessions must not block a newer active attempt.
    - after quiz-session/reset, the previous session stays in history, while a
      fresh ACTIVE session is created for the same Unit. The submit guard must
      evaluate the latest attempt for that timer config, not merely any expired
      session in the course.
    """
    if not getattr(settings, 'UNIT_RESET_QUIZ_TIMER_SERVER_GUARD_ENABLED', True):
        return False, None
    if not user or not user.is_authenticated or not course_id or not problem_usage_key:
        return False, None

    guarded_sessions = UnitQuizSession.objects.filter(
        user=user,
        course_id=str(course_id),
        status__in=[
            UnitQuizSession.STATUS_SUBMITTING,
            UnitQuizSession.STATUS_EXPIRED,
            UnitQuizSession.STATUS_RESET_WAIT,
            UnitQuizSession.STATUS_RESET_READY,
        ],
    ).select_related('config').order_by('-created_at')[:20]
    if not guarded_sessions:
        return False, None

    try:
        problem_key = UsageKey.from_string(str(problem_usage_key))
    except Exception:
        problem_key = None

    course_key = None
    try:
        course_key = CourseKey.from_string(str(course_id))
    except Exception:
        course_key = None

    for session in guarded_sessions:
        try:
            # If a newer ACTIVE/SUBMITTING attempt exists for the same timer config,
            # allow the submit. This is the normal state after the learner clicks
            # "Làm lại bài" and the Unit has been randomized again.
            latest_for_config = UnitQuizSession.objects.filter(
                user=user,
                config=session.config,
            ).order_by('-attempt_no', '-created_at').first()
            if latest_for_config and latest_for_config.id != session.id and latest_for_config.status in (
                UnitQuizSession.STATUS_ACTIVE,
                UnitQuizSession.STATUS_SUBMITTING,
            ):
                continue

            unit_key = UsageKey.from_string(session.unit_usage_key)
            reset_keys = collect_unit_usage_keys(unit_key)
            if course_key is not None:
                reset_keys = expand_randomized_selected_keys(user, course_key, reset_keys)
            reset_key_strings = {str(k) for k in reset_keys}
            if problem_key is not None and problem_key in reset_keys:
                if _session_allows_auto_submit_grace(session):
                    log.info('Allowing timed quiz auto-submit during grace window session_id=%s user_id=%s', session.id, user.id)
                    return False, None
                return True, session
            if str(problem_usage_key) in reset_key_strings:
                if _session_allows_auto_submit_grace(session):
                    log.info('Allowing timed quiz auto-submit during grace window session_id=%s user_id=%s', session.id, user.id)
                    return False, None
                return True, session
        except Exception:
            log.exception('Could not evaluate timed quiz submit guard')
            continue
    return False, None
