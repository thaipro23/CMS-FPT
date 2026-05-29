from tutor import hooks


hooks.Filters.ENV_PATCHES.add_item((
    "openedx-dockerfile-pre-assets",
    r"""
# Install Unit Reset plugin during openedx image build, before collectstatic.
RUN if [ -n "$PIP_COMMAND" ]; then \
        $PIP_COMMAND install -e /openedx/edx-platform/openedx_unit_reset; \
    else \
        pip install -e /openedx/edx-platform/openedx_unit_reset; \
    fi
"""
))
