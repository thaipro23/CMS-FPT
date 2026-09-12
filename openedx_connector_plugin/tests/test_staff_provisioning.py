from __future__ import annotations

import importlib.util
from pathlib import Path
import unittest


MODULE = Path(__file__).resolve().parents[1] / 'openedx_ai_connector' / 'staff_provisioning.py'
RESOLVE_MODULE = MODULE.with_name('student_insight.py')


class FakeUser:
    def __init__(self, *, is_staff=False, is_superuser=False, persist=True):
        self.is_staff = is_staff
        self.is_superuser = is_superuser
        self.persist = persist
        self.saved_fields = []

    def save(self, *, update_fields):
        self.saved_fields.append(tuple(update_fields))
        if not self.persist:
            self.is_staff = False

    def refresh_from_db(self, *, fields):
        self.saved_fields.append(('refresh', *fields))
        if not self.persist:
            self.is_staff = False


class StaffProvisioningTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        spec = importlib.util.spec_from_file_location('staff_provisioning_under_test', MODULE)
        cls.module = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(cls.module)

    def test_required_staff_is_persisted_without_superuser_escalation(self):
        user = FakeUser()

        result = self.module.ensure_required_cms_staff(user, required=True)

        self.assertTrue(user.is_staff)
        self.assertFalse(user.is_superuser)
        self.assertTrue(result['is_staff'])
        self.assertFalse(result['is_superuser'])
        self.assertTrue(result['staff_updated'])
        self.assertIn(('is_staff',), user.saved_fields)

    def test_non_staff_request_does_not_change_existing_user(self):
        user = FakeUser()

        result = self.module.ensure_required_cms_staff(user, required=False)

        self.assertFalse(user.is_staff)
        self.assertEqual(user.saved_fields, [])
        self.assertFalse(result['staff_updated'])

    def test_required_staff_fails_when_database_does_not_persist_it(self):
        user = FakeUser(persist=False)

        with self.assertRaisesRegex(RuntimeError, 'is_staff'):
            self.module.ensure_required_cms_staff(user, required=True)

    def test_staff_request_requires_explicit_boolean_true(self):
        self.assertTrue(self.module.cms_staff_requested({'require_staff': True}))
        for payload in ({}, {'require_staff': False}, {'require_staff': 'true'}, None):
            with self.subTest(payload=payload):
                self.assertFalse(self.module.cms_staff_requested(payload))

    def test_resolve_users_wires_staff_verification_into_response(self):
        source = RESOLVE_MODULE.read_text(encoding='utf-8')
        self.assertIn("CONNECTOR_VERSION = '25.9.16.5.101'", source)
        self.assertIn('ensure_required_cms_staff(', source)
        self.assertIn("item.get('person_type') == 'teacher'", source)
        self.assertIn('**staff_state', source)


if __name__ == '__main__':
    unittest.main()
