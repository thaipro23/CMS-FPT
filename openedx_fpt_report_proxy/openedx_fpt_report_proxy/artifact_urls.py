"""URLs for FPT private Studio user-task artifact downloads."""

from django.urls import path

from .artifact_views import download_artifact

app_name = "openedx_fpt_report_proxy"

urlpatterns = [
    path("download", download_artifact, name="download_artifact"),
]
