import json
import logging
from datetime import timedelta

from django.conf import settings
from django.db import transaction
from django.db.models import Max
from django.utils import timezone
from opaque_keys.edx.keys import CourseKey, UsageKey

from .models import (
    UnitQuizSession,
    UnitQuizTimerConfig,
    UnitResetAudit,
    UnitResetControl,
    UnitResetGradeReconciliation,
    UnitResetCourseGradeLock,
)
from .reset_scope import active_child_keys, partition_persisted_reset_keys

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

    if getattr(unit_key, "block_type", None) != "vertical":
        raise UnitResetError(
            "unit_usage_key phải trỏ đúng tới một Unit (vertical).",
            "UNIT_KEY_NOT_VERTICAL",
            400,
        )

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


def _get_cached_modulestore_item(store, key, block_cache=None):
    """Memoize successful XBlock lookups only for the current operation.

    A cached exception could hide a recovered modulestore lookup and change
    reset/submit safety decisions, so only successfully loaded blocks are saved.
    """
    if block_cache is None:
        return store.get_item(key)
    if key not in block_cache:
        block_cache[key] = store.get_item(key)
    return block_cache[key]


def collect_unit_usage_keys(unit_key, *, store=None, block_cache=None):
    """Collect Unit root + descendant keys, optionally reusing a local block cache."""
    if store is None:
        store = get_modulestore()
    keys = set()

    def walk(key):
        if key in keys:
            return
        keys.add(key)
        try:
            block = _get_cached_modulestore_item(store, key, block_cache)
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


def extract_selected_usage_keys_from_state(state_text, course_key):
    """Read only the ItemBank ``selected`` field from learner state.

    Older code recursively scanned every string in the JSON document. That
    could accidentally pull unrelated usage keys into the reset scope. ItemBank
    stores selection as ``[[block_type, block_id], ...]``; no other state field
    is accepted here.
    """
    if not state_text:
        return []
    try:
        data = json.loads(state_text)
    except (TypeError, ValueError):
        return []

    selected = data.get("selected", []) if isinstance(data, dict) else []
    keys = []
    seen = set()
    for entry in selected:
        if not isinstance(entry, (list, tuple)) or len(entry) != 2:
            continue
        block_type, block_id = entry
        if not isinstance(block_type, str) or not isinstance(block_id, str):
            continue
        try:
            usage_key = course_key.make_usage_key(block_type, block_id)
        except Exception:
            continue
        if usage_key not in seen:
            keys.append(usage_key)
            seen.add(usage_key)
    return keys


def _is_item_bank_block(block):
    """Return whether a block selects a learner-specific subset of children."""
    try:
        from xmodule.item_bank_block import ItemBankMixin
        if isinstance(block, ItemBankMixin):
            return True
    except Exception:  # pragma: no cover - release-specific import fallback
        pass
    return getattr(block, "category", None) in {"itembank", "library_content"}


def collect_active_unit_usage_keys(
    user, course_key, unit_key, lock_state=False, *,
    store=None, block_cache=None, structural_keys=None,
):
    """Collect the Unit path visible in the learner's current attempt.

    Ordinary containers traverse all children. ItemBank containers traverse
    only the persisted ``selected`` children, so unselected questions cannot be
    reset. Missing selection remains missing and is never initialized here.

    Returns ``(active_keys, container_keys)``.
    """
    StudentModule = get_student_module_model()
    if store is None:
        store = get_modulestore()
    if structural_keys is None:
        structural_keys = collect_unit_usage_keys(
            unit_key, store=store, block_cache=block_cache,
        )
    modules = StudentModule.objects.filter(
        student=user, course_id=course_key, module_state_key__in=list(structural_keys),
    )
    if lock_state:
        modules = modules.select_for_update()
    state_by_key = {
        module.module_state_key: module.state
        for module in modules.only("module_state_key", "state")
    }
    active_keys = set()
    container_keys = set()

    def walk(key):
        if key in active_keys:
            return
        active_keys.add(key)
        try:
            block = _get_cached_modulestore_item(store, key, block_cache)
        except Exception as exc:
            # A stale selected child can still have submissions/StudentModule
            # state. Only a verified not-found error allows treating it as a
            # stale leaf; a transient lookup failure must never broaden scope.
            try:
                from xmodule.modulestore.exceptions import ItemNotFoundError
            except ImportError:
                ItemNotFoundError = ()
            if isinstance(exc, ItemNotFoundError) and getattr(key, "block_type", None) == "problem":
                return
            raise UnitResetError(
                "Không xác minh được phạm vi câu hỏi. Chưa reset dữ liệu bài làm.",
                "RESET_SCOPE_LOOKUP_FAILED",
                503,
            ) from exc

        if key not in structural_keys:
            raise UnitResetError(
                "Selection cũ trỏ tới block nằm ngoài Unit này. Chưa reset dữ liệu bài làm.",
                "RESET_SELECTION_OUTSIDE_UNIT",
                409,
            )

        children = list(getattr(block, "children", None) or [])
        if not children and not getattr(block, "has_children", False):
            return
        container_keys.add(key)
        dynamic = _is_item_bank_block(block)
        selected = extract_selected_usage_keys_from_state(state_by_key.get(key), course_key) if dynamic else []
        for child_key in active_child_keys(children, selected, dynamic=dynamic):
            walk(child_key)

    walk(unit_key)
    return active_keys, container_keys


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




