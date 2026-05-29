from django.conf import settings
from django.db import migrations, models
import django.db.models.deletion
import django.utils.timezone


class Migration(migrations.Migration):
    initial = True

    dependencies = [
        migrations.swappable_dependency(settings.AUTH_USER_MODEL),
    ]

    operations = [
        migrations.CreateModel(
            name="UnitResetControl",
            fields=[
                ("id", models.BigAutoField(auto_created=True, primary_key=True, serialize=False, verbose_name="ID")),
                ("course_id", models.CharField(db_index=True, max_length=255)),
                ("unit_usage_key", models.CharField(db_index=True, max_length=255)),
                ("reset_count", models.PositiveIntegerField(default=0)),
                ("last_attempt_at", models.DateTimeField(blank=True, null=True)),
                ("last_reset_at", models.DateTimeField(blank=True, null=True)),
                ("next_reset_allowed_at", models.DateTimeField(blank=True, null=True)),
                ("cooldown_seconds", models.PositiveIntegerField(default=0)),
                ("last_ip", models.GenericIPAddressField(blank=True, null=True)),
                ("last_user_agent", models.TextField(blank=True, default="")),
                ("created_at", models.DateTimeField(default=django.utils.timezone.now)),
                ("updated_at", models.DateTimeField(auto_now=True)),
                ("user", models.ForeignKey(on_delete=django.db.models.deletion.CASCADE, to=settings.AUTH_USER_MODEL)),
            ],
            options={
                "unique_together": {("user", "course_id", "unit_usage_key")},
            },
        ),
        migrations.CreateModel(
            name="UnitResetAudit",
            fields=[
                ("id", models.BigAutoField(auto_created=True, primary_key=True, serialize=False, verbose_name="ID")),
                ("course_id", models.CharField(db_index=True, max_length=255)),
                ("unit_usage_key", models.CharField(db_index=True, max_length=255)),
                ("action", models.CharField(default="reset_unit", max_length=64)),
                ("success", models.BooleanField(default=False)),
                ("code", models.CharField(blank=True, default="", max_length=64)),
                ("message", models.TextField(blank=True, default="")),
                ("wait_seconds", models.PositiveIntegerField(default=0)),
                ("cooldown_seconds", models.PositiveIntegerField(default=0)),
                ("deleted_count", models.PositiveIntegerField(default=0)),
                ("reset_keys_count", models.PositiveIntegerField(default=0)),
                ("ip_address", models.GenericIPAddressField(blank=True, null=True)),
                ("user_agent", models.TextField(blank=True, default="")),
                ("created_at", models.DateTimeField(default=django.utils.timezone.now)),
                ("user", models.ForeignKey(on_delete=django.db.models.deletion.CASCADE, to=settings.AUTH_USER_MODEL)),
            ],
        ),
        migrations.AddIndex(
            model_name="unitresetcontrol",
            index=models.Index(fields=["user", "course_id"], name="unit_reset_user_course_idx"),
        ),
        migrations.AddIndex(
            model_name="unitresetcontrol",
            index=models.Index(fields=["course_id", "unit_usage_key"], name="unit_reset_course_unit_idx"),
        ),
        migrations.AddIndex(
            model_name="unitresetaudit",
            index=models.Index(fields=["user", "course_id", "unit_usage_key"], name="unit_reset_audit_user_idx"),
        ),
        migrations.AddIndex(
            model_name="unitresetaudit",
            index=models.Index(fields=["created_at"], name="unit_reset_audit_created_idx"),
        ),
    ]
