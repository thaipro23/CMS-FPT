"""Compatibility re-exports for Open edX connector URL wiring.

v25.9.16.4.5 moves implementation out of the historical 5k-line views.py.
Keep this file thin so Django URLs and external imports remain stable.
"""

from __future__ import annotations

from . import studio as _studio
from .itembank_naming import problem_bank_slot_display_name
from .media_publish import import_problem_to_library


def _runtime_problem_bank_slot_display_name(slot):
    """Resolve the primary Studio display name from the synced slot metadata."""
    return _studio._normalize_xblock_title(
        problem_bank_slot_display_name(slot),
        'Problem Bank',
        max_len=120,
    )


# Runtime wiring: urls.py imports endpoint callables from this module, while those
# callables execute with studio.py globals. Replace only the naming resolver so
# the real insert endpoint uses slot_m0/slot_no without replacing studio.py.
_studio._problem_bank_slot_display_name = _runtime_problem_bank_slot_display_name

health = _studio.health
course_content = _studio.course_content
studio_course_content = _studio.studio_course_content
publish_problem = _studio.publish_problem
ensure_chapter_library = _studio.ensure_chapter_library
publish_diagnostics = _studio.publish_diagnostics
backfill_library_tags = _studio.backfill_library_tags
library_tags_diagnostics = _studio.library_tags_diagnostics
verify_library_problem = _studio.verify_library_problem
delete_library_problem = _studio.delete_library_problem
session_me = _studio.session_me
session_bridge = _studio.session_bridge
create_quiz_node = _studio.create_quiz_node
delete_quiz_node = _studio.delete_quiz_node
insert_problem_banks = _studio.insert_problem_banks

__all__ = [
    'health',
    'course_content',
    'studio_course_content',
    'publish_problem',
    'ensure_chapter_library',
    'import_problem_to_library',
    'publish_diagnostics',
    'backfill_library_tags',
    'library_tags_diagnostics',
    'verify_library_problem',
    'delete_library_problem',
    'session_me',
    'session_bridge',
    'create_quiz_node',
    'delete_quiz_node',
    'insert_problem_banks',
]
