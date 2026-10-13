"""Standalone Django reset tests; only the Open edX API boundary is replaced.

Run in a fresh Python process with openedx_unit_reset on PYTHONPATH.
"""
from datetime import timedelta
import json
import sys
from types import SimpleNamespace, ModuleType
import unittest
from unittest.mock import patch
from celery.exceptions import Retry

import django
from django.conf import settings

if not settings.configured:
    settings.configure(
        SECRET_KEY='reset-tests-only', USE_TZ=True,
        INSTALLED_APPS=['django.contrib.auth', 'django.contrib.contenttypes', 'openedx_unit_reset'],
        DATABASES={'default': {'ENGINE': 'django.db.backends.sqlite3', 'NAME': ':memory:'}},
        DEFAULT_AUTO_FIELD='django.db.models.BigAutoField',
    )
django.setup()

from django.contrib.auth import get_user_model
from django.core.management import call_command
from django.db import connection, models, transaction
from django.utils import timezone
from opaque_keys.edx.keys import CourseKey, UsageKey
from openedx_unit_reset import services, tasks
from openedx_unit_reset.models import UnitResetGradeReconciliation, UnitResetControl, UnitQuizTimerConfig, UnitQuizSession

COURSE = CourseKey.from_string('course-v1:FPL+TEST+FA26')


class UsageField(models.CharField):
    def get_prep_value(self, value):
        return str(value) if value is not None else None

    def from_db_value(self, value, expression, connection):
        return UsageKey.from_string(value) if value else value


class SavedModule(models.Model):
    student = models.ForeignKey(settings.AUTH_USER_MODEL, on_delete=models.CASCADE)
    course_id = models.CharField(max_length=255)
    module_state_key = UsageField(max_length=255)
    state = models.TextField(default='{}')
    grade = models.FloatField(null=True)
    max_grade = models.FloatField(null=True)
    modified = models.DateTimeField(default=timezone.now)

    class Meta:
        app_label = 'reset_runtime_tests'


class SavedCompletion(models.Model):
    user = models.ForeignKey(settings.AUTH_USER_MODEL, on_delete=models.CASCADE)
    block_key = UsageField(max_length=255)
    value = models.FloatField(default=1)

    class Meta:
        app_label = 'reset_runtime_tests'


class ResetRuntimeTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        if settings.DATABASES['default']['NAME'] != ':memory:':
            raise unittest.SkipTest('Never run standalone reset tests on a persistent database')
        call_command('migrate', verbosity=0)
        with connection.schema_editor() as editor:
            editor.create_model(SavedModule)
            editor.create_model(SavedCompletion)

    def setUp(self):
        UnitQuizTimerConfig.objects.all().delete()
        SavedModule.objects.all().delete()
        SavedCompletion.objects.all().delete()
        get_user_model().objects.all().delete()
        self.user = get_user_model().objects.create(username='learner', is_staff=True)
        self.unit = COURSE.make_usage_key('vertical', 'quiz2')
        self.bank = COURSE.make_usage_key('itembank', 'bank2')
        self.selected = COURSE.make_usage_key('problem', 'selected2')
        self.unselected = COURSE.make_usage_key('problem', 'unselected2')
        self.other = COURSE.make_usage_key('problem', 'quiz1')
        self.final = COURSE.make_usage_key('problem', 'final')
        self.blocks = {
            self.unit: SimpleNamespace(category='vertical', has_children=True, children=[self.bank]),
            self.bank: SimpleNamespace(category='itembank', has_children=True, children=[self.selected, self.unselected]),
        }
        for key in (self.selected, self.unselected, self.other, self.final):
            self.blocks[key] = SimpleNamespace(category='problem', has_children=False, children=[])
            SavedModule.objects.create(student=self.user, course_id=str(COURSE), module_state_key=key, grade=10, max_grade=10)
            SavedCompletion.objects.create(user=self.user, block_key=key)
        SavedModule.objects.create(student=self.user, course_id=str(COURSE), module_state_key=self.unit)
        SavedModule.objects.create(student=self.user, course_id=str(COURSE), module_state_key=self.bank,
                                   state=json.dumps({'selected': [['problem', 'selected2']]}))
        self.request = SimpleNamespace(user=self.user, META={})
        self.dispatched = []
        self.reset_calls = []
        self.patches = [
            patch.object(services, 'get_student_module_model', return_value=SavedModule),
            patch.object(services, 'get_modulestore', return_value=SimpleNamespace(get_item=lambda key: self.blocks[key])),
            patch.object(services, 'get_block_cooldown_seconds', return_value=0),
            patch.object(services, 'get_reset_student_attempts', return_value=self.official_reset),
            patch.object(services, 'get_completion_reset_handler', return_value=self.reset_completion, create=True),
            patch.object(services, 'dispatch_grade_reconciliation', side_effect=self.dispatched.append),
        ]
        for item in self.patches:
            item.start()
            self.addCleanup(item.stop)

    def official_reset(self, course, user, target, **kwargs):
        self.assertFalse(kwargs['emit_signals_and_events'])
        self.reset_calls.append(target)
        row = SavedModule.objects.get(student=user, course_id=str(course), module_state_key=target)
        row.delete()

    def reset(self):
        return services.reset_unit_for_current_user(self.request, str(COURSE), str(self.unit))

    def reset_completion(self, sender, user_id, course_id, usage_id, score_deleted):
        self.assertTrue(score_deleted)
        SavedCompletion.objects.filter(user_id=user_id, block_key=UsageKey.from_string(usage_id)).update(value=0)

    def test_selected_quiz_reset_preserves_quiz_one_final_and_unselected_problem(self):
        before = {key: SavedModule.objects.values().get(module_state_key=key)
                  for key in (self.other, self.final, self.unselected)}
        with transaction.atomic():
            result = self.reset()
            self.assertEqual(self.dispatched, [])
            self.assertEqual(result['grade_reconciliation']['status'], 'PENDING')
        self.assertEqual(len(self.dispatched), 1)
        self.assertEqual(self.reset_calls, [self.selected])
        self.assertFalse(SavedModule.objects.filter(module_state_key__in=[self.unit, self.bank, self.selected]).exists())
        for key, original in before.items():
            self.assertEqual(SavedModule.objects.values().get(module_state_key=key), original)

    def test_selected_completion_resets_without_changing_other_quiz_or_final(self):
        self.reset()
        self.assertEqual(SavedCompletion.objects.get(block_key=self.selected).value, 0)
        for key in (self.other, self.final, self.unselected):
            self.assertEqual(SavedCompletion.objects.get(block_key=key).value, 1)

    def test_outer_rollback_preserves_scores_and_publishes_no_task(self):
        before = list(SavedModule.objects.order_by('id').values())
        with self.assertRaisesRegex(RuntimeError, 'rollback'):
            with transaction.atomic():
                self.reset()
                raise RuntimeError('rollback')
        self.assertEqual(self.dispatched, [])
        self.assertEqual(list(SavedModule.objects.order_by('id').values()), before)
        self.assertEqual(UnitResetGradeReconciliation.objects.count(), 0)
        self.assertEqual(SavedCompletion.objects.filter(value=0).count(), 0)

    def test_failed_official_reset_rolls_back_before_cleaning_containers(self):
        before = list(SavedModule.objects.order_by('id').values())
        def failure(*args, **kwargs):
            self.official_reset(*args, **kwargs)
            raise RuntimeError('official reset failed')
        with patch.object(services, 'get_reset_student_attempts', return_value=failure):
            with self.assertRaisesRegex(RuntimeError, 'official reset failed'):
                self.reset()
        self.assertEqual(self.dispatched, [])
        self.assertEqual(list(SavedModule.objects.order_by('id').values()), before)

    def test_selected_key_moved_to_other_unit_is_rejected_without_deleting_points(self):
        SavedModule.objects.filter(module_state_key=self.bank).update(
            state=json.dumps({'selected': [['problem', 'final']]}))
        with self.assertRaises(services.UnitResetError):
            self.reset()
        self.assertTrue(SavedModule.objects.filter(module_state_key=self.final).exists())
        self.assertEqual(self.dispatched, [])

    def test_transient_lookup_cannot_promote_outside_unit_selection_to_reset_target(self):
        SavedModule.objects.filter(module_state_key=self.bank).update(
            state=json.dumps({'selected': [['problem', 'final']]}))
        def lookup(key):
            if key == self.final:
                raise RuntimeError('modulestore temporarily unavailable')
            return self.blocks[key]
        with patch.object(services, 'get_modulestore', return_value=SimpleNamespace(get_item=lookup)):
            with self.assertRaises(services.UnitResetError):
                self.reset()
        self.assertTrue(SavedModule.objects.filter(module_state_key=self.final).exists())
        self.assertEqual(self.dispatched, [])

    def test_scored_container_cannot_be_deleted_by_raw_cleanup(self):
        SavedModule.objects.filter(module_state_key=self.bank).update(grade=8, max_grade=10)
        with self.assertRaises(services.UnitResetError):
            self.reset()
        self.assertTrue(SavedModule.objects.filter(module_state_key=self.bank, grade=8).exists())
        self.assertTrue(SavedModule.objects.filter(module_state_key=self.selected).exists())

    def test_new_timer_failure_rolls_back_reset_and_sends_no_grade_task(self):
        config = UnitQuizTimerConfig.objects.create(course_id=str(COURSE), unit_usage_key=str(self.unit), cooldown_seconds=0)
        UnitQuizSession.objects.create(user=self.user, config=config, course_id=str(COURSE), unit_usage_key=str(self.unit),
                                       status=UnitQuizSession.STATUS_RESET_READY, started_at=timezone.now()-timedelta(hours=1),
                                       expires_at=timezone.now()-timedelta(minutes=30), reset_available_at=timezone.now()-timedelta(minutes=20))
        before = list(SavedModule.objects.order_by('id').values())
        with patch.object(UnitQuizSession.objects, 'create', side_effect=RuntimeError('new timer failed')):
            with self.assertRaisesRegex(RuntimeError, 'new timer failed'):
                services.reset_quiz_session_for_current_user(self.request, str(COURSE), str(self.unit))
        self.assertEqual(list(SavedModule.objects.order_by('id').values()), before)
        self.assertEqual(self.dispatched, [])
        self.assertEqual(UnitResetGradeReconciliation.objects.count(), 0)

    def test_quiz_reset_locks_course_before_reading_config_and_current_attempt(self):
        config = UnitQuizTimerConfig.objects.create(course_id=str(COURSE), unit_usage_key=str(self.unit), cooldown_seconds=0)
        events = []
        acquire = services.UnitResetCourseGradeLock.acquire
        get_config = services._get_timer_config
        get_session = services._latest_quiz_session
        def lock(*args):
            events.append('lock')
            return acquire(*args)
        def timer(*args, **kwargs):
            events.append('config')
            self.assertTrue(kwargs['for_update'])
            return get_config(*args, **kwargs)
        def session(*args, **kwargs):
            self.assertTrue(kwargs['for_update'])
            return get_session(*args, **kwargs)
        with patch.object(services.UnitResetCourseGradeLock, 'acquire', side_effect=lock), \
             patch.object(services, '_get_timer_config', side_effect=timer), \
             patch.object(services, '_latest_quiz_session', side_effect=session):
            result = services.reset_quiz_session_for_current_user(self.request, str(COURSE), str(self.unit))
        self.assertEqual(events[:2], ['lock', 'config'])
        self.assertTrue(result['success'])
        self.assertEqual(UnitQuizSession.objects.filter(user=self.user, config=config, status='ACTIVE').count(), 1)

    def _new_guard_session(self, config, status, attempt):
        return UnitQuizSession.objects.create(
            user=self.user,
            config=config,
            course_id=str(COURSE),
            unit_usage_key=str(self.unit),
            status=status,
            attempt_no=attempt,
            started_at=timezone.now() - timedelta(hours=2),
            expires_at=timezone.now() - timedelta(hours=1),
            reset_available_at=timezone.now() - timedelta(minutes=30),
        )

    def test_guard_memoizes_xblock_lookups_and_session_queries(self):
        """Historical attempts must not deserialize the same Unit per session."""
        from django.db import connection
        from django.test.utils import CaptureQueriesContext

        config = UnitQuizTimerConfig.objects.create(
            course_id=str(COURSE), unit_usage_key=str(self.unit), cooldown_seconds=0,
        )
        for i in range(1, 13):
            self._new_guard_session(config, UnitQuizSession.STATUS_RESET_READY, i)

        reads = []
        def get_item(key):
            reads.append(key)
            return self.blocks[key]

        with patch.object(
            services, 'get_modulestore',
            return_value=SimpleNamespace(get_item=get_item),
        ), CaptureQueriesContext(connection) as recorded:
            blocked, session = services.is_late_submit_blocked(
                self.user, str(COURSE), str(self.other),
            )

        self.assertFalse(blocked)
        self.assertIsNone(session)
        # One structural walk, and the active walk reuses its block objects.
        self.assertCountEqual(reads, [self.unit, self.bank, self.selected, self.unselected])
        # The latest attempt for the same config is queried once, not 12 times.
        session_reads = [
            q for q in recorded.captured_queries
            if 'openedx_unit_reset_unitquizsession' in q['sql'].lower()
        ]
        self.assertLessEqual(len(session_reads), 3)

    def test_guard_keeps_read_only_bulk_scope_for_complete_traversal(self):
        """SplitMongo course structure is decoded only within one scoped read."""
        from contextlib import contextmanager

        config = UnitQuizTimerConfig.objects.create(
            course_id=str(COURSE), unit_usage_key=str(self.unit), cooldown_seconds=0,
        )
        for attempt in range(1, 6):
            self._new_guard_session(config, UnitQuizSession.STATUS_RESET_READY, attempt)

        events = []
        scope_active = [False]

        @contextmanager
        def bulk_operations(course_key, emit_signals=True):
            self.assertEqual(str(course_key), str(COURSE))
            self.assertFalse(emit_signals)
            self.assertFalse(scope_active[0])
            scope_active[0] = True
            events.append('enter')
            try:
                yield
            finally:
                scope_active[0] = False
                events.append('exit')

        def get_item(key):
            self.assertTrue(scope_active[0])
            events.append(str(key))
            return self.blocks[key]

        store = SimpleNamespace(
            get_item=get_item,
            bulk_operations=bulk_operations,
        )
        with patch.object(services, 'get_modulestore', return_value=store):
            blocked, session = services.is_late_submit_blocked(
                self.user, str(COURSE), str(self.other),
            )

        self.assertFalse(blocked)
        self.assertIsNone(session)
        self.assertEqual(events.count('enter'), 1)
        self.assertEqual(events.count('exit'), 1)
        self.assertEqual(events[-1], 'exit')
        self.assertFalse(scope_active[0])
        self.assertEqual(len(events) - 2, 4)

    def test_guard_blocks_selected_problem_but_not_unselected_bank_child(self):
        config = UnitQuizTimerConfig.objects.create(
            course_id=str(COURSE), unit_usage_key=str(self.unit), cooldown_seconds=0,
        )
        expired = self._new_guard_session(config, UnitQuizSession.STATUS_RESET_READY, 1)

        blocked, session = services.is_late_submit_blocked(
            self.user, str(COURSE), str(self.selected),
        )
        self.assertTrue(blocked)
        self.assertEqual(session.id, expired.id)

        blocked, session = services.is_late_submit_blocked(
            self.user, str(COURSE), str(self.unselected),
        )
        self.assertFalse(blocked)
        self.assertIsNone(session)

    def test_guard_does_not_block_new_active_retaken_quiz(self):
        config = UnitQuizTimerConfig.objects.create(
            course_id=str(COURSE), unit_usage_key=str(self.unit), cooldown_seconds=0,
        )
        self._new_guard_session(config, UnitQuizSession.STATUS_RESET_READY, 1)
        self._new_guard_session(config, UnitQuizSession.STATUS_ACTIVE, 2)

        # An active newer attempt skips historical expired/reset-ready sessions.
        with patch.object(services, 'collect_active_unit_usage_keys') as traversal:
            blocked, session = services.is_late_submit_blocked(
                self.user, str(COURSE), str(self.selected),
            )
        self.assertFalse(blocked)
        self.assertIsNone(session)
        traversal.assert_not_called()

    def test_guard_preserves_auto_submit_grace_window(self):
        config = UnitQuizTimerConfig.objects.create(
            course_id=str(COURSE), unit_usage_key=str(self.unit), cooldown_seconds=0,
        )
        self._new_guard_session(config, UnitQuizSession.STATUS_EXPIRED, 1)

        with patch.object(services, '_session_allows_auto_submit_grace', return_value=True):
            blocked, session = services.is_late_submit_blocked(
                self.user, str(COURSE), str(self.selected),
            )
        self.assertFalse(blocked)
        self.assertIsNone(session)

    def factory_modules(self, update):
        module = ModuleType('lms.djangoapps.grades.course_grade_factory')
        module.CourseGradeFactory = lambda: SimpleNamespace(update=update)
        api = ModuleType('lms.djangoapps.grades.models_api')
        api.clear_prefetched_course_grades = lambda course: None
        return {module.__name__: module, api.__name__: api}

    def run_task(self, reconciliation, update, retries=0):
        task = tasks.reconcile_unit_reset_grade
        task.push_request(id='grade-task', retries=retries)
        try:
            with patch.dict(sys.modules, self.factory_modules(update)):
                return task.run(reconciliation.id)
        finally:
            task.pop_request()

    def reconciliation(self):
        return UnitResetGradeReconciliation.objects.create(user=self.user, course_id=str(COURSE), unit_usage_key=str(self.unit))

    def test_duplicate_completed_task_does_not_recalculate_again(self):
        row = self.reconciliation()
        totals = []
        def update(**kwargs):
            totals.append(SavedModule.objects.filter(student=self.user).aggregate(total=models.Sum('grade'))['total'])
            return SimpleNamespace(percent=1, letter_grade='Pass')
        self.run_task(row, update)
        self.run_task(row, update)
        row.refresh_from_db()
        self.assertEqual(row.status, 'COMPLETED')
        self.assertEqual(row.attempts, 1)
        self.assertEqual(totals, [40])

    def test_distinct_reconciliations_and_reset_share_stable_course_lock(self):
        from openedx_unit_reset import models as reset_models
        self.assertTrue(hasattr(reset_models, 'UnitResetCourseGradeLock'))
        lock_model = reset_models.UnitResetCourseGradeLock
        used_locks = []
        acquire = lock_model.acquire
        def capture(user_id, course_id):
            self.assertTrue(connection.in_atomic_block)
            lock = acquire(user_id, course_id)
            used_locks.append(lock.pk)
            return lock
        with patch.object(lock_model, 'acquire', side_effect=capture):
            self.reset()
            first = UnitResetGradeReconciliation.objects.latest('created_at')
            second = self.reconciliation()
            factory = lambda **kwargs: SimpleNamespace(percent=0, letter_grade='Fail')
            self.run_task(first, factory)
            self.run_task(second, factory)
        self.assertEqual(len(used_locks), 3)
        self.assertEqual(len(set(used_locks)), 1)

    def test_final_factory_failure_is_durable_and_propagates(self):
        row = self.reconciliation()
        def fail(**kwargs):
            raise RuntimeError('grade rebuild failed')
        with self.assertRaisesRegex(RuntimeError, 'grade rebuild failed'):
            self.run_task(row, fail, retries=2)
        row.refresh_from_db()
        self.assertEqual(row.status, 'FAILED')
        self.assertIn('grade rebuild failed', row.last_error)

    def test_absent_course_grade_is_not_reported_completed(self):
        row = self.reconciliation()
        with self.assertRaises(RuntimeError):
            self.run_task(row, lambda **kwargs: None, retries=2)
        row.refresh_from_db()
        self.assertEqual(row.status, 'FAILED')

    def test_retry_is_visible_then_successful_redelivery_completes(self):
        row = self.reconciliation()
        def fail(**kwargs):
            raise RuntimeError('temporary grade error')
        with patch.object(tasks.reconcile_unit_reset_grade, 'retry', side_effect=Retry('scheduled')):
            with self.assertRaises(Retry):
                self.run_task(row, fail)
        row.refresh_from_db()
        self.assertEqual(row.status, 'RETRYING')
        self.run_task(row, lambda **kwargs: SimpleNamespace(percent=0, letter_grade='Fail'), retries=1)
        row.refresh_from_db()
        self.assertEqual(row.status, 'COMPLETED')
        self.assertEqual(row.attempts, 2)
        self.assertEqual(row.last_error, '')

    def test_failed_retry_publish_cannot_remain_retrying_without_a_task(self):
        row = self.reconciliation()
        def fail(**kwargs):
            raise RuntimeError('temporary grade error')
        with patch.object(tasks.reconcile_unit_reset_grade, 'retry', side_effect=RuntimeError('broker unavailable')):
            with self.assertRaisesRegex(RuntimeError, 'broker unavailable'):
                self.run_task(row, fail)
        row.refresh_from_db()
        self.assertEqual(row.status, 'FAILED')
        self.assertIn('broker unavailable', row.last_error)


if __name__ == '__main__':
    unittest.main()
