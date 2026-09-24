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
        outer_index = {'course-v1:outer': {'sentinel': True}}
        xblock_token = si._XBLOCK_REQUEST_CACHE.set(outer_cache)
        index_token = si._COURSE_LEARNING_INDEX_REQUEST_CACHE.set(outer_index)
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
            self.assertIs(si._COURSE_LEARNING_INDEX_REQUEST_CACHE.get(), outer_index)
        finally:
            si._COURSE_LEARNING_INDEX_REQUEST_CACHE.reset(index_token)
            si._XBLOCK_REQUEST_CACHE.reset(xblock_token)


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

    def test_build_course_learning_index_uses_modulestore_bulk_operations(self):
        sequential_key = 'block-v1:FPL+DOM1021+FA26+type@sequential+block@quiz1'
        problem_key = 'block-v1:FPL+DOM1021+FA26+type@problem+block@p1'
        course = self.FakeBlock('course', 'DOM1021', [sequential_key])
        blocks = {
            sequential_key: self.FakeBlock('sequential', 'Quiz 1', [problem_key]),
            problem_key: self.FakeBlock('problem', 'Question 1', []),
        }

        class BulkStore(self.FakeStore):
            def __init__(self, course, blocks):
                super().__init__(course, blocks)
                self.bulk_enter_count = 0
                self.bulk_exit_count = 0
                self.bulk_active = False

            def bulk_operations(self, _course_key):
                store = self

                class BulkContext:
                    def __enter__(self):
                        store.bulk_enter_count += 1
                        store.bulk_active = True
                        return store

                    def __exit__(self, exc_type, exc, tb):
                        store.bulk_active = False
                        store.bulk_exit_count += 1
                        return False

                return BulkContext()

        store = BulkStore(course, blocks)
        baseline = si._materialize_course_learning_index(
            'course-v1:FPL+DOM1021+FA26',
            store=self.FakeStore(course, blocks),
        )

        def get_item(_store, key):
            self.assertTrue(store.bulk_active)
            return blocks[str(key)]

        with patch.object(si, '_load_openedx_modules', return_value=(object(), lambda: store)), \
             patch.object(si, '_get_item_best_effort', side_effect=get_item) as lookup:
            index = si._build_course_learning_index('course-v1:FPL+DOM1021+FA26')

        self.assertEqual(store.bulk_enter_count, 1)
        self.assertEqual(store.bulk_exit_count, 1)
        self.assertFalse(store.bulk_active)
        self.assertEqual(lookup.call_count, 2)
        self.assertEqual(index['completion_denominator']['subsection_total'], 1)
        self.assertEqual(index['completion_denominator']['problem_total'], 1)
        self.assertEqual(index, baseline)

    def test_build_course_learning_index_falls_back_when_bulk_context_fails(self):
        sequential_key = 'block-v1:FPL+DOM1021+FA26+type@sequential+block@quiz1'
        problem_key = 'block-v1:FPL+DOM1021+FA26+type@problem+block@p1'
        course = self.FakeBlock('course', 'DOM1021', [sequential_key])
        blocks = {
            sequential_key: self.FakeBlock('sequential', 'Quiz 1', [problem_key]),
            problem_key: self.FakeBlock('problem', 'Question 1', []),
        }

        class BrokenBulkStore(self.FakeStore):
            def bulk_operations(self, _course_key):
                class BrokenContext:
                    def __enter__(self):
                        raise RuntimeError('bulk unavailable')

                    def __exit__(self, exc_type, exc, tb):
                        return False

                return BrokenContext()

        store = BrokenBulkStore(course, blocks)
        baseline = si._materialize_course_learning_index(
            'course-v1:FPL+DOM1021+FA26',
            store=self.FakeStore(course, blocks),
        )

        with patch.object(si, '_load_openedx_modules', return_value=(object(), lambda: store)):
            index = si._build_course_learning_index('course-v1:FPL+DOM1021+FA26')

        self.assertEqual(index, baseline)
