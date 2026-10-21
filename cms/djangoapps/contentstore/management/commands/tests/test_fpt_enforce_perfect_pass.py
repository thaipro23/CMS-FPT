"""Tests for the dry-run-first perfect-pass migration command."""

import io
import json
import os
import tempfile
from types import SimpleNamespace
from unittest import TestCase
from unittest.mock import Mock, patch

from django.core.management.base import CommandError
from django.test import override_settings

from cms.djangoapps.contentstore.management.commands.fpt_enforce_perfect_pass import Command


class PerfectPassBackfillTest(TestCase):
    @staticmethod
    def _store(store_provider):
        store = Mock()
        store.get_course_summaries.return_value = [SimpleNamespace(id='test-course')]
        store.get_course.return_value = SimpleNamespace(grading_policy={'GRADE_CUTOFFS': {'Pass': 0.5}})
        store_provider.return_value = store
        return store

    @override_settings(FPT_ENFORCE_PERFECT_PASS=True)
    @patch('cms.djangoapps.contentstore.management.commands.fpt_enforce_perfect_pass.CourseGradingModel.update_cutoffs_from_json')
    @patch('cms.djangoapps.contentstore.management.commands.fpt_enforce_perfect_pass.modulestore')
    def test_dry_run_does_not_write(self, store_provider, update):
        store = self._store(store_provider)
        out = io.StringIO()
        Command(stdout=out).handle(apply=False, limit=0, backup_file=None, expected_count=None)
        self.assertIn('PENDING=1 SELECTED=1', out.getvalue())
        self.assertIn('DRY-RUN', out.getvalue())
        store.update_item.assert_not_called()
        update.assert_not_called()

    @override_settings(FPT_ENFORCE_PERFECT_PASS=True)
    @patch('cms.djangoapps.contentstore.management.commands.fpt_enforce_perfect_pass.CourseGradingModel.update_cutoffs_from_json')
    @patch('cms.djangoapps.contentstore.management.commands.fpt_enforce_perfect_pass.modulestore')
    def test_count_mismatch_blocks_mutation(self, store_provider, update):
        self._store(store_provider)
        with tempfile.TemporaryDirectory() as directory:
            target = os.path.join(directory, 'policy.jsonl')
            with self.assertRaises(CommandError):
                Command(stdout=io.StringIO()).handle(apply=True, limit=0, backup_file=target, expected_count=2)
            self.assertFalse(os.path.exists(target))
        update.assert_not_called()

    @override_settings(FPT_ENFORCE_PERFECT_PASS=True)
    @patch('cms.djangoapps.contentstore.management.commands.fpt_enforce_perfect_pass.CourseGradingModel.update_cutoffs_from_json')
    @patch('cms.djangoapps.contentstore.management.commands.fpt_enforce_perfect_pass.modulestore')
    def test_backup_is_written_before_update(self, store_provider, update):
        self._store(store_provider)
        with tempfile.TemporaryDirectory() as directory:
            target = os.path.join(directory, 'policy.jsonl')
            Command(stdout=io.StringIO()).handle(apply=True, limit=1, backup_file=target, expected_count=1)
            with open(target, encoding='utf-8') as file:
                saved = [json.loads(line) for line in file]
            self.assertEqual(saved, [{'course_id': 'test-course', 'grade_cutoffs_before': {'Pass': 0.5}}])
            self.assertEqual(os.stat(target).st_mode & 0o077, 0)
            update.assert_called_once()
            with self.assertRaises(CommandError):
                Command(stdout=io.StringIO()).handle(apply=True, limit=1, backup_file=target, expected_count=1)

    @override_settings(FPT_ENFORCE_PERFECT_PASS=True)
    @patch('cms.djangoapps.contentstore.management.commands.fpt_enforce_perfect_pass.CourseGradingModel.update_cutoffs_from_json')
    @patch('cms.djangoapps.contentstore.management.commands.fpt_enforce_perfect_pass.modulestore')
    def test_concurrent_policy_change_blocks_update(self, store_provider, update):
        store = self._store(store_provider)
        store.get_course.side_effect = [
            SimpleNamespace(grading_policy={'GRADE_CUTOFFS': {'Pass': 0.5}}),
            SimpleNamespace(grading_policy={'GRADE_CUTOFFS': {'Pass': 0.7}}),
        ]
        with tempfile.TemporaryDirectory() as directory:
            with self.assertRaises(CommandError):
                Command(stdout=io.StringIO()).handle(
                    apply=True, limit=1, backup_file=os.path.join(directory, 'policy.jsonl'), expected_count=1,
                )
        update.assert_not_called()

    @override_settings(FPT_ENFORCE_PERFECT_PASS=False)
    @patch('cms.djangoapps.contentstore.management.commands.fpt_enforce_perfect_pass.modulestore')
    def test_apply_requires_enabled_feature_flag(self, store_provider):
        with self.assertRaises(CommandError):
            Command(stdout=io.StringIO()).handle(apply=True, limit=0, backup_file='backup.jsonl', expected_count=1)
        store_provider.assert_not_called()
