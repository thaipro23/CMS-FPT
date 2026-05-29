from django.contrib import admin

from .models import UnitResetAudit, UnitResetControl


@admin.register(UnitResetControl)
class UnitResetControlAdmin(admin.ModelAdmin):
    list_display = (
        "id",
        "user",
        "course_id",
        "unit_usage_key",
        "reset_count",
        "last_attempt_at",
        "last_reset_at",
        "next_reset_allowed_at",
        "cooldown_seconds",
        "updated_at",
    )
    search_fields = ("user__username", "user__email", "course_id", "unit_usage_key")
    list_filter = ("course_id",)
    readonly_fields = ("created_at", "updated_at")


@admin.register(UnitResetAudit)
class UnitResetAuditAdmin(admin.ModelAdmin):
    list_display = (
        "id",
        "user",
        "course_id",
        "unit_usage_key",
        "success",
        "code",
        "wait_seconds",
        "deleted_count",
        "created_at",
    )
    search_fields = ("user__username", "user__email", "course_id", "unit_usage_key", "code")
    list_filter = ("success", "code", "course_id")
    readonly_fields = ("created_at",)
