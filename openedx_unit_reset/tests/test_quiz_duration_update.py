import hashlib
import hmac
import json
import time
import unittest
import uuid
from datetime import timedelta
from unittest.mock import patch

from django.conf import settings

if not settings.configured:
    settings.configure(
        SECRET_KEY='timer-test', AI_CONNECTOR_HMAC_SECRET='timer-hmac-test', USE_TZ=True,
        INSTALLED_APPS=['django.contrib.auth', 'django.contrib.contenttypes', 'openedx_unit_reset'],
        DATABASES={'default': {'ENGINE': 'django.db.backends.sqlite3', 'NAME': ':memory:'}},
        CACHES={'default': {'BACKEND': 'django.core.cache.backends.locmem.LocMemCache'}},
    )

import django
django.setup()

from django.contrib.auth import get_user_model
from django.core.management import call_command
from django.test import RequestFactory
from django.utils import timezone
from openedx_unit_reset import services, views
from openedx_unit_reset.models import UnitQuizSession, UnitQuizTimerConfig

COURSE = 'course-v1:FPL+SUB+FA26'
UNIT = 'block-v1:FPL+SUB+FA26+type@vertical+block@quiz'
PATH = '/api/unit-reset/v1/quiz-config/duration'


class QuizDurationUpdateTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        super().setUpClass()
        if settings.DATABASES['default']['NAME'] != ':memory:':
            raise unittest.SkipTest('Run these standalone tests with an in-memory Django database.')
        call_command('migrate', verbosity=0)

    def setUp(self):
        UnitQuizSession.objects.all().delete()
        UnitQuizTimerConfig.objects.all().delete()
        get_user_model().objects.all().delete()
        self.user = get_user_model().objects.create(username='learner')
        self.config = UnitQuizTimerConfig.objects.create(
            course_id=COURSE, unit_usage_key=UNIT, sequence_usage_key='sequence', title='Quiz 1',
            duration_seconds=900, cooldown_seconds=300, lock_after_timeout=False,
            auto_submit_on_timeout=False, metadata_json={'question_ids': ['q1']})
        now = timezone.now()
        self.session = UnitQuizSession.objects.create(
            user=self.user, config=self.config, course_id=COURSE, unit_usage_key=UNIT,
            duration_seconds=900, cooldown_seconds=300, started_at=now,
            expires_at=now + timedelta(seconds=900), timeout_payload={'answer': 'A', 'score': 10})

    def test_only_config_duration_changes_and_existing_session_is_identical(self):
        before_config = UnitQuizTimerConfig.objects.values().get(pk=self.config.pk)
        before_session = UnitQuizSession.objects.values().get(pk=self.session.pk)
        result = services.update_unit_quiz_duration(
            course_id=COURSE, unit_usage_key=UNIT, duration_seconds=1800, actor='admin')
        after_config = UnitQuizTimerConfig.objects.values().get(pk=self.config.pk)
        self.assertEqual(UnitQuizSession.objects.values().get(pk=self.session.pk), before_session)
        for key, value in before_config.items():
            if key not in ('duration_seconds', 'updated_at', 'updated_by'):
                self.assertEqual(after_config[key], value, key)
        self.assertEqual(after_config['duration_seconds'], 1800)
        self.assertEqual(result['previous_duration_seconds'], 900)
        self.assertEqual(UnitQuizTimerConfig.objects.count(), 1)

    def test_missing_config_is_not_created(self):
        with self.assertRaises(services.UnitResetError) as caught:
            services.update_unit_quiz_duration(
                course_id=COURSE, unit_usage_key=UNIT.replace('block@quiz', 'block@missing'),
                duration_seconds=1800)
        self.assertEqual(caught.exception.status_code, 404)
        self.assertEqual(UnitQuizTimerConfig.objects.count(), 1)

    def test_new_session_uses_new_duration_while_current_session_keeps_old_expiry(self):
        services.update_unit_quiz_duration(course_id=COURSE, unit_usage_key=UNIT, duration_seconds=1800)
        with patch.object(services, 'assert_user_can_reset'):
            request = RequestFactory().post('/start')
            request.user = self.user
            active = services.start_quiz_session_for_current_user(request, COURSE, UNIT)
            self.assertEqual(active['duration_seconds'], 900)
            request.user = get_user_model().objects.create(username='new-learner')
            future = services.start_quiz_session_for_current_user(request, COURSE, UNIT)
            self.assertEqual(future['duration_seconds'], 1800)
            session = UnitQuizSession.objects.get(user=request.user)
            self.assertEqual((session.expires_at - session.started_at).total_seconds(), 1800)

    def test_invalid_seconds_are_rejected(self):
        for value in (None, 0, -1, 18001, 1.5, True, '1800'):
            with self.subTest(value=value), self.assertRaises(services.UnitResetError):
                services.update_unit_quiz_duration(
                    course_id=COURSE, unit_usage_key=UNIT, duration_seconds=value)
        self.config.refresh_from_db()
        self.assertEqual(self.config.duration_seconds, 900)

    def test_disabled_and_native_timers_are_not_enabled_or_converted(self):
        for enabled, native in ((False, False), (True, True)):
            UnitQuizTimerConfig.objects.filter(pk=self.config.pk).update(enabled=enabled, native_timed_exam=native)
            with self.assertRaises(services.UnitResetError) as caught:
                services.update_unit_quiz_duration(course_id=COURSE, unit_usage_key=UNIT, duration_seconds=1800)
            self.assertEqual(caught.exception.status_code, 409)

    def signed_request(self, payload):
        body = json.dumps(payload).encode()
        timestamp, nonce = str(int(time.time())), uuid.uuid4().hex
        message = f'{timestamp}.POST.{PATH}.{hashlib.sha256(body).hexdigest()}.{nonce}'
        signature = hmac.new(settings.AI_CONNECTOR_HMAC_SECRET.encode(), message.encode(), hashlib.sha256).hexdigest()
        return RequestFactory().post(PATH, body, content_type='application/json',
            HTTP_X_AI_CONNECTOR_TIMESTAMP=timestamp, HTTP_X_AI_CONNECTOR_NONCE=nonce,
            HTTP_X_AI_CONNECTOR_SIGNATURE=signature)

    def test_hmac_update_and_replay_protection(self):
        request = self.signed_request({'course_id': COURSE, 'unit_usage_key': UNIT,
                                       'duration_seconds': 1800, 'actor': 'admin'})
        self.assertEqual(views.quiz_timer_duration_update(request).status_code, 200)
        self.assertEqual(views.quiz_timer_duration_update(request).status_code, 403)
        self.session.refresh_from_db()
        self.assertEqual(self.session.duration_seconds, 900)

    def test_staff_cookie_without_hmac_cannot_update(self):
        request = RequestFactory().post(PATH, '{}', content_type='application/json')
        request.user = self.user
        self.user.is_staff = True
        self.assertEqual(views.quiz_timer_duration_update(request).status_code, 403)

    def test_non_object_request_returns_400(self):
        self.assertEqual(views.quiz_timer_duration_update(self.signed_request([])).status_code, 400)
