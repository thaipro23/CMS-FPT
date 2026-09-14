"""Open edX plugin registration for FPT presence."""

from pathlib import Path
from typing import ClassVar

from django.apps import AppConfig


class FPTPresenceConfig(AppConfig):
    """Register aggregate presence APIs and runtime settings in LMS/Studio."""

    name = "openedx_fpt_presence"
    path = str(Path(__file__).resolve().parent)
    verbose_name = "Open edX FPT Presence"

    plugin_app: ClassVar[dict[str, object]] = {
        "settings_config": {
            "lms.djangoapp": {
                "common": {"relative_path": "settings.common"},
            },
            "cms.djangoapp": {
                "common": {"relative_path": "settings.common"},
            },
        },
        "url_config": {
            "lms.djangoapp": {
                "namespace": "fpt_presence",
                "app_name": "openedx_fpt_presence",
                "regex": r"^api/fpt-presence/v1/",
                "relative_path": "urls",
            },
            "cms.djangoapp": {
                "namespace": "fpt_presence",
                "app_name": "openedx_fpt_presence",
                "regex": r"^api/fpt-presence/v1/",
                "relative_path": "urls",
            },
        },
    }
