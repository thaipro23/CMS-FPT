from django.test import SimpleTestCase

from openedx_fpt_report_proxy.apps import FPTReportProxyConfig


class ArtifactPluginRegistrationTest(SimpleTestCase):
    def test_cms_registers_artifact_download_route(self):
        cms_url_config = FPTReportProxyConfig.plugin_app["url_config"]["cms.djangoapp"]

        self.assertEqual(cms_url_config["regex"], r"^api/fpt-artifacts/v1/")
        self.assertEqual(cms_url_config["relative_path"], "artifact_urls")
