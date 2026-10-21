"""Audit/backfill persisted course grade cutoffs, safely read-only by default."""

import json
import os
from types import SimpleNamespace

from django.conf import settings
from django.core.management.base import BaseCommand, CommandError
from xmodule.modulestore import ModuleStoreEnum
from xmodule.modulestore.django import modulestore

from cms.djangoapps.models.settings.course_grading import CourseGradingModel


class Command(BaseCommand):
    help = 'Audit and optionally backfill course PASS grade cutoff to 100%.'

    def add_arguments(self, parser):
        parser.add_argument('--apply', action='store_true', help='Persist data; default is dry-run.')
        parser.add_argument('--backup-file', help='New backup JSONL file on persistent storage; required for apply.')
        parser.add_argument('--expected-count', type=int, help='Must match the selected course count for apply.')
        parser.add_argument('--limit', type=int, default=0, help='Limit changes per batch; 0 means all.')

    def handle(self, *args, **options):
        if options['limit'] < 0:
            raise CommandError('--limit must be nonnegative')
        if options['apply'] and (not options['backup_file'] or options['expected_count'] is None):
            raise CommandError('--apply requires --backup-file and --expected-count')
        if options['apply'] and not getattr(settings, 'FPT_ENFORCE_PERFECT_PASS', False):
            raise CommandError('Enable FPT_ENFORCE_PERFECT_PASS before applying persisted policies')
        store = modulestore()
        keys = sorted((summary.id for summary in store.get_course_summaries()), key=str)
        pending = []
        for key in keys:
            course = store.get_course(key, depth=1)
            if course is None:
                raise CommandError(f'Missing course data for: {key}')
            # An enabled runtime override masks the persisted grade_cutoffs property.
            cutoffs = (course.grading_policy or {}).get('GRADE_CUTOFFS', {})
            if cutoffs != {'Pass': 1.0}:
                pending.append((key, cutoffs))
        selected = pending[:options['limit']] if options['limit'] else pending
        self.stdout.write(f'TOTAL={len(keys)} PENDING={len(pending)} SELECTED={len(selected)}')
        for key, cutoffs in selected[:20]:
            self.stdout.write(f'PENDING {key}: {cutoffs!r}')
        if not options['apply']:
            self.stdout.write('DRY-RUN: no changes')
            return
        if len(selected) != options['expected_count']:
            raise CommandError('Expected-count mismatch. Audit and retry with updated count.')
        if not selected:
            self.stdout.write('Nothing to update')
            return
        try:
            # No overwrite, 0600 permissions even if the container umask is permissive.
            fd = os.open(options['backup_file'], os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
            backup = os.fdopen(fd, 'w', encoding='utf-8')
        except OSError as exc:
            raise CommandError(f'Could not exclusively create backup: {exc}') from exc
        updated = 0
        with backup:
            for key, old_cutoffs in selected:
                current = store.get_course(key, depth=1)
                if current is None or (current.grading_policy or {}).get('GRADE_CUTOFFS', {}) != old_cutoffs:
                    raise CommandError(f'Concurrent grading-policy update detected for {key}')
                # Persist rollback metadata first, including fsync, before each write.
                backup.write(json.dumps({'course_id': str(key), 'grade_cutoffs_before': old_cutoffs},
                                        ensure_ascii=False) + '\n')
                backup.flush()
                os.fsync(backup.fileno())
                CourseGradingModel.update_cutoffs_from_json(
                    key, {'Pass': 1.0}, SimpleNamespace(id=ModuleStoreEnum.UserID.mgmt_command),
                )
                updated += 1
                self.stdout.write(f'UPDATED: {key}')
        self.stdout.write(f'UPDATED_COUNT={updated}, BACKUP={options["backup_file"]}')
