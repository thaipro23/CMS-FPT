from io import BytesIO
from unittest.mock import Mock, patch

from django.core import signing
from django.test import RequestFactory, SimpleTestCase, override_settings

from openedx_fpt_report_proxy.storage import TOKEN_SALT
from openedx_fpt_report_proxy.views import download_report

COURSE_HASH = "0123456789abcdef0123456789abcdef01234567"
REPORT_KEY = f"{COURSE_HASH}/grades.csv"


@override_settings(
    FPT_REPORT_PROXY_TOKEN_TTL_SECONDS=300,
    GRADES_DOWNLOAD={"STORAGE_KWARGS": {"bucket_name": "openedxgrades", "location": "openedx/media/grades"}},
)
class DownloadReportViewTest(SimpleTestCase):
    def setUp(self):
        self.factory = RequestFactory()
        self.user = Mock(is_authenticated=True, is_staff=True, is_superuser=False)

    def _request(self, token):
        request = self.factory.get("/api/fpt-reports/v1/download", {"token": token})
        request.user = self.user
        return request

    def test_streams_private_report_for_authorized_user(self):
        token = signing.dumps({"key": REPORT_KEY}, salt=TOKEN_SALT, compress=True)
        storage = Mock()
        storage.exists.return_value = True
        storage.open.return_value = BytesIO(b"student,grade\nSV001,10\n")

        with patch("openedx_fpt_report_proxy.views.FPTReportProxyS3Storage", return_value=storage):
            response = download_report(self._request(token))

        self.assertEqual(response.status_code, 200)
        self.assertIn('attachment; filename="grades.csv"', response["Content-Disposition"])
        self.assertEqual(b"".join(response.streaming_content), b"student,grade\nSV001,10\n")

    def test_rejects_tampered_token(self):
        response = download_report(self._request("not-a-valid-token"))
        self.assertEqual(response.status_code, 400)

    def test_rejects_unauthenticated_request(self):
        token = signing.dumps({"key": REPORT_KEY}, salt=TOKEN_SALT, compress=True)
        request = self._request(token)
        request.user = Mock(is_authenticated=False, is_staff=False, is_superuser=False)
        response = download_report(request)
        self.assertEqual(response.status_code, 401)

    @patch("openedx_fpt_report_proxy.views.signing.loads", side_effect=signing.SignatureExpired("expired"))
    def test_expired_link_returns_410(self, _mock_loads):
        response = download_report(self._request("expired-token"))
        self.assertEqual(response.status_code, 410)
