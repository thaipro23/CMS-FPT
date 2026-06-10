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
    get_quiz_session_status_for_current_user,
    lock_quiz_session_for_current_user,
    reset_quiz_session_for_current_user,
    start_quiz_session_for_current_user,
    timeout_quiz_session_for_current_user,
    upsert_unit_quiz_timer_config,
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



def _quiz_required_fields_from_query(request):
    course_id = request.GET.get('course_id')
    unit_usage_key = request.GET.get('unit_usage_key')
    if not course_id or not unit_usage_key:
        return None, None, JsonResponse(
            {'success': False, 'code': 'MISSING_REQUIRED_FIELDS', 'message': 'Missing course_id or unit_usage_key'},
            status=400,
        )
    return course_id, unit_usage_key, None


def _quiz_required_fields_from_body(request):
    payload = _json_body(request)
    course_id = payload.get('course_id')
    unit_usage_key = payload.get('unit_usage_key')
    if not course_id or not unit_usage_key:
        return payload, None, None, JsonResponse(
            {'success': False, 'code': 'MISSING_REQUIRED_FIELDS', 'message': 'Missing course_id or unit_usage_key'},
            status=400,
        )
    return payload, course_id, unit_usage_key, None


def _quiz_error_response(exc):
    if isinstance(exc, ResetCooldownError):
        return JsonResponse({
            'success': False,
            'code': exc.code,
            'message': f'Bạn cần chờ {exc.wait_seconds} giây nữa để làm lại bài.',
            'wait_seconds': exc.wait_seconds,
            'next_reset_allowed_at': exc.next_reset_allowed_at.isoformat() if exc.next_reset_allowed_at else None,
            'cooldown_seconds': exc.cooldown_seconds,
        }, status=exc.status_code)
    if isinstance(exc, UnitResetError):
        return JsonResponse({'success': False, 'code': exc.code, 'message': str(exc)}, status=exc.status_code)
    log.exception('Quiz session API failed')
    return JsonResponse({'success': False, 'code': 'INTERNAL_ERROR', 'message': str(exc)}, status=500)


@login_required
@require_GET
def quiz_session_status(request):
    course_id, unit_usage_key, error = _quiz_required_fields_from_query(request)
    if error:
        return error
    try:
        return JsonResponse(get_quiz_session_status_for_current_user(request, course_id, unit_usage_key), status=200)
    except Exception as exc:
        return _quiz_error_response(exc)


@login_required
@csrf_protect
@require_POST
def quiz_session_start(request):
    payload, course_id, unit_usage_key, error = _quiz_required_fields_from_body(request)
    if error:
        return error
    try:
        return JsonResponse(start_quiz_session_for_current_user(request, course_id, unit_usage_key), status=200)
    except Exception as exc:
        return _quiz_error_response(exc)


@login_required
@csrf_protect
@require_POST
def quiz_session_timeout(request):
    payload, course_id, unit_usage_key, error = _quiz_required_fields_from_body(request)
    if error:
        return error
    try:
        return JsonResponse(timeout_quiz_session_for_current_user(request, course_id, unit_usage_key, payload=payload), status=200)
    except Exception as exc:
        return _quiz_error_response(exc)


@login_required
@csrf_protect
@require_POST
def quiz_session_lock(request):
    payload, course_id, unit_usage_key, error = _quiz_required_fields_from_body(request)
    if error:
        return error
    try:
        return JsonResponse(lock_quiz_session_for_current_user(request, course_id, unit_usage_key, payload=payload), status=200)
    except Exception as exc:
        return _quiz_error_response(exc)


@login_required
@csrf_protect
@require_POST
def quiz_session_reset(request):
    payload, course_id, unit_usage_key, error = _quiz_required_fields_from_body(request)
    if error:
        return error
    try:
        return JsonResponse(reset_quiz_session_for_current_user(request, course_id, unit_usage_key), status=200)
    except Exception as exc:
        return _quiz_error_response(exc)


