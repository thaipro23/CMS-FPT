from urllib.parse import parse_qs, urlsplit

from django.core import signing
from django.test import SimpleTestCase, override_settings

import openedx_fpt_report_proxy.storage as storage_module


@override_settings(
    FPT_ARTIFACT_PROXY_BASE_URL="https://scms.fpl.edu.vn",
    FPT_ARTIFACT_PROXY_TOKEN_TTL_SECONDS=300,
)
class FPTUserTaskArtifactProxyStorageTest(SimpleTestCase):
    def _storage(self):
        storage_class = getattr(
            storage_module,
            "FPTUserTaskArtifactProxyS3Storage",
            None,
        )
        self.assertIsNotNone(storage_class, "artifact proxy storage is not implemented")
        return storage_class(
            bucket_name="openedx",
            access_key="test-access",
            secret_key="test-secret",
            endpoint_url="http://10.205.194.48:443",
        )

    def test_url_returns_cms_proxy_for_user_task_artifact(self):
        storage = self._storage()

        url = storage.url("user_tasks/2026/09/21/course.test.tar.gz")
        parsed = urlsplit(url)
        token = parse_qs(parsed.query)["token"][0]
        payload = signing.loads(
            token,
            salt=storage_module.ARTIFACT_TOKEN_SALT,
            max_age=300,
        )

        self.assertEqual(parsed.scheme, "https")
        self.assertEqual(parsed.netloc, "scms.fpl.edu.vn")
        self.assertEqual(parsed.path, "/api/fpt-artifacts/v1/download")
        self.assertNotIn("10.205.194.48", url)
        self.assertEqual(
            payload,
            {"key": "user_tasks/2026/09/21/course.test.tar.gz"},
        )

    def test_url_rejects_keys_outside_user_tasks_prefix(self):
        storage = self._storage()

        with self.assertRaises(ValueError):
            storage.url("openedx/media/grades/report.csv")
