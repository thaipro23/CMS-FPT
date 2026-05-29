"""
Common settings for openedx_unit_reset.

Open edX plugin loader requires this module to expose plugin_settings(settings).
Do not leave only module-level constants here, otherwise collectstatic/build can fail with:
AttributeError: module 'openedx_unit_reset.settings.common' has no attribute 'plugin_settings'
"""

UNIT_RESET_DEFAULT_COOLDOWN_SECONDS = 600
UNIT_RESET_REQUIRE_COOLDOWN = True

# Security defaults.
UNIT_RESET_ALLOW_STUDENT_SELF_RESET = True
UNIT_RESET_ALLOW_CLIENT_USER_ID = False
UNIT_RESET_REQUIRE_ENROLLMENT = True
UNIT_RESET_REQUIRE_UNIT_COURSE_MATCH = True
UNIT_RESET_AUDIT_LOG_ENABLED = True

# Optional anti-abuse guard. 0 means unlimited reset count.
UNIT_RESET_MAX_RESETS_PER_UNIT = 0

# When true, cooldown baseline is max(last reset time, latest StudentModule.modified in the Unit).
# This makes the first reset after a submit wait for cooldown even before the user has reset once.
UNIT_RESET_USE_LATEST_STUDENTMODULE_MODIFIED_AS_ATTEMPT_TIME = True


def plugin_settings(settings):
    """Register settings into LMS/CMS Django settings object."""

    settings.UNIT_RESET_DEFAULT_COOLDOWN_SECONDS = getattr(
        settings,
        "UNIT_RESET_DEFAULT_COOLDOWN_SECONDS",
        UNIT_RESET_DEFAULT_COOLDOWN_SECONDS,
    )
    settings.UNIT_RESET_REQUIRE_COOLDOWN = getattr(
        settings,
        "UNIT_RESET_REQUIRE_COOLDOWN",
        UNIT_RESET_REQUIRE_COOLDOWN,
    )
    settings.UNIT_RESET_ALLOW_STUDENT_SELF_RESET = getattr(
        settings,
        "UNIT_RESET_ALLOW_STUDENT_SELF_RESET",
        UNIT_RESET_ALLOW_STUDENT_SELF_RESET,
    )
    settings.UNIT_RESET_ALLOW_CLIENT_USER_ID = getattr(
        settings,
        "UNIT_RESET_ALLOW_CLIENT_USER_ID",
        UNIT_RESET_ALLOW_CLIENT_USER_ID,
    )
    settings.UNIT_RESET_REQUIRE_ENROLLMENT = getattr(
        settings,
        "UNIT_RESET_REQUIRE_ENROLLMENT",
        UNIT_RESET_REQUIRE_ENROLLMENT,
    )
    settings.UNIT_RESET_REQUIRE_UNIT_COURSE_MATCH = getattr(
        settings,
        "UNIT_RESET_REQUIRE_UNIT_COURSE_MATCH",
        UNIT_RESET_REQUIRE_UNIT_COURSE_MATCH,
    )
    settings.UNIT_RESET_AUDIT_LOG_ENABLED = getattr(
        settings,
        "UNIT_RESET_AUDIT_LOG_ENABLED",
        UNIT_RESET_AUDIT_LOG_ENABLED,
    )
    settings.UNIT_RESET_MAX_RESETS_PER_UNIT = getattr(
        settings,
        "UNIT_RESET_MAX_RESETS_PER_UNIT",
        UNIT_RESET_MAX_RESETS_PER_UNIT,
    )
    settings.UNIT_RESET_USE_LATEST_STUDENTMODULE_MODIFIED_AS_ATTEMPT_TIME = getattr(
        settings,
        "UNIT_RESET_USE_LATEST_STUDENTMODULE_MODIFIED_AS_ATTEMPT_TIME",
        UNIT_RESET_USE_LATEST_STUDENTMODULE_MODIFIED_AS_ATTEMPT_TIME,
    )