@csrf_protect
@require_POST
def quiz_timer_config_upsert(request):
    if not request.user.is_authenticated or not (request.user.is_staff or request.user.is_superuser):
        return JsonResponse({'success': False, 'code': 'STAFF_REQUIRED', 'message': 'Staff required'}, status=403)
    payload = _json_body(request)
    try:
        result = upsert_unit_quiz_timer_config(
            course_id=payload.get('course_id'),
            sequence_usage_key=payload.get('sequence_usage_key') or '',
            unit_usage_key=payload.get('unit_usage_key'),
            title=payload.get('title') or 'Quiz tự luyện',
            duration_seconds=payload.get('duration_seconds'),
            cooldown_seconds=payload.get('cooldown_seconds'),
            enabled=payload.get('enabled', True),
            auto_submit_on_timeout=payload.get('auto_submit_on_timeout', True),
            lock_after_timeout=payload.get('lock_after_timeout', True),
            native_timed_exam=payload.get('native_timed_exam', False),
            actor=getattr(request.user, 'username', '') or str(request.user.id),
            metadata_json=payload.get('metadata') or {},
        )
        return JsonResponse(result, status=200)
    except Exception as exc:
        return _quiz_error_response(exc)


@require_GET
def quiz_session_runtime_js(request):
    from django.http import HttpResponse
    js = """
(function(){
  if (window.__OPENEDX_UNIT_RESET_TIMER_JS__) return;
  window.__OPENEDX_UNIT_RESET_TIMER_JS__ = true;
  function selected(problem){
    var checked = problem.querySelector('input[type="radio"]:checked,input[type="checkbox"]:checked');
    if (checked) return true;
    var textInputs = Array.prototype.slice.call(problem.querySelectorAll('input[type="text"],textarea'));
    if (textInputs.some(function(el){ return el.value && el.value.trim().length > 0; })) return true;
    var selects = Array.prototype.slice.call(problem.querySelectorAll('select'));
    return selects.some(function(el){ return el.value && el.value.trim().length > 0; });
  }
  function submitButton(problem){
    var buttons = Array.prototype.slice.call(problem.querySelectorAll('button,input[type="button"],input[type="submit"]'));
    return buttons.find(function(btn){
      var text = ((btn.innerText || btn.value || '') + '').trim().toLowerCase();
      return ['submit','check','nộp bài','nop bai','kiểm tra','kiem tra'].some(function(x){ return text.indexOf(x) >= 0; });
    });
  }
  function sleep(ms){ return new Promise(function(resolve){ setTimeout(resolve, ms); }); }
  async function autoSubmit(){
    var problems = Array.prototype.slice.call(document.querySelectorAll('.problem,.xblock-student_view,[data-usage-id]'));
    var submitted = 0;
    for (var i=0; i<problems.length; i++){
      var p = problems[i];
      if (!selected(p)) continue;
      var btn = submitButton(p);
      if (!btn || btn.disabled) continue;
      btn.click();
      submitted += 1;
      await sleep(800);
    }
    return submitted;
  }
  function lock(){
    Array.prototype.slice.call(document.querySelectorAll('input,textarea,select,button')).forEach(function(el){
      var text = ((el.innerText || el.value || '') + '').toLowerCase();
      if (text.indexOf('hint') >= 0 || text.indexOf('show answer') >= 0 || text.indexOf('xem đáp án') >= 0 || text.indexOf('submission history') >= 0) return;
      el.disabled = true;
      el.setAttribute('aria-disabled', 'true');
    });
    document.body.classList.add('ai-quiz-timeout-locked');
  }
  window.addEventListener('message', async function(event){
    if (!event.data || event.data.type !== 'AI_QUIZ_TIMEOUT_AUTO_SUBMIT') return;
    var count = await autoSubmit();
    lock();
    window.parent && window.parent.postMessage({type:'AI_QUIZ_TIMEOUT_AUTO_SUBMIT_DONE', submitted_problem_count: count}, '*');
  });
})();
"""
    return HttpResponse(js, content_type='application/javascript; charset=utf-8')
