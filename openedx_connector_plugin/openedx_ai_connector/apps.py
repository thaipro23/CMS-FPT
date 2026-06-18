from django.apps import AppConfig


class OpenEdxAIConnectorConfig(AppConfig):
    name = "openedx_ai_connector"
    verbose_name = "Open edX AI Connector"

    plugin_app = {
        "url_config": {
            "lms.djangoapp": {
                "namespace": "openedx_ai_connector",
                "app_name": "openedx_ai_connector",
                "regex": r"^api/(?:ai-connector/v1/|ai-student-insight/v1/)",
                "relative_path": "urls",
            },
            "cms.djangoapp": {
                "namespace": "openedx_ai_connector",
                "app_name": "openedx_ai_connector",
                "regex": r"^api/(?:ai-connector/v1/|ai-student-insight/v1/)",
                "relative_path": "urls",
            },
        }
    }
