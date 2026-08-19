"""Tutor build helper for the Open edX FPT Auth plugin."""

from tutor import hooks

hooks.Filters.ENV_PATCHES.add_item(
    (
        "openedx-dockerfile-pre-assets",
        r"""
# Install map-only FEID/Google authentication before collectstatic.
RUN if [ -n "$PIP_COMMAND" ]; then \
        $PIP_COMMAND install -e /openedx/edx-platform/openedx_fpt_auth; \
    else \
        pip install -e /openedx/edx-platform/openedx_fpt_auth; \
    fi
""",
    )
)
