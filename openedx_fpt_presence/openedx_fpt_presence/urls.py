"""URLs for FPT presence."""

from django.urls import path

from .views import count_view

app_name = "openedx_fpt_presence"

urlpatterns = [
    path("count", count_view, name="count"),
]
