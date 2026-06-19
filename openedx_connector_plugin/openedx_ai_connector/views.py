"""Compatibility re-exports for Open edX connector URL wiring.

v25.9.16.4.5 moves implementation out of the historical 5k-line views.py.
Keep this file thin so Django URLs and external imports remain stable.
"""

from __future__ import annotations

from .studio import (
    health,
    course_content,
    studio_course_content,
    publish_problem,
    ensure_chapter_library,
    import_problem_to_library,
    publish_diagnostics,
    backfill_library_tags,
    library_tags_diagnostics,
    verify_library_problem,
    delete_library_problem,
    session_me,
    session_bridge,
    create_quiz_node,
    delete_quiz_node,
    insert_problem_banks,
)

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
