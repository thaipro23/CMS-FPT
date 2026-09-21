"""Pure helpers for validating and authorizing FPT report proxy object keys."""

from __future__ import annotations

import hashlib
import re

_SHA1_HEX = re.compile(r"^[0-9a-f]{40}$")


def normalize_report_key(value: object) -> str:
    """Return a safe relative POSIX key or raise ``ValueError``."""
    raw = str(value or "")
    if not raw or raw.startswith(("/", "\\")):
        raise ValueError("report key must be relative")

    key = raw.replace("\\", "/")
    parts = key.split("/")
    if any(part in ("", ".", "..") for part in parts):
        raise ValueError("invalid report key")

    return "/".join(parts)


def normalize_user_task_artifact_key(value: object) -> str:
    """Return a safe key contained by the user-task artifact prefix."""
    key = normalize_report_key(value)
    if not key.startswith("user_tasks/"):
        raise ValueError("artifact key must be inside user_tasks/")
    return key


def course_hash(course_id: object) -> str:
    """Mirror ``ReportStore.path_to`` SHA1 course-directory naming."""
    return hashlib.sha1(str(course_id).encode("utf-8")).hexdigest()


def report_course_hash(key: object) -> str:
    """Extract and validate the hashed course directory from a report key."""
    normalized = normalize_report_key(key)
    prefix = normalized.split("/", 1)[0]
    if not _SHA1_HEX.fullmatch(prefix):
        raise ValueError("report key does not start with a course hash")
    return prefix
