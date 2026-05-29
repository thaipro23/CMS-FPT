from django.urls import path

from .views import (
    health,
    course_content,
    studio_course_content,
    publish_problem,
    ensure_chapter_library,
    import_problem_to_library,
    publish_diagnostics,
    backfill_library_tags,
    library_tags_diagnostics,
)

# NOTE:
# OpenEdxAIConnectorConfig already mounts this module under:
#   /api/ai-connector/v1/
# Therefore paths here must be RELATIVE. Do not repeat the prefix.
urlpatterns = [
    path("health", health, name="ai_connector_health"),
    path("health/", health, name="ai_connector_health_slash"),
    path("publish-diagnostics", publish_diagnostics, name="ai_connector_publish_diagnostics"),
    path("publish-diagnostics/", publish_diagnostics, name="ai_connector_publish_diagnostics_slash"),
    path("courses/<path:course_id>/content", course_content, name="ai_connector_course_content"),
    path("courses/<path:course_id>/studio-content", studio_course_content, name="ai_connector_studio_content"),
    path("courses/<path:course_id>/problems", publish_problem, name="ai_connector_publish_problem"),
    path("courses/<path:course_id>/libraries", ensure_chapter_library, name="ai_connector_ensure_chapter_library"),
    path("libraries/<path:library_key>/problems", import_problem_to_library, name="ai_connector_import_problem_to_library"),
    path("libraries/<path:library_key>/backfill-tags", backfill_library_tags, name="ai_connector_backfill_library_tags"),
    path("libraries/<path:library_key>/backfill-tags/", backfill_library_tags, name="ai_connector_backfill_library_tags_slash"),
    path("libraries/<path:library_key>/tags/diagnostics", library_tags_diagnostics, name="ai_connector_library_tags_diagnostics"),
    path("libraries/<path:library_key>/tags/diagnostics/", library_tags_diagnostics, name="ai_connector_library_tags_diagnostics_slash"),
]
