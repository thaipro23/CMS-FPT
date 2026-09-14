"""Aggregate presence API."""

from django.http import JsonResponse
from django.views.decorators.http import require_GET

from .service import get_presence_service


@require_GET
def count_view(_request):
    """Return the cached distinct active-user count."""

    response = JsonResponse({"online": get_presence_service().count_safely()})
    response["Cache-Control"] = "no-store"
    return response
