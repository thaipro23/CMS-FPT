import importlib
import importlib.util
from io import BytesIO
from unittest.mock import Mock, patch

from django.core import signing
from django.test import RequestFactory, SimpleTestCase, override_settings


ARTIFACT_KEY = "user_tasks/2026/09/21/course.test.tar.gz"


@override_settings(
    FPT_ARTIFACT_PROXY_TOKEN_TTL_SECONDS=300,
    FPT_ARTIFACT_STORAGE_KWARGS={"bucket_name": "openedx"},
)
class DownloadUserTaskArtifactViewTest(SimpleTestCase):
    def _module(self):
        module_name = "openedx_fpt_report_proxy.artifact_views"
        self.assertIsNotNone(
            importlib.util.find_spec(module_name),
            "artifact download view is not implemented",
        )
        return importlib.import_module(module_name)

    def _request(self, artifact_views, key=ARTIFACT_KEY, authenticated=True):
        token = signing.dumps(
            {"key": key},
            salt=artifact_views.ARTIFACT_TOKEN_SALT,
            compress=True,
        )
        request = RequestFactory().get(
            "/api/fpt-artifacts/v1/download",
            {"token": token},
        )
        request.user = Mock(is_authenticated=authenticated, id=42, pk=42)
        return request

    def test_streams_artifact_owned_by_current_user(self):
        artifact_views = self._module()
        request = self._request(artifact_views)

        storage = Mock()
        storage.exists.return_value = True
        storage.open.return_value = BytesIO(b"course export")

        with (
            patch.object(artifact_views.UserTaskArtifact.objects, "filter") as artifacts,
            patch.object(
                artifact_views,
                "FPTUserTaskArtifactProxyS3Storage",
                return_value=storage,
            ),
        ):
            artifacts.return_value.exists.return_value = True
            response = artifact_views.download_artifact(request)

        artifacts.assert_called_once_with(
            file=ARTIFACT_KEY,
            status__user_id=42,
        )
        self.assertEqual(response.status_code, 200)
        self.assertIn(
            'attachment; filename="course.test.tar.gz"',
            response["Content-Disposition"],
        )
        self.assertEqual(b"".join(response.streaming_content), b"course export")

    def test_rejects_unauthenticated_request(self):
        artifact_views = self._module()
        response = artifact_views.download_artifact(
            self._request(artifact_views, authenticated=False)
        )
        self.assertEqual(response.status_code, 401)

    def test_rejects_tampered_token(self):
        artifact_views = self._module()
        request = RequestFactory().get(
            "/api/fpt-artifacts/v1/download",
            {"token": "not-a-valid-token"},
        )
        request.user = Mock(is_authenticated=True, id=42, pk=42)
        response = artifact_views.download_artifact(request)
        self.assertEqual(response.status_code, 400)

    def test_expired_token_returns_410(self):
        artifact_views = self._module()
        with patch.object(
            artifact_views.signing,
            "loads",
            side_effect=signing.SignatureExpired("expired"),
        ):
            response = artifact_views.download_artifact(self._request(artifact_views))
        self.assertEqual(response.status_code, 410)

    def test_rejects_key_outside_user_tasks_prefix(self):
        artifact_views = self._module()
        response = artifact_views.download_artifact(
            self._request(artifact_views, key="openedx/media/grades/report.csv")
        )
        self.assertEqual(response.status_code, 400)

    def test_rejects_artifact_owned_by_another_user(self):
        artifact_views = self._module()
        with patch.object(artifact_views.UserTaskArtifact.objects, "filter") as artifacts:
            artifacts.return_value.exists.return_value = False
            response = artifact_views.download_artifact(self._request(artifact_views))
        self.assertEqual(response.status_code, 403)

    def test_missing_artifact_returns_404(self):
        artifact_views = self._module()
        storage = Mock()
        storage.exists.return_value = False
        with (
            patch.object(artifact_views.UserTaskArtifact.objects, "filter") as artifacts,
            patch.object(
                artifact_views,
                "FPTUserTaskArtifactProxyS3Storage",
                return_value=storage,
            ),
        ):
            artifacts.return_value.exists.return_value = True
            response = artifact_views.download_artifact(self._request(artifact_views))
        self.assertEqual(response.status_code, 404)

    def test_storage_failure_returns_503(self):
        artifact_views = self._module()
        storage = Mock()
        storage.exists.side_effect = OSError("storage unavailable")
        with (
            patch.object(artifact_views.UserTaskArtifact.objects, "filter") as artifacts,
            patch.object(
                artifact_views,
                "FPTUserTaskArtifactProxyS3Storage",
                return_value=storage,
            ),
        ):
            artifacts.return_value.exists.return_value = True
            response = artifact_views.download_artifact(self._request(artifact_views))
        self.assertEqual(response.status_code, 503)
