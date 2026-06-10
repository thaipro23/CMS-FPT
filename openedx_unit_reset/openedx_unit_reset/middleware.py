import json
import logging
from urllib.parse import unquote

from django.http import JsonResponse

from .services import is_late_submit_blocked

log = logging.getLogger(__name__)


class UnitQuizSessionSubmitGuardMiddleware:
    """Block late problem submissions for custom timed practice quizzes.

    Frontend locking is not enough because students can manually call submit
    endpoints. This middleware best-effort blocks Open edX problem_check-style
    requests after the learner's quiz session is expired/locked.
    """

    SUBMIT_PATH_MARKERS = (
        'problem_check',
        'save_problem_success',
        'xmodule_handler/problem_check',
        '/handler/xmodule_handler/problem_check',
    )

    def __init__(self, get_response):
        self.get_response = get_response

    def __call__(self, request):
        if self._looks_like_problem_submit(request):
            try:
                course_id, problem_usage_key = self._extract_course_and_problem(request)
                blocked, session = is_late_submit_blocked(request.user, course_id, problem_usage_key)
                if blocked:
                    return JsonResponse(
                        {
                            'success': False,
                            'error': 'QUIZ_TIME_EXPIRED',
                            'code': 'QUIZ_TIME_EXPIRED',
                            'message': 'Đã hết giờ. Lượt làm này đã bị khóa, không thể nộp thêm đáp án.',
                            'course_id': course_id,
                            'unit_usage_key': getattr(session, 'unit_usage_key', None),
                            'reset_available_at': session.reset_available_at.isoformat() if getattr(session, 'reset_available_at', None) else None,
                        },
                        status=403,
                    )
            except Exception:
                log.exception('Timed quiz submit guard failed; allowing request to avoid breaking Open edX submit flow')
        return self.get_response(request)

    def _looks_like_problem_submit(self, request):
        if request.method not in ('POST', 'PUT', 'PATCH'):
            return False
        path = (getattr(request, 'path', '') or '').lower()
        return any(marker in path for marker in self.SUBMIT_PATH_MARKERS)

    def _extract_course_and_problem(self, request):
        data = {}
        for source in (getattr(request, 'POST', None), getattr(request, 'GET', None)):
            try:
                for key in ('course_id', 'course_key', 'problem_id', 'usage_id', 'module_id', 'id'):
                    value = source.get(key) if source is not None else None
                    if value and key not in data:
                        data[key] = value
            except Exception:
                pass
        try:
            if request.body:
                body_text = request.body.decode('utf-8')
                if body_text.strip().startswith('{'):
                    body = json.loads(body_text)
                    if isinstance(body, dict):
                        data.update({k: v for k, v in body.items() if isinstance(v, str)})
        except Exception:
            pass

        path = unquote(getattr(request, 'path', '') or '')
        problem_usage_key = data.get('problem_id') or data.get('usage_id') or data.get('module_id') or data.get('id')
        if not problem_usage_key and 'block-v1:' in path:
            idx = path.find('block-v1:')
            problem_usage_key = path[idx:].split('/')[0]
        course_id = data.get('course_id') or data.get('course_key')
        if not course_id and problem_usage_key and 'block-v1:' in problem_usage_key:
            # block-v1:ORG+COURSE+RUN+type@problem+block@...
            try:
                parts = problem_usage_key.split('+')
                if len(parts) >= 3:
                    course_id = '+'.join(parts[:3]).replace('block-v1:', 'course-v1:')
            except Exception:
                course_id = None
        return course_id, problem_usage_key
