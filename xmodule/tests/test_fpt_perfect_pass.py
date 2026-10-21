"""Pure math regression tests for the FPT passing threshold."""

from unittest import TestCase

from xmodule.fpt_perfect_pass import has_perfect_grade


class TestHasPerfectGrade(TestCase):
    def test_only_perfect_grade_passes(self):
        for score in (0, 0.5, 0.99, 0.995, 0.9999, 2, None, True, 'bad', float('nan'), float('inf')):
            with self.subTest(score=score):
                self.assertFalse(has_perfect_grade(score))
        self.assertTrue(has_perfect_grade(1.0))
        self.assertTrue(has_perfect_grade(1.0 - 1e-14))
        self.assertFalse(has_perfect_grade(1.0 - 1e-9))
