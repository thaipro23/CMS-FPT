"""Tutor integration for the Open edX <-> AI Server connector.

This plugin has two responsibilities:
1. Install the Django connector package into the Open edX image.
2. Render the connector security/runtime settings into both LMS and CMS.

Secrets are deliberately empty by default. Production must provide the same HMAC
secret used by AI Server (OPENEDX_CONNECTOR_HMAC_SECRET) via Tutor config.
"""

from tutor import hooks


hooks.Filters.CONFIG_DEFAULTS.add_items([
    ("AI_CONNECTOR_PUBLISH_USERNAME", ""),
    ("AI_CONNECTOR_ALLOW_ANONYMOUS_PUBLISH", "false"),
    ("AI_CONNECTOR_HMAC_SECRET", ""),
    ("AI_CONNECTOR_HMAC_SKEW_SECONDS", "300"),
    ("AI_CONNECTOR_ALLOWED_DOWNLOAD_HOSTS", "edx.cms.fpl.edu.vn,scms.fpl.edu.vn,app.cms.fpl.edu.vn"),
    ("AI_CONNECTOR_COMPONENT_PUBLISH_ENABLED", "false"),
    ("AI_CONNECTOR_TAGGING_ENABLED", "true"),
    ("AI_CONNECTOR_TAG_TAXONOMY_EXPORT_ID", "ai-learning-check"),
    ("AI_CONNECTOR_TAG_TAXONOMY_NAME", "AI Learning Check"),
    ("AI_CONNECTOR_TAG_TAXONOMY_DESCRIPTION", "Tags automatically assigned by AI Learning Check Generator."),
    ("AI_CONNECTOR_AUTO_CREATE_ORG", "false"),
    ("AI_CONNECTOR_POST_PUBLISH_EVENTS_ENABLED", "false"),
    ("AI_CONNECTOR_ADMIN_GROUPS", "AI_ADMIN,AI Admin"),
    ("AI_CONNECTOR_ALLOW_ADMIN_GROUP", "false"),
    ("AI_CONNECTOR_SESSION_BRIDGE_SECRET", ""),
    ("AI_CONNECTOR_SESSION_BRIDGE_ALLOWED_RETURN_HOSTS", "dash-cms.fpl.edu.vn"),
    ("AI_CONNECTOR_SESSION_BRIDGE_TTL_SECONDS", "60"),
    ("AI_CONNECTOR_SESSION_BRIDGE_ISSUER", "openedx-ai-connector"),
    ("AI_CONNECTOR_SESSION_BRIDGE_AUDIENCE", "ai-learning-server"),
    ("AI_CONNECTOR_ALLOW_LOCAL_BRIDGE_RETURN", "false"),
    ("AI_CONNECTOR_DEBUG_ERRORS", "false"),
    ("AI_QUIZ_RUNTIME_ALLOWED_ORIGINS", "https://app.cms.fpl.edu.vn,https://edx.cms.fpl.edu.vn,https://scms.fpl.edu.vn,https://dash-cms.fpl.edu.vn"),
])


# The connector app is installed in both LMS and CMS, so render the shared
# security contract into openedx-common-settings instead of CMS-only settings.
# _setting_or_env() in the connector still gives a real process environment
# variable precedence over these Django settings.
hooks.Filters.ENV_PATCHES.add_item((
    "openedx-common-settings",
    r'''
# FPT AI Server <-> Open edX connector settings
AI_CONNECTOR_PUBLISH_USERNAME = "{{ AI_CONNECTOR_PUBLISH_USERNAME }}"
AI_CONNECTOR_ALLOW_ANONYMOUS_PUBLISH = "{{ AI_CONNECTOR_ALLOW_ANONYMOUS_PUBLISH }}"
AI_CONNECTOR_HMAC_SECRET = "{{ AI_CONNECTOR_HMAC_SECRET }}"
AI_CONNECTOR_HMAC_SKEW_SECONDS = "{{ AI_CONNECTOR_HMAC_SKEW_SECONDS }}"
AI_CONNECTOR_ALLOWED_DOWNLOAD_HOSTS = "{{ AI_CONNECTOR_ALLOWED_DOWNLOAD_HOSTS }}"
AI_CONNECTOR_COMPONENT_PUBLISH_ENABLED = "{{ AI_CONNECTOR_COMPONENT_PUBLISH_ENABLED }}"
AI_CONNECTOR_TAGGING_ENABLED = "{{ AI_CONNECTOR_TAGGING_ENABLED }}"
AI_CONNECTOR_TAG_TAXONOMY_EXPORT_ID = "{{ AI_CONNECTOR_TAG_TAXONOMY_EXPORT_ID }}"
AI_CONNECTOR_TAG_TAXONOMY_NAME = "{{ AI_CONNECTOR_TAG_TAXONOMY_NAME }}"
AI_CONNECTOR_TAG_TAXONOMY_DESCRIPTION = "{{ AI_CONNECTOR_TAG_TAXONOMY_DESCRIPTION }}"
AI_CONNECTOR_AUTO_CREATE_ORG = "{{ AI_CONNECTOR_AUTO_CREATE_ORG }}"
AI_CONNECTOR_POST_PUBLISH_EVENTS_ENABLED = "{{ AI_CONNECTOR_POST_PUBLISH_EVENTS_ENABLED }}"
AI_CONNECTOR_ADMIN_GROUPS = "{{ AI_CONNECTOR_ADMIN_GROUPS }}"
AI_CONNECTOR_ALLOW_ADMIN_GROUP = "{{ AI_CONNECTOR_ALLOW_ADMIN_GROUP }}"
AI_CONNECTOR_SESSION_BRIDGE_SECRET = "{{ AI_CONNECTOR_SESSION_BRIDGE_SECRET }}"
AI_CONNECTOR_SESSION_BRIDGE_ALLOWED_RETURN_HOSTS = "{{ AI_CONNECTOR_SESSION_BRIDGE_ALLOWED_RETURN_HOSTS }}"
AI_CONNECTOR_SESSION_BRIDGE_TTL_SECONDS = "{{ AI_CONNECTOR_SESSION_BRIDGE_TTL_SECONDS }}"
AI_CONNECTOR_SESSION_BRIDGE_ISSUER = "{{ AI_CONNECTOR_SESSION_BRIDGE_ISSUER }}"
AI_CONNECTOR_SESSION_BRIDGE_AUDIENCE = "{{ AI_CONNECTOR_SESSION_BRIDGE_AUDIENCE }}"
AI_CONNECTOR_ALLOW_LOCAL_BRIDGE_RETURN = "{{ AI_CONNECTOR_ALLOW_LOCAL_BRIDGE_RETURN }}"
AI_CONNECTOR_DEBUG_ERRORS = "{{ AI_CONNECTOR_DEBUG_ERRORS }}"
AI_QUIZ_RUNTIME_ALLOWED_ORIGINS = "{{ AI_QUIZ_RUNTIME_ALLOWED_ORIGINS }}"
''',
))


hooks.Filters.ENV_PATCHES.add_item((
    "openedx-dockerfile-pre-assets",
    """
# Install Open edX Connector Plugin during openedx image build
RUN $PIP_COMMAND install -e /openedx/edx-platform/openedx_connector_plugin
""",
))
