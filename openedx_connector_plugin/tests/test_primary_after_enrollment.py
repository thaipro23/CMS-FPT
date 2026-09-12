from __future__ import annotations

import importlib.util
from pathlib import Path
import unittest


PLUGIN = Path(__file__).resolve().parents[1] / 'openedx_ai_connector'
POLICY_MODULE = PLUGIN / 'read_consistency.py'


class PrimaryAfterEnrollmentTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        spec = importlib.util.spec_from_file_location('read_consistency_under_test', POLICY_MODULE)
        cls.module = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(cls.module)

    def test_only_exact_signed_value_selects_primary(self):
        self.assertEqual(self.module.normalize_analytics_read_consistency('primary_after_enrollment'), 'primary_after_enrollment')
        for value in (None, '', 'primary', 'PRIMARY_AFTER_ENROLLMENT', True):
            with self.subTest(value=value):
                self.assertEqual(self.module.normalize_analytics_read_consistency(value), 'replica')

    def test_class_analytics_wires_primary_and_replica_readers(self):
        source = (PLUGIN / 'student_insight.py').read_text(encoding='utf-8')
        self.assertIn('def _student_learning_results_primary(', source)
        self.assertIn("read_consistency == 'primary_after_enrollment'", source)
        self.assertIn("'read_consistency': read_consistency", source)


if __name__ == '__main__':
    unittest.main()
