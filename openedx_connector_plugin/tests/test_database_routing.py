"""Run without the Open edX dependency stack: python -m unittest discover -s openedx_connector_plugin/tests -p test_database_routing.py."""
import asyncio
import importlib.util
from pathlib import Path
import sqlite3
import unittest

MODULE = Path(__file__).resolve().parents[1] / 'openedx_ai_connector' / 'database.py'


class DatabaseRoutingTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        assert MODULE.exists(), 'Missing replica routing implementation'
        spec = importlib.util.spec_from_file_location('connector_database_under_test', MODULE)
        cls.db = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(cls.db)

    def setUp(self):
        self.router = self.db.ConnectorDatabaseRouter()
        self.databases = {
            'default': {'ENGINE': 'django.db.backends.mysql', 'HOST': 'primary', 'PORT': 3306, 'NAME': 'openedx'},
            'read_replica': {'ENGINE': 'django.db.backends.mysql', 'HOST': 'replica', 'PORT': '3306', 'NAME': 'openedx'},
        }

    def test_default_and_custom_alias_are_validated(self):
        self.assertEqual(self.db.validate_replica_alias(self.databases), 'read_replica')
        self.databases['reporting'] = self.databases.pop('read_replica')
        self.assertEqual(self.db.validate_replica_alias(self.databases, 'reporting'), 'reporting')

    def test_invalid_configuration_never_falls_back_to_primary(self):
        for alias in ('', 'default', 'missing'):
            with self.subTest(alias=alias), self.assertRaises(self.db.ConnectorReplicaError):
                self.db.validate_replica_alias(self.databases, alias)
        self.databases['read_replica']['HOST'] = ' PRIMARY '
        with self.assertRaises(self.db.ConnectorReplicaError):
            self.db.validate_replica_alias(self.databases)
        self.databases['read_replica']['HOST'] = ''
        with self.assertRaises(self.db.ConnectorReplicaError):
            self.db.validate_replica_alias(self.databases)

    def test_router_is_inert_outside_connector_scope(self):
        self.assertIsNone(self.router.db_for_read(str))
        self.assertIsNone(self.router.db_for_write(str))
        self.assertIsNone(self.router.allow_relation(object(), object()))

    def test_report_reads_stay_on_replica_even_after_write_routing(self):
        primary = sqlite3.connect(':memory:')
        replica = sqlite3.connect(':memory:')
        self.addCleanup(primary.close)
        self.addCleanup(replica.close)
        for conn, score in ((primary, 99), (replica, 75)):
            conn.execute('CREATE TABLE grade (score INTEGER)')
            conn.execute('INSERT INTO grade VALUES (?)', (score,))
        conns = {'default': primary, 'reporting': replica}
        with self.db.database_scope('reporting'):
            conn = conns[self.router.db_for_read(str)]
            self.assertEqual(conn.execute('SELECT score FROM grade').fetchone()[0], 75)
            conns[self.router.db_for_write(str)].execute('UPDATE grade SET score = 100')
            conn = conns[self.router.db_for_read(str)]
            self.assertEqual(conn.execute('SELECT score FROM grade').fetchone()[0], 75)
            self.assertEqual(primary.execute('SELECT score FROM grade').fetchone()[0], 100)
            self.assertEqual(self.router.db_for_read(int), 'reporting')
        self.assertEqual(replica.execute('SELECT score FROM grade').fetchone()[0], 75)
        self.assertIsNone(self.router.db_for_read(str))

    def test_primary_scope_nested_scope_and_exception_cleanup(self):
        with self.db.database_scope('reporting'):
            try:
                with self.db.database_scope('default'):
                    self.assertEqual(self.router.db_for_read(str), 'default')
                    self.assertEqual(self.router.db_for_write(str), 'default')
                    raise ValueError('failure')
            except ValueError:
                pass
            self.assertEqual(self.router.db_for_read(str), 'reporting')
        self.assertIsNone(self.router.db_for_write(str))

    def test_primary_reads_decorator_is_scoped_and_restores_replica(self):
        @self.db.primary_reads
        def read_primary():
            return self.router.db_for_read(str)

        with self.db.database_scope('reporting'):
            self.assertEqual(read_primary(), 'default')
            self.assertEqual(self.router.db_for_read(str), 'reporting')

    def test_concurrent_requests_do_not_share_database_or_write_pins(self):
        async def request(alias, write):
            with self.db.database_scope(alias):
                if write:
                    self.router.db_for_write(str)
                await asyncio.sleep(0)
                return self.router.db_for_read(str)
        async def run():
            return await asyncio.gather(request('replica_a', True), request('replica_b', False))
        self.assertEqual(asyncio.run(run()), ['replica_a', 'replica_b'])


class DjangoReplicaIntegrationTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        import sys
        import tempfile
        from django.conf import settings
        import django
        sys.path.insert(0, str(MODULE.parents[1]))
        cls.temp = tempfile.TemporaryDirectory()
        cls.addClassCleanup(cls.temp.cleanup)
        settings.configure(
            DATABASES={alias: {'ENGINE': 'django.db.backends.sqlite3', 'NAME': str(Path(cls.temp.name) / (alias + '.sqlite3'))}
                       for alias in ('default', 'read_replica')},
            DATABASE_ROUTERS=['openedx_ai_connector.database.ConnectorDatabaseRouter'],
            INSTALLED_APPS=[],
        )
        django.setup()
        from openedx_ai_connector import database
        from django.db import connections, models
        cls.db = database
        cls.connections = connections
        cls.addClassCleanup(connections.close_all)
        class Grade(models.Model):
            score = models.IntegerField()
            class Meta:
                app_label = 'connector_test'
        cls.Grade = Grade
        for alias, score in (('default', 99), ('read_replica', 75)):
            with connections[alias].schema_editor() as editor:
                editor.create_model(Grade)
            Grade.objects.using(alias).create(score=score)

    def test_reporting_queries_use_replica_and_scope_restores_primary(self):
        @self.db.replica_reads
        def report():
            return self.Grade.objects.get().score
        self.assertEqual(report(), 75)
        self.assertEqual(self.Grade.objects.get().score, 99)

    def test_explicit_primary_read_is_blocked_even_if_caller_swallows_error(self):
        @self.db.replica_reads
        def report():
            try:
                return self.Grade.objects.using('default').get().score
            except Exception:
                return 'silently lost data'
        with self.assertRaises(self.db.ConnectorReplicaError):
            report()
        self.assertEqual(self.Grade.objects.get().score, 99)

    def test_replica_query_error_cannot_be_reported_as_empty_success(self):
        @self.db.replica_reads
        def report():
            try:
                with self.connections['read_replica'].cursor() as cursor:
                    cursor.execute('SELECT * FROM missing_grade_table')
            except Exception:
                return []
        with self.assertRaises(self.db.ConnectorReplicaError):
            report()

    def test_missing_alias_fails_before_report_body_runs(self):
        from django.test import override_settings
        @self.db.replica_reads
        def report():
            self.fail('Report must not run without replica configuration')
        with override_settings(AI_CONNECTOR_READ_DB_ALIAS='missing'):
            with self.assertRaises(self.db.ConnectorReplicaError):
                report()

    def test_missing_router_fails_before_report_body_runs(self):
        from django.test import override_settings
        @self.db.replica_reads
        def report():
            self.fail('Report must not run without scoped routing')
        with override_settings(DATABASE_ROUTERS=[]):
            with self.assertRaises(self.db.ConnectorReplicaError):
                report()

    def test_settings_hook_is_first_and_idempotent_for_existing_routers(self):
        from types import SimpleNamespace
        from openedx_ai_connector.settings.common import plugin_settings
        settings = SimpleNamespace(DATABASE_ROUTERS=['existing.Router'])
        plugin_settings(settings)
        plugin_settings(settings)
        self.assertEqual(settings.DATABASE_ROUTERS, [
            'openedx_ai_connector.database.ConnectorDatabaseRouter', 'existing.Router'])


if __name__ == '__main__':
    unittest.main()
