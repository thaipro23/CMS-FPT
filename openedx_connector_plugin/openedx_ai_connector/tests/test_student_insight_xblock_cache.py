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
