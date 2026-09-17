"""S3 storage that returns LMS proxy URLs instead of private MinIO URLs."""

from __future__ import annotations

from urllib.parse import urlencode

from django.conf import settings
from django.core import signing
from storages.backends.s3boto3 import S3Boto3Storage

from .security import normalize_report_key

TOKEN_SALT = "openedx_fpt_report_proxy.download.v1"
DEFAULT_DOWNLOAD_PATH = "/api/fpt-reports/v1/download"


class FPTReportProxyS3Storage(S3Boto3Storage):
    """Keep S3 I/O internal while exposing only a short-lived LMS URL."""

    def url(self, name, parameters=None, expire=None, http_method=None):  # pylint: disable=unused-argument
        key = normalize_report_key(name)
        token = signing.dumps({"key": key}, salt=TOKEN_SALT, compress=True)
        base_url = str(getattr(settings, "FPT_REPORT_PROXY_BASE_URL", "")).rstrip("/")
        download_path = str(
            getattr(settings, "FPT_REPORT_PROXY_DOWNLOAD_PATH", DEFAULT_DOWNLOAD_PATH)
        )
        if not download_path.startswith("/"):
            download_path = f"/{download_path}"
        return f"{base_url}{download_path}?{urlencode({'token': token})}"
