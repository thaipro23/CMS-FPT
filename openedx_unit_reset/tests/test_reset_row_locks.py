"""Opt-in concurrency tests on a disposable PostgreSQL/MySQL reset_test_* DB.

SQLite cannot prove SELECT FOR UPDATE behavior. API boundaries are replaced,
but reset mutations, reconciliation and database locks are production code.
"""
import sys
import threading
from datetime import timedelta
from types import ModuleType, SimpleNamespace
import unittest
from unittest.mock import patch
from uuid import uuid4

from test_reset_runtime import SavedModule, COURSE
from django.contrib.auth import get_user_model
from django.core.management import call_command
from django.db import close_old_connections, connection, models, connections
from django.conf import settings
from django.utils import timezone
from openedx_unit_reset import services, tasks
from openedx_unit_reset.models import UnitResetCourseGradeLock, UnitResetGradeReconciliation, UnitQuizTimerConfig, UnitQuizSession


class SavedTotal(models.Model):
    user = models.ForeignKey(settings.AUTH_USER_MODEL, on_delete=models.CASCADE)
    value = models.FloatField(default=0)

    class Meta:
        app_label = 'reset_runtime_tests'


class ResetRowLockTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        if not connection.features.has_select_for_update or not str(connection.settings_dict['NAME']).startswith('reset_test_'):
            raise unittest.SkipTest('Requires disposable reset_test_* database with real row locks')
        call_command('migrate', verbosity=0)
        tables = connection.introspection.table_names()
        cls.created_models = [model for model in (SavedModule, SavedTotal) if model._meta.db_table not in tables]
        with connection.schema_editor() as editor:
            for model in cls.created_models:
                editor.create_model(model)

    @classmethod
    def tearDownClass(cls):
        with connection.schema_editor() as editor:
            for model in reversed(cls.created_models):
                editor.delete_model(model)

    def setUp(self):
        self.user = get_user_model().objects.create(username='rowlock-'+uuid4().hex, is_staff=True)
        self.unit = COURSE.make_usage_key('vertical', 'quiz')
        self.problem = COURSE.make_usage_key('problem', 'question')
        SavedModule.objects.create(student=self.user, course_id=str(COURSE), module_state_key=self.problem, grade=10, max_grade=10)
        SavedTotal.objects.create(user=self.user, value=10)
        UnitResetCourseGradeLock.objects.create(user=self.user, course_id=str(COURSE))
        self.first = UnitResetGradeReconciliation.objects.create(user=self.user, course_id=str(COURSE), unit_usage_key=str(self.unit))
        self.started = threading.Event()
        self.release = threading.Event()
        self.second_started = threading.Event()
        self.errors = []
        self.threads = []
        self.force_repeatable_read = False
        factory = ModuleType('lms.djangoapps.grades.course_grade_factory')
        factory.CourseGradeFactory = lambda: SimpleNamespace(update=self.update_grade)
        api = ModuleType('lms.djangoapps.grades.models_api')
        api.clear_prefetched_course_grades = lambda course: None
        self.modules = patch.dict(sys.modules, {factory.__name__: factory, api.__name__: api})
        self.modules.start()

    def tearDown(self):
        self.release.set()
        for thread in self.threads:
            thread.join(10)
            self.assertFalse(thread.is_alive(), 'Worker failed to release lock')
        self.modules.stop()
        SavedModule.objects.filter(student=self.user).delete()
        SavedTotal.objects.filter(user=self.user).delete()
        self.user.delete()

    def update_grade(self, **kwargs):
        total = SavedModule.objects.filter(student=self.user).aggregate(total=models.Sum('grade'))['total'] or 0
        if threading.current_thread().name == 'old-grade':
            self.started.set()
            if not self.release.wait(10):
                raise RuntimeError('Timed out waiting to release grade computation')
        else:
            self.second_started.set()
        SavedTotal.objects.filter(user=self.user).update(value=total)
        return SimpleNamespace(percent=total/10, letter_grade='Pass' if total else 'Fail')

    def task(self, row):
        task = tasks.reconcile_unit_reset_grade
        task.push_request(id=threading.current_thread().name, retries=2)
        try:
            task.run(row.id)
        finally:
            task.pop_request()

    def start(self, name, action):
        def run():
            close_old_connections()
            try:
                if self.force_repeatable_read:
                    sql = ('SET SESSION CHARACTERISTICS AS TRANSACTION ISOLATION LEVEL REPEATABLE READ'
                           if connection.vendor == 'postgresql' else 'SET SESSION TRANSACTION ISOLATION LEVEL REPEATABLE READ')
                    with connection.cursor() as cursor:
                        cursor.execute(sql)
                action()
            except Exception as exc:
                self.errors.append(exc)
            finally:
                connections.close_all()
        thread = threading.Thread(name=name, target=run, daemon=True)
        self.threads.append(thread)
        thread.start()
        return thread

    def test_different_reconciliation_rows_do_not_compute_concurrently(self):
        second = UnitResetGradeReconciliation.objects.create(user=self.user, course_id=str(COURSE), unit_usage_key=str(self.unit))
        old = self.start('old-grade', lambda: self.task(self.first))
        self.assertTrue(self.started.wait(5))
        new = self.start('new-grade', lambda: self.task(second))
        self.assertFalse(self.second_started.wait(.2))
        self.release.set()
        old.join(10)
        new.join(10)
        self.assertEqual(self.errors, [])
        self.assertTrue(self.second_started.is_set())

    def test_reset_waits_for_old_rebuild_and_latest_rebuild_persists_zero(self):
        blocks = {self.unit: SimpleNamespace(category='vertical', has_children=True, children=[self.problem]),
                  self.problem: SimpleNamespace(category='problem', has_children=False, children=[])}
        finished = threading.Event()
        request = SimpleNamespace(user=self.user, META={})
        def reset_api(course, user, target, **kwargs):
            SavedModule.objects.get(student=user, module_state_key=target).delete()
        def reset():
            services.reset_unit_for_current_user(request, str(COURSE), str(self.unit))
            finished.set()
        with patch.object(services, 'get_modulestore', return_value=SimpleNamespace(get_item=lambda key: blocks[key])), \
             patch.object(services, 'get_student_module_model', return_value=SavedModule), \
             patch.object(services, 'get_block_cooldown_seconds', return_value=0), \
             patch.object(services, 'get_reset_student_attempts', return_value=reset_api), \
             patch.object(services, 'get_completion_reset_handler', return_value=lambda **kwargs: None), \
             patch.object(services, 'dispatch_grade_reconciliation'):
            old = self.start('old-grade', lambda: self.task(self.first))
            self.assertTrue(self.started.wait(5))
            reset_thread = self.start('reset', reset)
            self.assertFalse(finished.wait(.2))
            self.release.set()
            old.join(10)
            reset_thread.join(10)
        self.assertEqual(self.errors, [])
        self.assertTrue(finished.is_set())
        self.task(UnitResetGradeReconciliation.objects.filter(user=self.user).latest('created_at'))
        self.assertEqual(SavedTotal.objects.get(user=self.user).value, 0)

    def test_repeatable_read_waiter_rejects_reset_of_new_active_attempt(self):
        self.force_repeatable_read = True
        config = UnitQuizTimerConfig.objects.create(course_id=str(COURSE), unit_usage_key=str(self.unit), cooldown_seconds=300)
        UnitQuizSession.objects.create(user=self.user, config=config, course_id=str(COURSE), unit_usage_key=str(self.unit),
                                       status='RESET_READY', started_at=timezone.now()-timedelta(hours=1),
                                       expires_at=timezone.now()-timedelta(minutes=30), reset_available_at=timezone.now()-timedelta(minutes=20))
        blocks = {self.unit: SimpleNamespace(category='vertical', has_children=True, children=[self.problem]),
                  self.problem: SimpleNamespace(category='problem', has_children=False, children=[])}
        rejected = threading.Event()
        request = SimpleNamespace(user=self.user, META={})
        def reset_api(course, user, target, **kwargs):
            SavedModule.objects.get(student=user, module_state_key=target).delete()
        def completion(**kwargs):
            if threading.current_thread().name == 'first-reset':
                self.started.set()
                if not self.release.wait(10):
                    raise RuntimeError('Reset timeout')
        def second_reset():
            try:
                services.reset_quiz_session_for_current_user(request, str(COURSE), str(self.unit))
            except services.ResetCooldownError:
                rejected.set()
        with patch.object(services, 'get_modulestore', return_value=SimpleNamespace(get_item=lambda key: blocks[key])), \
             patch.object(services, 'get_student_module_model', return_value=SavedModule), \
             patch.object(services, 'get_reset_student_attempts', return_value=reset_api), \
             patch.object(services, 'get_completion_reset_handler', return_value=completion), \
             patch.object(services, 'dispatch_grade_reconciliation'):
            first = self.start('first-reset', lambda: services.reset_quiz_session_for_current_user(request, str(COURSE), str(self.unit)))
            self.assertTrue(self.started.wait(5))
            second = self.start('second-reset', second_reset)
            self.assertFalse(rejected.wait(.2))
            self.release.set()
            first.join(10)
            second.join(10)
        self.assertEqual(self.errors, [])
        self.assertTrue(rejected.is_set())
        self.assertEqual(UnitQuizSession.objects.filter(user=self.user, config=config, status='ACTIVE').count(), 1)