def get_reset_student_attempts():
    """Import Open edX's supported reset API lazily."""
    try:
        from lms.djangoapps.instructor.enrollment import reset_student_attempts
        return reset_student_attempts
    except Exception as exc:  # pragma: no cover - depends on Open edX runtime
        raise UnitResetError(
            "Không import được API reset chính thức của Open edX.",
            "OPENEDX_RESET_API_IMPORT_FAILED",
            500,
        ) from exc


def reset_unit_attempt_state(user, course_key, reset_keys, container_keys):
    """Reset active leaf state once, without recursive per-problem signals."""
    StudentModule = get_student_module_model()
    reset_student_attempts = get_reset_student_attempts()
    reset_completion = get_completion_reset_handler()
    reset_keys = set(reset_keys)
    container_keys = set(container_keys)
    if StudentModule.objects.select_for_update().filter(
        student=user,
        course_id=course_key,
        module_state_key__in=list(container_keys),
        grade__isnull=False,
        max_grade__gt=0,
    ).exists():
        raise UnitResetError(
            "Container có dữ liệu điểm bất thường. Chưa reset dữ liệu bài làm.",
            "RESET_SCORED_CONTAINER",
            409,
        )

    persisted_keys = set(StudentModule.objects.select_for_update().filter(
        student=user,
        course_id=course_key,
        module_state_key__in=list(reset_keys),
    ).values_list("module_state_key", flat=True))
    persisted_leaf_targets, container_cleanup = partition_persisted_reset_keys(
        persisted_keys,
        container_keys,
    )
    # Scores can exist in submissions without a StudentModule row. Include all
    # active leaves so the supported API clears both storage systems.
    targets = sorted(
        set(persisted_leaf_targets) | (reset_keys - container_keys - persisted_keys),
        key=str,
    )
    before_count = len(persisted_keys)
    missing_student_modules = 0

    for target in targets:
        try:
            reset_student_attempts(
                course_key,
                user,
                target,
                requesting_user=user,
                delete_module=True,
                emit_signals_and_events=False,
            )
        except StudentModule.DoesNotExist:
            # The API resets submissions before fetching StudentModule, so a
            # missing row here does not mean the submission reset was skipped.
            missing_student_modules += 1
        # Bulk grade signalling is suppressed above. Preserve the official
        # completion handler separately so reset does not leave old completion=1.
        reset_completion(
            sender=None,
            user_id=user.id,
            course_id=str(course_key),
            usage_id=str(target),
            score_deleted=True,
        )

    # Container rows only hold navigation/randomization state. Never directly
    # delete a leaf row because it may own score/submission state.
    remaining_deleted = 0
    if container_cleanup:
        remaining_deleted, _ = StudentModule.objects.filter(
            student=user,
            course_id=course_key,
            module_state_key__in=container_cleanup,
        ).delete()
    after_count = StudentModule.objects.filter(
        student=user,
        course_id=course_key,
        module_state_key__in=list(reset_keys),
    ).count()

    return {
        "student_modules_deleted": max(before_count - after_count, 0),
        "remaining_student_modules_deleted": remaining_deleted,
        "official_reset_targets": [str(target) for target in targets],
        "missing_student_modules": missing_student_modules,
    }


def get_completion_reset_handler():
    """Use the official completion policy without per-problem grade tasks."""
    try:
        from completion.handlers import scorable_block_completion
        return scorable_block_completion
    except Exception as exc:
        raise UnitResetError(
            "Không import được API completion của Open edX.",
            "OPENEDX_COMPLETION_API_IMPORT_FAILED",
            500,
        ) from exc


def dispatch_grade_reconciliation(reconciliation_id):
    """Publish the durable grade task after the reset transaction commits."""
    try:
        from .tasks import reconcile_unit_reset_grade
        result = reconcile_unit_reset_grade.delay(reconciliation_id)
        UnitResetGradeReconciliation.objects.filter(pk=reconciliation_id).update(
            task_id=str(result.id or ""),
            updated_at=timezone.now(),
        )
    except Exception as exc:  # pragma: no cover - broker/runtime dependent
        UnitResetGradeReconciliation.objects.filter(pk=reconciliation_id).update(
            status=UnitResetGradeReconciliation.STATUS_FAILED,
            last_error=f"Task publish failed: {exc}"[:8000],
            completed_at=timezone.now(),
            updated_at=timezone.now(),
        )
        log.exception("Could not publish Unit reset grade reconciliation id=%s", reconciliation_id)


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


