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
    js = r"""
(function(){
  if (window.__OPENEDX_UNIT_RESET_TIMER_JS__) return;
  window.__OPENEDX_UNIT_RESET_TIMER_JS__ = true;

  // v0.4.15: iframe runtime submits selected answers by calling Open edX
  // problem_check APIs directly. It never clicks Submit/Check buttons. This is
  // more stable when the native button is disabled or the learner switches tabs.
  if (!window.parent || window.parent === window) return;

  var autoSubmitting = false;

  function lower(value){ return ((value || '') + '').toLowerCase(); }
  function sleep(ms){ return new Promise(function(resolve){ setTimeout(resolve, ms); }); }

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

  function getCookie(name){
    var value = '; ' + (document.cookie || '');
    var parts = value.split('; ' + name + '=');
    if (parts.length === 2) return decodeURIComponent(parts.pop().split(';').shift());
    return '';
  }

  function isVisible(el){
    if (!el) return false;
    var style = window.getComputedStyle(el);
    return style.visibility !== 'hidden' && style.display !== 'none';
  }

  function rootUsageId(root){
    if (!root) return '';
    var attrs = ['data-usage-id', 'data-locator', 'data-block-id', 'data-problem-id'];
    for (var i=0; i<attrs.length; i++) {
      var v = root.getAttribute && root.getAttribute(attrs[i]);
      if (v && (v.indexOf('block-v1:') >= 0 || v.indexOf('+type@') >= 0)) return v;
    }
    var candidate = root.querySelector && root.querySelector('[data-usage-id],[data-locator],[data-block-id],[data-problem-id]');
    if (candidate) return rootUsageId(candidate);
    var html = '';
    try { html = root.outerHTML || ''; } catch (error) { html = ''; }
    var match = html.match(/block-v1:[^"'<>\\s]+/);
    return match ? match[0] : '';
  }

  function courseFromLocation(){
    var match = (window.location.pathname || '').match(/\/courses\/([^\/]+)/);
    return match ? decodeURIComponent(match[1]) : '';
  }

  function findProblemCheckUrl(root, courseId){
    var nodes = [root].concat(Array.prototype.slice.call(root.querySelectorAll('form,button,input,a,[data-url],[data-check-url],[data-submit-url],[data-problem-check-url]')));
    var attrs = ['action', 'href', 'data-check-url', 'data-url', 'data-submit-url', 'data-problem-check-url'];
    for (var i=0; i<nodes.length; i++) {
      for (var j=0; j<attrs.length; j++) {
        var value = '';
        try { value = nodes[i].getAttribute && nodes[i].getAttribute(attrs[j]); } catch (error) { value = ''; }
        if (value && value.indexOf('problem_check') >= 0) return new URL(value, window.location.href).toString();
      }
    }
    var html = '';
    try { html = root.outerHTML || ''; } catch (error) { html = ''; }
    var match = html.match(/([^"']*problem_check[^"']*)/);
    if (match && match[1]) return new URL(match[1], window.location.href).toString();

    var usage = rootUsageId(root);
    var course = courseId || courseFromLocation();
    if (usage && course) {
      return window.location.origin + '/courses/' + course + '/xblock/' + encodeURIComponent(usage) + '/handler/xmodule_handler/problem_check';
    }
    return '';
  }

  function problemRoots(){
    var roots = Array.prototype.slice.call(document.querySelectorAll('.problem'));
    if (roots.length) return roots;
    roots = Array.prototype.slice.call(document.querySelectorAll('[data-usage-id], [data-locator], .xblock-student_view'));
    var filtered = [];
    roots.forEach(function(root){
      if (!root || filtered.some(function(existing){ return existing.contains(root); })) return;
      if (root.querySelector('input,textarea,select')) filtered.push(root);
    });
    return filtered;
  }

  function selected(root){
    if (!root) return false;
    if (root.querySelector('input[type="radio"]:checked,input[type="checkbox"]:checked')) return true;
    var fields = Array.prototype.slice.call(root.querySelectorAll('input[type="text"],input[type="number"],input:not([type]),textarea,select'));
    return fields.some(function(el){ return el.name && el.value && String(el.value).trim().length > 0; });
  }

  function appendField(formData, name, value){
    if (!name) return;
    formData.append(name, value == null ? '' : value);
  }

  function collectAnswerFormData(root){
    var formData = new FormData();
    var fields = Array.prototype.slice.call(root.querySelectorAll('input,textarea,select'));
    fields.forEach(function(el){
      if (!el.name) return;
      var tag = lower(el.tagName);
      var type = lower(el.type);
      if (type === 'button' || type === 'submit' || type === 'reset' || type === 'file') return;
      if ((type === 'checkbox' || type === 'radio') && !el.checked) return;
      if (tag === 'select' && el.multiple) {
        Array.prototype.slice.call(el.selectedOptions || []).forEach(function(opt){ appendField(formData, el.name, opt.value); });
        return;
      }
      appendField(formData, el.name, el.value);
    });
    if (!formData.has('csrfmiddlewaretoken')) {
      var csrfInput = document.querySelector('input[name="csrfmiddlewaretoken"]');
      if (csrfInput && csrfInput.value) appendField(formData, 'csrfmiddlewaretoken', csrfInput.value);
    }
    return formData;
  }

  async function submitProblemByApi(root, courseId){
    if (!root || !selected(root)) return { skipped: true, reason: 'no_answer' };
    var url = findProblemCheckUrl(root, courseId);
    if (!url) return { skipped: true, reason: 'missing_problem_check_url' };
    var csrf = getCookie('csrftoken') || getCookie('csrf_token') || '';
    var response = await fetch(url, {
      method: 'POST',
      credentials: 'include',
      headers: csrf ? { 'X-CSRFToken': csrf, 'X-Requested-With': 'XMLHttpRequest' } : { 'X-Requested-With': 'XMLHttpRequest' },
      body: collectAnswerFormData(root),
    });
    return { skipped: false, ok: response.ok, status: response.status, url: url };
  }

  async function autoSubmitByApi(courseId){
    var roots = problemRoots().filter(function(root){ return isVisible(root) && selected(root); });
    var submitted = 0;
    var failed = 0;
    var skipped = 0;
    for (var i=0; i<roots.length; i++) {
      try {
        var result = await submitProblemByApi(roots[i], courseId);
        if (result.skipped) skipped += 1;
        else if (result.ok) submitted += 1;
        else failed += 1;
      } catch (error) {
        failed += 1;
      }
      await sleep(120);
    }
    return { submitted: submitted, failed: failed, skipped: skipped, discovered: roots.length };
  }

  function lockLocally(){
    Array.prototype.slice.call(document.querySelectorAll('input,textarea,select,button')).forEach(function(el){
      var text = lower((el.innerText || el.value || '') + '');
      if (text.indexOf('hint') >= 0 || text.indexOf('show answer') >= 0 || text.indexOf('xem đáp án') >= 0 || text.indexOf('submission history') >= 0) return;
      el.disabled = true;
      el.setAttribute('aria-disabled', 'true');
    });
    document.body.classList.add('ai-quiz-timeout-locked');
  }

  window.addEventListener('message', async function(event){
    if (!event.data || event.data.type !== 'AI_QUIZ_TIMEOUT_API_SUBMIT') return;
    if (autoSubmitting) return;
    autoSubmitting = true;
    var result = { submitted: 0, failed: 0, skipped: 0, discovered: 0 };
    try { result = await autoSubmitByApi(event.data.course_id || ''); } catch (error) { /* best effort */ }
    lockLocally();
    if (window.parent) {
      window.parent.postMessage({
        type: 'AI_QUIZ_TIMEOUT_API_SUBMIT_DONE',
        submitted_problem_count: result.submitted || 0,
        failed_problem_count: result.failed || 0,
        skipped_problem_count: result.skipped || 0,
        discovered_problem_count: result.discovered || 0
      }, '*');
    }
    autoSubmitting = false;
  });
})();
"""
    response = HttpResponse(js, content_type='application/javascript; charset=utf-8')
    response['Cache-Control'] = 'no-store, max-age=0'
    return response

