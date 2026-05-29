from django.conf import settings
from django.db import models
from django.utils import timezone


class UnitResetControl(models.Model):
    """
    Server-side reset/cooldown control per learner + course + Unit.

    We intentionally store course_id and unit_usage_key as strings to avoid tight
    coupling with opaque-key migration serialization across Open edX releases.
    max_length=255 avoids MySQL/InnoDB composite index length problems.
    """

    user = models.ForeignKey(settings.AUTH_USER_MODEL, on_delete=models.CASCADE)
    course_id = models.CharField(max_length=255, db_index=True)
    unit_usage_key = models.CharField(max_length=255, db_index=True)

    reset_count = models.PositiveIntegerField(default=0)
    last_attempt_at = models.DateTimeField(null=True, blank=True)
    last_reset_at = models.DateTimeField(null=True, blank=True)
    next_reset_allowed_at = models.DateTimeField(null=True, blank=True)
    cooldown_seconds = models.PositiveIntegerField(default=0)

    last_ip = models.GenericIPAddressField(null=True, blank=True)
    last_user_agent = models.TextField(blank=True, default="")

    created_at = models.DateTimeField(default=timezone.now)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        unique_together = (("user", "course_id", "unit_usage_key"),)
        indexes = [
            models.Index(fields=["user", "course_id"], name="unit_reset_user_course_idx"),
            models.Index(fields=["course_id", "unit_usage_key"], name="unit_reset_course_unit_idx"),
        ]

    def __str__(self):
        return f"{self.user_id} {self.course_id} {self.unit_usage_key}"


class UnitResetAudit(models.Model):
    """Append-only audit trail for reset attempts."""

    user = models.ForeignKey(settings.AUTH_USER_MODEL, on_delete=models.CASCADE)
    course_id = models.CharField(max_length=255, db_index=True)
    unit_usage_key = models.CharField(max_length=255, db_index=True)
    action = models.CharField(max_length=64, default="reset_unit")
    success = models.BooleanField(default=False)
    code = models.CharField(max_length=64, blank=True, default="")
    message = models.TextField(blank=True, default="")
    wait_seconds = models.PositiveIntegerField(default=0)
    cooldown_seconds = models.PositiveIntegerField(default=0)
    deleted_count = models.PositiveIntegerField(default=0)
    reset_keys_count = models.PositiveIntegerField(default=0)
    ip_address = models.GenericIPAddressField(null=True, blank=True)
    user_agent = models.TextField(blank=True, default="")
    created_at = models.DateTimeField(default=timezone.now)

    class Meta:
        indexes = [
            models.Index(fields=["user", "course_id", "unit_usage_key"], name="unit_reset_audit_user_idx"),
            models.Index(fields=["created_at"], name="unit_reset_audit_created_idx"),
        ]

    def __str__(self):
        return f"{self.user_id} {self.course_id} {self.unit_usage_key} {self.code}"
