"""Integration-boundary regression tests for mandatory 100% PASS."""

from types import SimpleNamespace
from unittest import TestCase
from unittest.mock import Mock

from django.test import override_settings

from lms.djangoapps.grades.course_grade import CourseGrade
from xmodule.course_block import CourseBlock


class PerfectPassPolicyTest(TestCase):
    @staticmethod
    def _grade(raw_percent, cutoff):
        grade = object.__new__(CourseGrade)
        grade.course_data = SimpleNamespace(course=SimpleNamespace(grade_cutoffs={'Pass': cutoff}))
        grade.grader_result = Mock(return_value={'percent': raw_percent})
        grade.update()
        return grade

    @override_settings(FPT_ENFORCE_PERFECT_PASS=True)
    def test_rounded_100_does_not_pass(self):
        for raw_percent in (0.99, 0.995, 0.9999, 1.0):
            with self.subTest(raw_percent=raw_percent):
                grade = self._grade(raw_percent, 1.0)
                self.assertEqual(grade.passed, raw_percent == 1.0)
                self.assertEqual(grade.letter_grade, 'Pass' if raw_percent == 1.0 else None)
                if raw_percent == 0.995:
                    self.assertEqual(grade.percent, 1.0)  # rounded for display only

    @override_settings(FPT_ENFORCE_PERFECT_PASS=False)
    def test_disabled_flag_preserves_old_grading(self):
        grade = self._grade(0.5, 0.5)
        self.assertTrue(grade.passed)

    @override_settings(FPT_ENFORCE_PERFECT_PASS=True)
    def test_existing_course_policy_overridden_without_mutating_store(self):
        fake_course = SimpleNamespace(_grading_policy={'GRADE_CUTOFFS': {'Pass': 0.5}})
        self.assertEqual(CourseBlock.grade_cutoffs.fget(fake_course), {'Pass': 1.0})
        self.assertEqual(fake_course._grading_policy['GRADE_CUTOFFS'], {'Pass': 0.5})

    @override_settings(FPT_ENFORCE_PERFECT_PASS=False)
    def test_disabled_flag_uses_stored_course_policy(self):
        fake_course = SimpleNamespace(_grading_policy={'GRADE_CUTOFFS': {'Pass': 0.5}})
        self.assertEqual(CourseBlock.grade_cutoffs.fget(fake_course), {'Pass': 0.5})
