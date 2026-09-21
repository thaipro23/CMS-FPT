from __future__ import annotations

from unittest import TestCase
from unittest.mock import patch

from openedx_ai_connector import student_insight as si


class XBlockRequestCacheTests(TestCase):
    def test_request_cache_deduplicates_successful_lookup(self):
        store = object()
        block = object()

        with patch.object(si, '_studio_get_item_best_effort', return_value=block) as lookup:
            token = si._XBLOCK_REQUEST_CACHE.set({})
            try:
                first = si._get_item_best_effort(store, 'block-v1:test')
                second = si._get_item_best_effort(store, 'block-v1:test')
            finally:
                si._XBLOCK_REQUEST_CACHE.reset(token)

        self.assertIs(first, block)
        self.assertIs(second, block)
        self.assertEqual(lookup.call_count, 1)

    def test_request_cache_also_deduplicates_missing_block(self):
        store = object()

        with patch.object(si, '_studio_get_item_best_effort', return_value=None) as lookup:
            token = si._XBLOCK_REQUEST_CACHE.set({})
            try:
                self.assertIsNone(si._get_item_best_effort(store, 'block-v1:missing'))
                self.assertIsNone(si._get_item_best_effort(store, 'block-v1:missing'))
            finally:
                si._XBLOCK_REQUEST_CACHE.reset(token)

        self.assertEqual(lookup.call_count, 1)

    def test_lookup_is_not_cached_outside_class_analytics_scope(self):
        store = object()
        block = object()

        with patch.object(si, '_studio_get_item_best_effort', return_value=block) as lookup:
            si._get_item_best_effort(store, 'block-v1:test')
            si._get_item_best_effort(store, 'block-v1:test')

        self.assertEqual(lookup.call_count, 2)

    def test_selected_database_wrapper_restores_outer_cache_on_failure(self):
        outer_cache = {'existing': object()}
        token = si._XBLOCK_REQUEST_CACHE.set(outer_cache)
        try:
            with patch.object(
                si,
                '_student_learning_results_on_selected_database_uncached',
                side_effect=RuntimeError('boom'),
            ):
                with self.assertRaisesRegex(RuntimeError, 'boom'):
                    si._student_learning_results_on_selected_database(
                        'course-v1:FPS+COM1091+FA26',
                        [],
                        compact=True,
                        skip_course_home_progress=True,
                    )

            self.assertIs(si._XBLOCK_REQUEST_CACHE.get(), outer_cache)
        finally:
            si._XBLOCK_REQUEST_CACHE.reset(token)


class CourseLearningIndexTests(TestCase):
    class FakeBlock:
        def __init__(self, category, display_name, children=None):
            self.category = category
            self.display_name = display_name
            self.children = list(children or [])

    class FakeStore:
        def __init__(self, course, blocks):
            self.course = course
            self.blocks = blocks

        def get_course(self, _course_key):
            return self.course

    def test_three_analytics_views_share_one_course_tree_build(self):
        sequential_key = 'block-v1:FPS+COM1091+FA26+type@sequential+block@quiz1'
        problem_key = 'block-v1:FPS+COM1091+FA26+type@problem+block@p1'
        course = self.FakeBlock('course', 'COM1091', [sequential_key])
        blocks = {
            sequential_key: self.FakeBlock('sequential', 'Quiz 1', [problem_key]),
            problem_key: self.FakeBlock('problem', 'Question 1', []),
        }
        store = self.FakeStore(course, blocks)

        def get_item(_store, key):
            return blocks[str(key)]

        index_token = si._COURSE_LEARNING_INDEX_REQUEST_CACHE.set({})
        try:
            with patch.object(si, '_load_openedx_modules', return_value=(object(), lambda: store)), \
                 patch.object(si, '_get_item_best_effort', side_effect=get_item) as lookup:
                planned = si._course_outline_quiz_components('course-v1:FPS+COM1091+FA26')
                problem_index = si._subsection_problem_index('course-v1:FPS+COM1091+FA26')
                denominator = si._completion_denominator_block_snapshot('course-v1:FPS+COM1091+FA26')
        finally:
            si._COURSE_LEARNING_INDEX_REQUEST_CACHE.reset(index_token)

        self.assertEqual(lookup.call_count, 2)
        self.assertEqual(len(planned), 1)
        self.assertEqual(planned[0]['name'], 'Quiz 1')
        self.assertEqual(problem_index[problem_key]['key'], sequential_key)
        self.assertEqual(denominator['subsection_total'], 1)
        self.assertEqual(denominator['eligible_total'], 1)
        self.assertEqual(
            denominator['component_to_completion_unit'][problem_key],
            sequential_key,
        )

    def test_course_learning_index_cache_is_request_local(self):
        with patch.object(
            si,
            '_build_course_learning_index',
            return_value={
                'planned_components': [],
                'problem_to_subsection': {},
                'display_names': {},
                'completion_denominator': {},
                'error': None,
            },
        ) as build:
            token = si._COURSE_LEARNING_INDEX_REQUEST_CACHE.set({})
            try:
                si._course_learning_index('course-v1:FPS+COM1091+FA26')
                si._course_learning_index('course-v1:FPS+COM1091+FA26')
            finally:
                si._COURSE_LEARNING_INDEX_REQUEST_CACHE.reset(token)

            si._course_learning_index('course-v1:FPS+COM1091+FA26')

        self.assertEqual(build.call_count, 2)
