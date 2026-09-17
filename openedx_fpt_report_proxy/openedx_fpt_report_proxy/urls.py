"""URLs for FPT private report downloads."""

from django.urls import path

from .views import download_report

app_name = "openedx_fpt_report_proxy"

urlpatterns = [
    path("download", download_report, name="download"),
]
