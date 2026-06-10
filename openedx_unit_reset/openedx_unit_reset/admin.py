from django.contrib import admin

from .models import UnitQuizSession, UnitQuizTimerConfig, UnitResetAudit, UnitResetControl


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



@admin.register(UnitQuizTimerConfig)
class UnitQuizTimerConfigAdmin(admin.ModelAdmin):
    list_display = (
        "id", "course_id", "title", "unit_usage_key", "enabled",
        "duration_seconds", "cooldown_seconds", "auto_submit_on_timeout",
        "lock_after_timeout", "native_timed_exam", "updated_at",
    )
    search_fields = ("course_id", "sequence_usage_key", "unit_usage_key", "title")
    list_filter = ("enabled", "auto_submit_on_timeout", "lock_after_timeout", "native_timed_exam")
    readonly_fields = ("created_at", "updated_at")


@admin.register(UnitQuizSession)
class UnitQuizSessionAdmin(admin.ModelAdmin):
    list_display = (
        "id", "user", "course_id", "unit_usage_key", "attempt_no",
        "status", "started_at", "expires_at", "locked_at", "reset_available_at",
    )
    search_fields = ("user__username", "user__email", "course_id", "unit_usage_key")
    list_filter = ("status", "course_id")
    readonly_fields = ("created_at", "updated_at")
