"""Standalone plugin tests: real timer persistence, stubbed LMS grade reset."""
import json
from types import SimpleNamespace
from unittest.mock import patch

import django
from django.conf import settings

if not settings.configured:
    settings.configure(SECRET_KEY='analytics-test', AI_CONNECTOR_HMAC_SECRET='timer-hmac-test', USE_TZ=True,
        INSTALLED_APPS=['django.contrib.auth', 'django.contrib.contenttypes', 'openedx_unit_reset'],
        DATABASES={'default': {'ENGINE': 'django.db.backends.sqlite3', 'NAME': ':memory:'}},
        CACHES={'default': {'BACKEND': 'django.core.cache.backends.locmem.LocMemCache'}})
django.setup()

import pytest
from django.contrib.auth import get_user_model
from django.core.management import call_command
from django.test import RequestFactory
from django.utils import timezone
from lxml import etree
from openedx_unit_reset import analytics, services, views
from openedx_unit_reset.models import UnitQuizSession, UnitQuizTimerConfig

COURSE = 'course-v1:FPL+SUB+FA26'
UNIT = 'block-v1:FPL+SUB+FA26+type@vertical+block@quiz'


@pytest.fixture
def timer():
    if settings.DATABASES['default']['NAME'] != ':memory:':
        pytest.skip('Standalone tests require an in-memory database')
    call_command('migrate', verbosity=0)
    UnitQuizSession.objects.all().delete()
    UnitQuizTimerConfig.objects.all().delete()
    get_user_model().objects.all().delete()
    user = get_user_model().objects.create(username='learner')
    UnitQuizTimerConfig.objects.create(course_id=COURSE, unit_usage_key=UNIT,
        sequence_usage_key='sequence', title='Quiz 1', duration_seconds=900, cooldown_seconds=0)
    request = RequestFactory().post('/start', json.dumps({'course_id': COURSE,
        'unit_usage_key': UNIT, 'started_at': '1900-01-01', 'unit_reset_nonce': 'client-value'}),
        content_type='application/json')
    request.user = user
    request._dont_enforce_csrf_checks = True
    return request


def test_start_refresh_emits_actual_persisted_session_without_extending_timer(timer):
    with patch.object(services, 'assert_user_can_reset'), patch('openedx_unit_reset.analytics.emit_quiz_session_start') as emit:
        assert views.quiz_session_start(timer).status_code == 200
        before = UnitQuizSession.objects.values().get()
        assert views.quiz_session_start(timer).status_code == 200
        assert UnitQuizSession.objects.values().get() == before
        results = [call.args[0] for call in emit.call_args_list]
    captured = []
    for result in results:
        assert analytics.emit_quiz_session_start(result, emit=lambda kind, payload: captured.append(payload))
    assert captured[0] == captured[1]
    assert captured[0]['started_at'] == before['started_at'].isoformat()
    assert captured[0]['unit_reset_nonce'] == 'quiz-session:' + str(before['id'])
    assert captured[0]['unit_usage_key'] == UNIT


def test_failed_reset_does_not_emit_a_successful_start(timer):
    with (patch.object(views, 'reset_quiz_session_for_current_user', side_effect=services.UnitResetError('failed')),
          patch('openedx_unit_reset.analytics.emit_quiz_session_start') as emit):
        assert views.quiz_session_reset(timer).status_code != 200
        emit.assert_not_called()


def test_successful_reset_preserves_native_reset_result_and_emits_new_session(timer):
    with patch.object(services, 'assert_user_can_reset'), patch('openedx_unit_reset.analytics.emit_quiz_session_start') as emit:
        assert views.quiz_session_start(timer).status_code == 200
        prior = UnitQuizSession.objects.get()
        prior.expires_at = timezone.now()
        prior.reset_available_at = timezone.now()
        prior.status = UnitQuizSession.STATUS_RESET_READY
        prior.save()
        with patch.object(services, 'reset_unit_for_current_user', return_value={'success': True, 'native_grade_reset': 'preserved'}) as reset:
            response = views.quiz_session_reset(timer)
        assert response.status_code == 200
        reset.assert_called_once()
        result = emit.call_args.args[0]
        assert emit.call_args.kwargs == {'reset': True}
    assert UnitQuizSession.objects.count() == 2
    captured = []
    assert analytics.emit_quiz_session_start(result, reset=True, emit=lambda kind, payload: captured.append(payload))
    assert captured[0]['reset_request'] is True
    assert captured[0]['unit_reset_nonce'] != 'quiz-session:' + str(prior.pk)


def test_real_capa_generated_response_and_group_label_ids_do_not_change_clone_identity():
    hashes = []
    for name in ('clonea', 'cloneb'):
        root = etree.fromstring(f'<problem><p>Prompt</p><multiplechoiceresponse id="{name}_1"><label id="multiinput-group-label-{name}_1">Choose</label><choicegroup id="{name}_2_1" multiinput-group-label-id="multiinput-group-label-{name}_1"><choice>A</choice></choicegroup></multiplechoiceresponse></problem>')
        block = SimpleNamespace(data='<problem>same original definition</problem>',
            location=name, get_parent=lambda: SimpleNamespace(category='vertical', location=UNIT),
            lcp=SimpleNamespace(responders={'r': SimpleNamespace(xml=root[1], answer_ids=[name + '_2_1'])}))
        payload = {'submission': {name + '_2_1': {'answer': 'A', 'correct': True}}}
        result = analytics.enrich_problem_check(block, payload)
        hashes.append(result['submission'][name + '_2_1']['question_hash'])
        assert result['unit_usage_key'] == UNIT and result['content_version'].startswith('sha256:')
        assert 'content_version' not in payload
    assert hashes[0] == hashes[1]
