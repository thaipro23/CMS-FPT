from __future__ import annotations


REPLICA = 'replica'
PRIMARY_AFTER_ENROLLMENT = 'primary_after_enrollment'


def normalize_analytics_read_consistency(value) -> str:
    """Default every analytics request to replica unless it uses the exact flag."""

    return PRIMARY_AFTER_ENROLLMENT if value == PRIMARY_AFTER_ENROLLMENT else REPLICA
