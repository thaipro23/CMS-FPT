"""Open edX runtime import helpers kept separate from HTTP views."""

from __future__ import annotations


def _load_openedx_modules():
    from opaque_keys.edx.keys import CourseKey  # type: ignore
    from xmodule.modulestore.django import modulestore  # type: ignore
    return CourseKey, modulestore
