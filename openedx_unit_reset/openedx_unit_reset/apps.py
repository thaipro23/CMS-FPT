from django.apps import AppConfig


class UnitResetConfig(AppConfig):
    """
    Open edX plugin app config.

    This keeps the string-based plugin_app format used by many Tutor/Open edX
    Ulmo installations. The important bit is that every settings module below
    exposes plugin_settings(settings).
    """

    name = "openedx_unit_reset"
    verbose_name = "Open edX Unit Reset"

    plugin_app = {
        "url_config": {
            "lms.djangoapp": {
                "namespace": "openedx_unit_reset",
                "regex": "^api/unit-reset/",
                "relative_path": "urls",
            },
        },
        "settings_config": {
            "lms.djangoapp": {
                "common": {"relative_path": "settings.common"},
                "production": {"relative_path": "settings.production"},
                "devstack": {"relative_path": "settings.devstack"},
            },
            "cms.djangoapp": {
                "common": {"relative_path": "settings.common"},
                "production": {"relative_path": "settings.production"},
                "devstack": {"relative_path": "settings.devstack"},
            },
        },
    }
