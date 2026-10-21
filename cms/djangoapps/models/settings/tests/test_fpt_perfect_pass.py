"""The server must enforce 100%, regardless of client grading cutoff requests."""

from types import SimpleNamespace
from unittest import TestCase
from unittest.mock import Mock, patch

from django.test import override_settings

from cms.djangoapps.models.settings.course_grading import CourseGradingModel


class PerfectPassCmsTest(TestCase):
    @override_settings(FPT_ENFORCE_PERFECT_PASS=True)
    @patch('cms.djangoapps.models.settings.course_grading._grading_event_and_signal')
    @patch('cms.djangoapps.models.settings.course_grading.modulestore')
    def test_cutoff_update_cannot_downgrade_pass(self, store_provider, signal):
        course = SimpleNamespace(grade_cutoffs={'Pass': 0.5})
        store = Mock()
        store.get_course.return_value = course
        store_provider.return_value = store
        result = CourseGradingModel.update_cutoffs_from_json('course', {'Pass': 0.5}, SimpleNamespace(id=11))
        self.assertEqual(result, {'Pass': 1.0})
        self.assertEqual(course.grade_cutoffs, {'Pass': 1.0})
        store.update_item.assert_called_once_with(course, 11)
        signal.assert_called_once()

    @override_settings(FPT_ENFORCE_PERFECT_PASS=True)
    @patch('cms.djangoapps.models.settings.course_grading.material_recompute_only')
    def test_persisted_cutoff_change_requires_invalidation(self, unused_toggle):
        course = SimpleNamespace(
            grading_policy={'GRADE_CUTOFFS': {'Pass': 0.5}},
            grade_cutoffs={'Pass': 1.0},  # runtime overlay
            raw_grader=[],
        )
        self.assertTrue(CourseGradingModel.must_fire_grading_event_and_signal(
            'course', [], course, {'grade_cutoffs': {'Pass': 1.0}},
        ))
