from django.urls import path

from .views import reset_unit_attempt, reset_unit_status

urlpatterns = [
    path("v1/reset/", reset_unit_attempt, name="reset_unit_attempt"),
    path("v1/status/", reset_unit_status, name="reset_unit_status"),
]
