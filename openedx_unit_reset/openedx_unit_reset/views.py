import hashlib
import hmac
import json
import logging
import os
import time

from django.conf import settings
from django.http import JsonResponse
from django.views.decorators.csrf import csrf_exempt, csrf_protect
from django.views.decorators.http import require_GET, require_POST
from django.contrib.auth.decorators import login_required

from .services import (
    ResetCooldownError,
    ResetLimitExceededError,
    UnitResetError,
    audit_reset,
    get_status_for_current_user,
    get_legacy_compatible_timer_status_for_current_user,
    get_timer_config_or_none,
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


def _request_path_with_query(request):
    path = request.path or ''
    query = request.META.get('QUERY_STRING') or ''
    return f'{path}?{query}' if query else path


def _connector_hmac_secret():
    return (
        getattr(settings, 'AI_CONNECTOR_HMAC_SECRET', '')
        or getattr(settings, 'OPENEDX_CONNECTOR_HMAC_SECRET', '')
        or os.environ.get('AI_CONNECTOR_HMAC_SECRET')
        or os.environ.get('OPENEDX_CONNECTOR_HMAC_SECRET')
        or ''
    )


def _valid_connector_hmac(request):
    secret = str(_connector_hmac_secret() or '')
    if not secret:
        return False
    timestamp = request.META.get('HTTP_X_AI_CONNECTOR_TIMESTAMP') or ''
    supplied = request.META.get('HTTP_X_AI_CONNECTOR_SIGNATURE') or ''
    try:
        ts = int(timestamp)
    except Exception:
        return False
    skew = int(os.environ.get('AI_CONNECTOR_HMAC_SKEW_SECONDS') or getattr(settings, 'AI_CONNECTOR_HMAC_SKEW_SECONDS', 300) or 300)
    if abs(int(time.time()) - ts) > skew:
        return False
    body_hash = hashlib.sha256(request.body or b'').hexdigest()
    message = f'{timestamp}.{request.method.upper()}.{_request_path_with_query(request)}.{body_hash}'
    expected = hmac.new(secret.encode('utf-8'), message.encode('utf-8'), hashlib.sha256).hexdigest()
    return hmac.compare_digest(expected, supplied)


def _staff_or_hmac(request):
    user = getattr(request, 'user', None)
    if _valid_connector_hmac(request):
        return True
    return bool(getattr(user, 'is_authenticated', False) and (getattr(user, 'is_staff', False) or getattr(user, 'is_superuser', False)))


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
        if get_timer_config_or_none(course_id, unit_usage_key):
            data = get_legacy_compatible_timer_status_for_current_user(request, course_id, unit_usage_key)
        else:
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
        if get_timer_config_or_none(course_id, unit_usage_key):
            result = reset_quiz_session_for_current_user(request, course_id, unit_usage_key)
        else:
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


@csrf_exempt
@require_POST
def quiz_timer_config_upsert(request):
    if not _staff_or_hmac(request):
        return JsonResponse({'success': False, 'code': 'CONNECTOR_AUTH_REQUIRED', 'message': 'HMAC hoặc staff required'}, status=403)
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

  // v0.4.14: iframe-only runtime clicks real Submit/Check buttons only, never Save/Lưu.
  // Important: runtime.js may also be loaded in the top Learning MFE window.
  // Auto-submit must run only inside the LMS problem iframe. If the top window
  // handles AI_QUIZ_TIMEOUT_AUTO_SUBMIT it can send DONE too early, making the
  // MFE call /lock after the first problem_check while the remaining checks are
  // still in flight.
  if (!window.parent || window.parent === window) {
    return;
  }

  function reloadIframeDocument(reason, token){
    try {
      var storageKey = 'openedx-unit-reset:iframe-self-reload:' + (reason || 'active') + ':' + (token || 'active');
      try {
        if (window.sessionStorage && window.sessionStorage.getItem(storageKey) === '1') return;
        if (window.sessionStorage) window.sessionStorage.setItem(storageKey, '1');
      } catch (storageError) { /* ignore */ }
      var url = new URL(window.location.href);
      url.searchParams.set('unit_reset_nonce', String(token || Date.now()));
      url.searchParams.set('unit_reset_reason', reason || 'active-session-ready');
      window.location.replace(url.toString());
    } catch (error) {
      try { window.location.reload(); } catch (reloadError) { /* ignore */ }
    }
  }

  window.addEventListener('message', function(event){
    if (!event.data || event.data.type !== 'AI_QUIZ_ACTIVE_SESSION_READY_RELOAD') return;
    reloadIframeDocument(event.data.reason || 'active-session-ready', event.data.token || Date.now());
  });

  var pendingProblemChecks = 0;
  var startedProblemChecks = 0;
  var finishedProblemChecks = 0;
  var autoSubmitting = false;

  function lower(value){ return ((value || '') + '').toLowerCase(); }
  function isProblemCheckUrl(url){
    url = lower(url);
    return url.indexOf('problem_check') >= 0 || url.indexOf('xmodule_handler/problem_check') >= 0;
  }
  function markProblemCheckStart(url){
    if (!isProblemCheckUrl(url)) return false;
    startedProblemChecks += 1;
    pendingProblemChecks += 1;
    return true;
  }
  function markProblemCheckDone(wasTracked){
    if (!wasTracked) return;
    pendingProblemChecks = Math.max(0, pendingProblemChecks - 1);
    finishedProblemChecks += 1;
  }

  // Track native Open edX problem_check requests. The Learning MFE must not lock
  // the timed attempt until these requests finish; otherwise only the first
  // problem_check succeeds and the rest are blocked as QUIZ_TIME_EXPIRED.
  try {
    if (window.fetch && !window.fetch.__openedxUnitResetTracked) {
      var originalFetch = window.fetch;
      var trackedFetch = function(input, init){
        var url = '';
        try { url = typeof input === 'string' ? input : (input && input.url) || ''; } catch (error) { url = ''; }
        var tracked = markProblemCheckStart(url);
        return originalFetch.apply(this, arguments).finally(function(){ markProblemCheckDone(tracked); });
      };
      trackedFetch.__openedxUnitResetTracked = true;
      window.fetch = trackedFetch;
    }
  } catch (error) { /* best effort */ }

  try {
    if (window.XMLHttpRequest && !window.XMLHttpRequest.__openedxUnitResetTracked) {
      var OriginalXHR = window.XMLHttpRequest;
      var originalOpen = OriginalXHR.prototype.open;
      var originalSend = OriginalXHR.prototype.send;
      OriginalXHR.prototype.open = function(method, url){
        try { this.__openedxUnitResetUrl = url || ''; } catch (error) { /* ignore */ }
        return originalOpen.apply(this, arguments);
      };
      OriginalXHR.prototype.send = function(){
        var tracked = false;
        try { tracked = markProblemCheckStart(this.__openedxUnitResetUrl || ''); } catch (error) { tracked = false; }
        if (tracked) {
          try { this.addEventListener('loadend', function(){ markProblemCheckDone(true); }, { once: true }); } catch (error) { /* ignore */ }
        }
        return originalSend.apply(this, arguments);
      };
      window.XMLHttpRequest.__openedxUnitResetTracked = true;
    }
  } catch (error) { /* best effort */ }

  function sleep(ms){ return new Promise(function(resolve){ setTimeout(resolve, ms); }); }
  function isVisible(el){
    if (!el) return false;
    if (el.offsetParent === null && getComputedStyle(el).position !== 'fixed') return false;
    var style = getComputedStyle(el);
    return style.visibility !== 'hidden' && style.display !== 'none';
  }
  function problemRoot(el){
    return el && (el.closest('.problem') || el.closest('.xblock-student_view') || el.closest('[data-usage-id]') || document);
  }
  function selected(problem){
    var checked = problem.querySelector('input[type="radio"]:checked,input[type="checkbox"]:checked');
    if (checked) return true;
    var textInputs = Array.prototype.slice.call(problem.querySelectorAll('input[type="text"],input[type="number"],textarea'));
    if (textInputs.some(function(el){ return el.value && el.value.trim().length > 0; })) return true;
    var selects = Array.prototype.slice.call(problem.querySelectorAll('select'));
    return selects.some(function(el){ return el.value && el.value.trim().length > 0; });
  }
  function buttonText(btn){
    return lower(((btn && (btn.innerText || btn.value || btn.getAttribute('aria-label') || btn.getAttribute('title'))) || '') + '').trim();
  }
  function isNoiseButtonText(text){
    return text.indexOf('hint') >= 0
      || text.indexOf('show answer') >= 0
      || text.indexOf('xem đáp án') >= 0
      || text.indexOf('submission history') >= 0;
  }
  function isSaveOnlyButtonText(text){
    // v0.4.14: do not click Save/Lưu. Save only sends problem_save and does
    // not grade the answer. Auto-submit must click Submit/Check/Nộp bài only.
    return text.indexOf('save') >= 0 || text.indexOf('lưu') >= 0 || text.indexOf('luu') >= 0;
  }
  function isActualSubmitButton(btn){
    var text = buttonText(btn);
    if (!text || isNoiseButtonText(text) || isSaveOnlyButtonText(text)) return false;
    return text.indexOf('submit') >= 0
      || text.indexOf('check') >= 0
      || text.indexOf('nộp bài') >= 0
      || text.indexOf('nop bai') >= 0
      || text.indexOf('kiểm tra') >= 0
      || text.indexOf('kiem tra') >= 0;
  }
  function submitButtons(){
    var all = Array.prototype.slice.call(document.querySelectorAll('button,input[type="button"],input[type="submit"]'));
    var byProblem = new Map();
    all.forEach(function(btn){
      if (!btn || btn.disabled || !isVisible(btn) || !isActualSubmitButton(btn)) return;
      var root = problemRoot(btn);
      if (!selected(root)) return;
      if (!byProblem.has(root)) byProblem.set(root, btn);
    });
    return Array.from(byProblem.values());
  }
  async function waitForProblemCheckBurstToStart(beforeStarted){
    // Submit all selected problems in one burst, then wait briefly for Open edX
    // to enqueue the native problem_check requests. We do not wait after each
    // click because there is no server /lock policy in this version.
    var startDeadline = Date.now() + 2500;
    while (Date.now() < startDeadline && startedProblemChecks <= beforeStarted) await sleep(50);
  }
  async function waitForAllPendingProblemChecks(){
    // After all clicks, wait only for already-started problem_check requests.
    // This keeps the browser from reporting DONE while problem_check requests
    // are still in flight, but avoids slow per-problem sequential submit.
    var deadline = Date.now() + 15000;
    var stableSince = null;
    while (Date.now() < deadline) {
      if (pendingProblemChecks <= 0) {
        if (stableSince === null) stableSince = Date.now();
        if (Date.now() - stableSince >= 800) break;
      } else {
        stableSince = null;
      }
      await sleep(100);
    }
  }

  async function autoSubmit(){
    var buttons = submitButtons();
    var clicked = 0;
    var networkStartedBefore = startedProblemChecks;

    // Click every selected problem's Submit/Check button in one pass. This is
    // intentionally concurrent/batched: Open edX will issue problem_save and
    // problem_check requests for multiple problems without waiting for each
    // previous problem_check to finish.
    for (var i=0; i<buttons.length; i++){
      var btn = buttons[i];
      if (!btn || btn.disabled || !isVisible(btn)) continue;
      if (!selected(problemRoot(btn))) continue;
      try { btn.click(); clicked += 1; } catch (error) { /* keep submitting the rest */ }
    }

    await waitForProblemCheckBurstToStart(networkStartedBefore);
    await waitForAllPendingProblemChecks();
    return {
      clicked: clicked,
      problem_check_started: Math.max(0, startedProblemChecks - networkStartedBefore),
      problem_check_finished: finishedProblemChecks,
      problem_check_pending: pendingProblemChecks
    };
  }

  function lock(){
    Array.prototype.slice.call(document.querySelectorAll('input,textarea,select,button')).forEach(function(el){
      var text = lower((el.innerText || el.value || '') + '');
      if (text.indexOf('hint') >= 0 || text.indexOf('show answer') >= 0 || text.indexOf('xem đáp án') >= 0 || text.indexOf('submission history') >= 0) return;
      el.disabled = true;
      el.setAttribute('aria-disabled', 'true');
    });
    document.body.classList.add('ai-quiz-timeout-locked');
  }

  window.addEventListener('message', async function(event){
    if (!event.data || event.data.type !== 'AI_QUIZ_TIMEOUT_AUTO_SUBMIT') return;
    if (autoSubmitting) return;
    autoSubmitting = true;
    var result = { clicked: 0, problem_check_started: 0, problem_check_finished: 0, problem_check_pending: pendingProblemChecks };
    try { result = await autoSubmit(); } catch (error) { /* still lock locally and report best effort */ }
    // v0.4.12: do not rely on the server /lock flow. Local disabling is kept
    // only inside this iframe after auto-submit finishes; the server-side guard
    // will expire the SUBMITTING session after the grace window.
    lock();
    if (window.parent) {
      window.parent.postMessage({
        type: 'AI_QUIZ_TIMEOUT_AUTO_SUBMIT_DONE',
        submitted_problem_count: result.clicked || 0,
        problem_check_started_count: result.problem_check_started || 0,
        problem_check_finished_count: result.problem_check_finished || 0,
        pending_problem_check_count: result.problem_check_pending || 0
      }, '*');
    }
    autoSubmitting = false;
  });
})();
"""
    response = HttpResponse(js, content_type='application/javascript; charset=utf-8')
    response['Cache-Control'] = 'no-store, max-age=0'
    return response

