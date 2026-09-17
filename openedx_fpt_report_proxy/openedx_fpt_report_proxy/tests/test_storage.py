from urllib.parse import parse_qs, urlsplit

from django.core import signing
from django.test import SimpleTestCase, override_settings

from openedx_fpt_report_proxy.storage import FPTReportProxyS3Storage, TOKEN_SALT


@override_settings(
    FPT_REPORT_PROXY_BASE_URL="https://cms.fpl.edu.vn",
    FPT_REPORT_PROXY_TOKEN_TTL_SECONDS=300,
)
class FPTReportProxyStorageTest(SimpleTestCase):
    def test_url_returns_lms_proxy_not_minio_endpoint(self):
        storage = FPTReportProxyS3Storage(
            bucket_name="openedxgrades",
            access_key="test-access",
            secret_key="test-secret",
            endpoint_url="https://10.205.194.48:443",
        )

        url = storage.url("0123456789abcdef0123456789abcdef01234567/report.csv")
        parsed = urlsplit(url)
        token = parse_qs(parsed.query)["token"][0]
        payload = signing.loads(token, salt=TOKEN_SALT, max_age=300)

        self.assertEqual(parsed.scheme, "https")
        self.assertEqual(parsed.netloc, "cms.fpl.edu.vn")
        self.assertEqual(parsed.path, "/api/fpt-reports/v1/download")
        self.assertNotIn("10.205.194.48", url)
        self.assertEqual(
            payload["key"],
            "0123456789abcdef0123456789abcdef01234567/report.csv",
        )
