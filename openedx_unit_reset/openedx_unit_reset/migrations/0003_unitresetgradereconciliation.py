from django.conf import settings
from django.db import migrations, models
import django.db.models.deletion
import django.utils.timezone


class Migration(migrations.Migration):

    dependencies = [
        migrations.swappable_dependency(settings.AUTH_USER_MODEL),
        ("openedx_unit_reset", "0002_custom_quiz_timer"),
    ]

    operations = [
        migrations.CreateModel(
            name="UnitResetCourseGradeLock",
            fields=[
                ("id", models.BigAutoField(auto_created=True, primary_key=True, serialize=False, verbose_name="ID")),
                ("course_id", models.CharField(max_length=255)),
                ("created_at", models.DateTimeField(default=django.utils.timezone.now)),
                ("user", models.ForeignKey(on_delete=django.db.models.deletion.CASCADE, to=settings.AUTH_USER_MODEL)),
            ],
            options={"unique_together": {("user", "course_id")}},
        ),
        migrations.CreateModel(
            name="UnitResetGradeReconciliation",
            fields=[
                ("id", models.BigAutoField(auto_created=True, primary_key=True, serialize=False, verbose_name="ID")),
                ("course_id", models.CharField(db_index=True, max_length=255)),
                ("unit_usage_key", models.CharField(db_index=True, max_length=255)),
                (
                    "status",
                    models.CharField(
                        choices=[
                            ("PENDING", "PENDING"),
                            ("RUNNING", "RUNNING"),
                            ("RETRYING", "RETRYING"),
                            ("COMPLETED", "COMPLETED"),
                            ("FAILED", "FAILED"),
                        ],
                        db_index=True,
                        default="PENDING",
                        max_length=16,
                    ),
                ),
                ("attempts", models.PositiveIntegerField(default=0)),
                ("task_id", models.CharField(blank=True, default="", max_length=255)),
                ("last_error", models.TextField(blank=True, default="")),
                ("created_at", models.DateTimeField(default=django.utils.timezone.now)),
                ("started_at", models.DateTimeField(blank=True, null=True)),
                ("completed_at", models.DateTimeField(blank=True, null=True)),
                ("updated_at", models.DateTimeField(auto_now=True)),
                (
                    "user",
                    models.ForeignKey(on_delete=django.db.models.deletion.CASCADE, to=settings.AUTH_USER_MODEL),
                ),
            ],
        ),
        migrations.AddIndex(
            model_name="unitresetgradereconciliation",
            index=models.Index(fields=["status", "created_at"], name="unit_reset_recon_status_idx"),
        ),
        migrations.AddIndex(
            model_name="unitresetgradereconciliation",
            index=models.Index(fields=["user", "course_id"], name="unit_reset_recon_user_idx"),
        ),
    ]
