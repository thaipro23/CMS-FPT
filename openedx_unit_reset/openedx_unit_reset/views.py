import json
import logging

from django.http import JsonResponse
from django.views.decorators.csrf import csrf_protect
from django.views.decorators.http import require_GET, require_POST
from django.contrib.auth.decorators import login_required

from .services import (
    ResetCooldownError,
    ResetLimitExceededError,
    UnitResetError,
    audit_reset,
    get_status_for_current_user,
    parse_keys,
    reset_unit_for_current_user,
)

log = logging.getLogger(__name__)


def _json_body(request):
    try:
        return json.loads(request.body.decode("utf-8"))
    except Exception:
        return {}


@login_required
@require_GET
def reset_unit_status(request):
    course_id = request.GET.get("course_id")
    unit_usage_key = request.GET.get("unit_usage_key")

    if not course_id or not unit_usage_key:
        return JsonResponse(
            {"success": False, "code": "MISSING_REQUIRED_FIELDS", "message": "Missing course_id or unit_usage_key"},
            status=400,
        )

    try:
        data = get_status_for_current_user(request, course_id, unit_usage_key)
        return JsonResponse(data, status=200)
    except UnitResetError as exc:
        return JsonResponse({"success": False, "code": exc.code, "message": str(exc)}, status=exc.status_code)
    except Exception as exc:
        log.exception("Failed to get unit reset status")
        return JsonResponse({"success": False, "code": "INTERNAL_ERROR", "message": str(exc)}, status=500)


@login_required
@csrf_protect
@require_POST
def reset_unit_attempt(request):
    payload = _json_body(request)

    if payload.get("user_id"):
        return JsonResponse(
            {
                "success": False,
                "code": "CLIENT_USER_ID_NOT_ALLOWED",
                "message": "Không được truyền user_id từ client. Hệ thống chỉ reset cho user đang đăng nhập.",
            },
            status=400,
        )

    course_id = payload.get("course_id")
    unit_usage_key = payload.get("unit_usage_key")

    if not course_id or not unit_usage_key:
        return JsonResponse(
            {"success": False, "code": "MISSING_REQUIRED_FIELDS", "message": "Missing course_id or unit_usage_key"},
            status=400,
        )

    try:
        result = reset_unit_for_current_user(request, course_id, unit_usage_key)
        return JsonResponse(result, status=200)

    except ResetCooldownError as exc:
        try:
            course_key, unit_key = parse_keys(course_id, unit_usage_key)
            audit_reset(
                request,
                course_key,
                unit_key,
                success=False,
                code=exc.code,
                message=str(exc),
                wait_seconds=exc.wait_seconds,
                cooldown_seconds=exc.cooldown_seconds,
            )
        except Exception:
            pass

        return JsonResponse(
            {
                "success": False,
                "code": exc.code,
                "message": f"Bạn cần chờ {exc.wait_seconds} giây nữa để làm lại bài.",
                "wait_seconds": exc.wait_seconds,
                "next_reset_allowed_at": exc.next_reset_allowed_at.isoformat() if exc.next_reset_allowed_at else None,
                "reset_count": exc.reset_count,
                "cooldown_seconds": exc.cooldown_seconds,
            },
            status=exc.status_code,
        )

    except ResetLimitExceededError as exc:
        return JsonResponse({"success": False, "code": exc.code, "message": str(exc)}, status=exc.status_code)

    except UnitResetError as exc:
        try:
            course_key, unit_key = parse_keys(course_id, unit_usage_key)
            audit_reset(request, course_key, unit_key, success=False, code=exc.code, message=str(exc))
        except Exception:
            pass
        return JsonResponse({"success": False, "code": exc.code, "message": str(exc)}, status=exc.status_code)

    except Exception as exc:
        log.exception("Failed to reset unit attempt")
        return JsonResponse({"success": False, "code": "INTERNAL_ERROR", "message": str(exc)}, status=500)
