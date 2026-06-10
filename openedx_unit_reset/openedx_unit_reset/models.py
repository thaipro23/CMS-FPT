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



class UnitQuizTimerConfig(models.Model):
    """Custom timed-practice config per Open edX Unit created by AI Server.

    This is intentionally not Open edX native Timed Exam. It supports practice
    quiz sessions, auto-submit-on-timeout, lock-after-timeout, and retake cooldown.
    """

    course_id = models.CharField(max_length=255, db_index=True)
    sequence_usage_key = models.CharField(max_length=255, blank=True, default='', db_index=True)
    unit_usage_key = models.CharField(max_length=255, db_index=True)
    title = models.CharField(max_length=255, blank=True, default='Quiz tự luyện')

    enabled = models.BooleanField(default=True)
    duration_seconds = models.PositiveIntegerField(default=900)
    cooldown_seconds = models.PositiveIntegerField(default=300)
    auto_submit_on_timeout = models.BooleanField(default=True)
    lock_after_timeout = models.BooleanField(default=True)
    native_timed_exam = models.BooleanField(default=False)

    created_by = models.CharField(max_length=255, blank=True, default='')
    updated_by = models.CharField(max_length=255, blank=True, default='')
    metadata_json = models.JSONField(default=dict, blank=True)
    created_at = models.DateTimeField(default=timezone.now)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        unique_together = (("course_id", "unit_usage_key"),)
        indexes = [
            models.Index(fields=["course_id", "unit_usage_key"], name="unit_quiz_cfg_course_unit_idx"),
            models.Index(fields=["course_id", "sequence_usage_key"], name="unit_quiz_cfg_seq_idx"),
            models.Index(fields=["enabled"], name="unit_quiz_cfg_enabled_idx"),
        ]

    def __str__(self):
        return f"{self.course_id} {self.unit_usage_key} {self.duration_seconds}s"


class UnitQuizSession(models.Model):
    """Server-side learner timer session for a practice quiz Unit."""

    STATUS_ACTIVE = 'ACTIVE'
    STATUS_SUBMITTING = 'SUBMITTING'
    STATUS_EXPIRED = 'EXPIRED'
    STATUS_RESET_WAIT = 'RESET_WAIT'
    STATUS_RESET_READY = 'RESET_READY'

    user = models.ForeignKey(settings.AUTH_USER_MODEL, on_delete=models.CASCADE)
    config = models.ForeignKey(UnitQuizTimerConfig, on_delete=models.CASCADE, related_name='sessions')
    course_id = models.CharField(max_length=255, db_index=True)
    sequence_usage_key = models.CharField(max_length=255, blank=True, default='', db_index=True)
    unit_usage_key = models.CharField(max_length=255, db_index=True)
    attempt_no = models.PositiveIntegerField(default=1)
    duration_seconds = models.PositiveIntegerField(default=900)
    cooldown_seconds = models.PositiveIntegerField(default=300)
    started_at = models.DateTimeField(default=timezone.now)
    expires_at = models.DateTimeField(db_index=True)
    status = models.CharField(max_length=32, default=STATUS_ACTIVE, db_index=True)
    auto_submitted_at = models.DateTimeField(null=True, blank=True)
    locked_at = models.DateTimeField(null=True, blank=True)
    reset_available_at = models.DateTimeField(null=True, blank=True, db_index=True)
    timeout_payload = models.JSONField(default=dict, blank=True)
    last_ip = models.GenericIPAddressField(null=True, blank=True)
    last_user_agent = models.TextField(blank=True, default='')
    created_at = models.DateTimeField(default=timezone.now)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        indexes = [
            models.Index(fields=["user", "course_id", "unit_usage_key", "status"], name="unit_quiz_sess_user_status_idx"),
            models.Index(fields=["course_id", "unit_usage_key", "status"], name="unit_quiz_sess_unit_status_idx"),
            models.Index(fields=["expires_at", "status"], name="unit_quiz_sess_exp_status_idx"),
        ]

    def __str__(self):
        return f"{self.user_id} {self.course_id} {self.unit_usage_key} attempt={self.attempt_no} {self.status}"
