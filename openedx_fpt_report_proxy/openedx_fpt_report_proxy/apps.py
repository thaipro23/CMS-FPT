"""Open edX plugin registration for private report downloads."""

from pathlib import Path
from typing import ClassVar

from django.apps import AppConfig


class FPTReportProxyConfig(AppConfig):
    """Expose the private grade-report download endpoint in the LMS."""

    name = "openedx_fpt_report_proxy"
    path = str(Path(__file__).resolve().parent)
    verbose_name = "Open edX FPT Report Proxy"

    plugin_app: ClassVar[dict[str, object]] = {
        "url_config": {
            "lms.djangoapp": {
                "namespace": "fpt_report_proxy",
                "app_name": "openedx_fpt_report_proxy",
                "regex": r"^api/fpt-reports/v1/",
                "relative_path": "urls",
            },
        },
    }
