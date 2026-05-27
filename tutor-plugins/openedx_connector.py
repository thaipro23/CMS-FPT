from tutor import hooks


hooks.Filters.ENV_PATCHES.add_item((
    "openedx-dockerfile-pre-assets",
    """
# Install Open edX Connector Plugin during openedx image build
RUN $PIP_COMMAND install -e /openedx/edx-platform/openedx_connector_plugin
"""
))