def get_latest_grade_reconciliation(user, course_key, unit_key):
    """Return the latest durable grade status for reset-status polling."""
    reconciliation = UnitResetGradeReconciliation.objects.filter(
        user=user,
        course_id=str(course_key),
        unit_usage_key=str(unit_key),
    ).order_by("-created_at").first()
    if reconciliation is None:
        return None
    return {
        "id": reconciliation.id,
        "status": reconciliation.status,
        "attempts": reconciliation.attempts,
        "last_error": reconciliation.last_error or None,
        "created_at": reconciliation.created_at.isoformat(),
        "completed_at": reconciliation.completed_at.isoformat() if reconciliation.completed_at else None,
    }


def get_status_for_current_user(request, course_id, unit_usage_key):
    course_key, unit_key = parse_keys(course_id, unit_usage_key)
    assert_user_can_reset(request, course_key)

    reset_keys, _container_keys = collect_active_unit_usage_keys(request.user, course_key, unit_key)
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
        "grade_reconciliation": get_latest_grade_reconciliation(request.user, course_key, unit_key),
    }


def reset_unit_for_current_user(request, course_id, unit_usage_key, cooldown_seconds_override=None, bypass_cooldown=False):
    course_key, unit_key = parse_keys(course_id, unit_usage_key)
    assert_user_can_reset(request, course_key)

    cooldown_seconds = int(cooldown_seconds_override) if cooldown_seconds_override is not None else int(
        getattr(settings, "UNIT_RESET_DEFAULT_COOLDOWN_SECONDS", 600)
    )
    max_resets = int(getattr(settings, "UNIT_RESET_MAX_RESETS_PER_UNIT", 0) or 0)

    with transaction.atomic():
        record, _ = UnitResetControl.objects.select_for_update().get_or_create(
            user=request.user,
            course_id=str(course_key),
            unit_usage_key=str(unit_key),
            defaults={"cooldown_seconds": cooldown_seconds},
        )
        UnitResetCourseGradeLock.acquire(request.user.id, str(course_key))
        # Read the selection only after taking the per-user/Unit lock. A waiting
        # reset must not delete the selection from a newer attempt.
        reset_keys, container_keys = collect_active_unit_usage_keys(request.user, course_key, unit_key, lock_state=True)
        if cooldown_seconds_override is None:
            cooldown_seconds = get_block_cooldown_seconds(reset_keys)
        now = timezone.now()

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

        reset_state = reset_unit_attempt_state(
            request.user,
            course_key,
            reset_keys,
            container_keys,
        )
        deleted_count = reset_state["student_modules_deleted"]

        reconciliation = UnitResetGradeReconciliation.objects.create(
            user=request.user,
            course_id=str(course_key),
            unit_usage_key=str(unit_key),
        )
        grade_reconciliation = {
            "id": reconciliation.id,
            "status": UnitResetGradeReconciliation.STATUS_PENDING,
        }
        transaction.on_commit(lambda: dispatch_grade_reconciliation(reconciliation.id))

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
        "Unit reset OK user_id=%s course_id=%s unit=%s deleted=%s reset_state=%s grade_reconciliation=%s reset_keys=%s cooldown=%s reset_count=%s",
        request.user.id,
        course_key,
        unit_key,
        deleted_count,
        reset_state,
        grade_reconciliation,
        len(reset_keys),
        cooldown_seconds,
        record.reset_count,
    )

    return {
        "success": True,
        "code": "RESET_OK",
        "message": "Đã reset Unit. Hệ thống sẽ random lại bộ câu hỏi mới.",
        "deleted_count": deleted_count,
        "reset_state": reset_state,
        "grade_reconciliation": grade_reconciliation,
        # Backward-compatible key. Its status is explicitly asynchronous; this
        # response never claims the course grade was already recalculated.
        "grade_recalculation": grade_reconciliation,
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


def update_unit_quiz_duration(*, course_id, unit_usage_key, duration_seconds, actor=''):
    """Change only the duration used by future sessions of an existing quiz."""
    if type(duration_seconds) is not int or not 1 <= duration_seconds <= 18000:
        raise UnitResetError('Thời lượng phải từ 1 đến 18000 giây.', 'INVALID_DURATION_SECONDS', 400)
    course_key, unit_key = parse_keys(course_id, unit_usage_key)
    with transaction.atomic():
        try:
            config = UnitQuizTimerConfig.objects.select_for_update().get(
                course_id=str(course_key), unit_usage_key=str(unit_key))
        except UnitQuizTimerConfig.DoesNotExist as exc:
            raise UnitResetError('Quiz chưa có cấu hình timer.', 'QUIZ_TIMER_CONFIG_NOT_FOUND', 404) from exc
        if not config.enabled or config.native_timed_exam:
            raise UnitResetError('Chỉ sửa timer tự luyện đang bật.', 'QUIZ_TIMER_NOT_EDITABLE', 409)
        previous_duration = config.duration_seconds
        config.duration_seconds = duration_seconds
        config.updated_by = str(actor or '')[:255]
        config.save(update_fields=['duration_seconds', 'updated_by', 'updated_at'])
        # Every learner session keeps its original duration_seconds/expires_at.
        return {
            'success': True, 'previous_duration_seconds': previous_duration,
            'applies_to': 'new_sessions',
            'config': {'id': config.id, 'course_id': config.course_id,
                       'unit_usage_key': config.unit_usage_key, 'duration_seconds': config.duration_seconds},
        }


def _get_timer_config(course_id, unit_usage_key, for_update=False):
    try:
        configs = UnitQuizTimerConfig.objects.select_for_update() if for_update else UnitQuizTimerConfig.objects
        return configs.get(course_id=str(course_id), unit_usage_key=str(unit_usage_key), enabled=True)
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


def _latest_quiz_session(user, config, for_update=False):
    sessions = UnitQuizSession.objects.select_for_update() if for_update else UnitQuizSession.objects
    return sessions.filter(user=user, config=config).order_by('-attempt_no', '-created_at').first()


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
        UnitResetControl.objects.select_for_update().get_or_create(
            user=request.user, course_id=str(course_key), unit_usage_key=str(unit_key),
        )
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
    return _reset_quiz_session_attempt(request, course_key, unit_key)


@transaction.atomic
def _reset_quiz_session_attempt(request, course_key, unit_key):
    # Serialize with generic Unit resets and session start, including the case
    # where no timer session exists yet. Keep reset + new session in one commit.
    UnitResetControl.objects.select_for_update().get_or_create(
        user=request.user, course_id=str(course_key), unit_usage_key=str(unit_key),
    )
    UnitResetCourseGradeLock.acquire(request.user.id, str(course_key))
    config = _get_timer_config(str(course_key), str(unit_key), for_update=True)
    latest = _latest_quiz_session(request.user, config, for_update=True)
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
    previous = _latest_quiz_session(request.user, config, for_update=True)
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

    # This function runs on every problem_check. Never persist these caches
    # beyond one invocation: selections and session state are learner-specific.
    latest_by_config = {}
    active_keys_by_unit = {}
    block_cache = {}
    store = None

    for session in guarded_sessions:
        try:
            # If a newer ACTIVE/SUBMITTING attempt exists for the same timer config,
            # allow the submit. This is the normal state after the learner clicks
            # "Làm lại bài" and the Unit has been randomized again.
            if session.config_id not in latest_by_config:
                latest_by_config[session.config_id] = UnitQuizSession.objects.filter(
                    user=user,
                    config_id=session.config_id,
                ).order_by('-attempt_no', '-created_at').first()
            latest_for_config = latest_by_config[session.config_id]
            if latest_for_config and latest_for_config.id != session.id and latest_for_config.status in (
                UnitQuizSession.STATUS_ACTIVE,
                UnitQuizSession.STATUS_SUBMITTING,
            ):
                continue

            unit_key = UsageKey.from_string(session.unit_usage_key)
            if unit_key not in active_keys_by_unit:
                if store is None:
                    store = get_modulestore()
                if course_key is not None:
                    # SplitMongo get_item() opens its own bulk_operations context.
                    # Hold one outer read-only scope for the Unit traversal to
                    # reuse the deserialized course structure across all children.
                    # Without this, Redis pickle.loads runs once per child.
                    from contextlib import nullcontext

                    bulk = getattr(store, 'bulk_operations', None)
                    if (
                        bulk is not None
                        and getattr(settings, 'UNIT_RESET_QUIZ_GUARD_BULK_READ_ENABLED', True)
                    ):
                        scope = bulk(course_key, emit_signals=False)
                    else:
                        scope = nullcontext()
                    with scope:
                        reset_keys, _container_keys = collect_active_unit_usage_keys(
                            user, course_key, unit_key,
                            store=store, block_cache=block_cache,
                        )
                else:
                    reset_keys = collect_unit_usage_keys(
                        unit_key, store=store, block_cache=block_cache,
                    )
                active_keys_by_unit[unit_key] = (
                    reset_keys,
                    {str(key) for key in reset_keys},
                )
            reset_keys, reset_key_strings = active_keys_by_unit[unit_key]
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
