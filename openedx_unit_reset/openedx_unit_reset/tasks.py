"""Asynchronous reconciliation tasks for Unit Reset."""

import logging

from celery import shared_task
from celery.exceptions import Retry
from django.db import transaction
from django.utils import timezone

from .models import UnitResetGradeReconciliation, UnitResetCourseGradeLock

log = logging.getLogger(__name__)


@shared_task(
    bind=True,
    max_retries=2,
    default_retry_delay=40,
    acks_late=True,
    reject_on_worker_lost=True,
)
def reconcile_unit_reset_grade(self, reconciliation_id):
    """Rebuild one learner's course grade after reset transaction commits.

    The durable row makes retries and final failure visible. This task is the
    only grade rebuild produced by one Unit reset; leaf state deletion itself
    deliberately suppresses per-problem grade signals.
    """
    with transaction.atomic():
        reconciliation = UnitResetGradeReconciliation.objects.select_for_update().get(
            pk=reconciliation_id,
        )
        if reconciliation.status == UnitResetGradeReconciliation.STATUS_COMPLETED:
            return {"status": reconciliation.status, "reconciliation_id": reconciliation.id}
        reconciliation.status = UnitResetGradeReconciliation.STATUS_RUNNING
        reconciliation.attempts += 1
        reconciliation.task_id = str(getattr(self.request, "id", "") or "")
        reconciliation.started_at = reconciliation.started_at or timezone.now()
        reconciliation.last_error = ""
        reconciliation.save(update_fields=[
            "status", "attempts", "task_id", "started_at", "last_error", "updated_at",
        ])

    try:
        from opaque_keys.edx.keys import CourseKey
        from lms.djangoapps.grades.course_grade_factory import CourseGradeFactory
        from lms.djangoapps.grades.models_api import clear_prefetched_course_grades

        with transaction.atomic():
            # A rebuild touches the whole course. Share this lock with reset
            # mutations and other reconciliation rows for the same learner.
            UnitResetCourseGradeLock.acquire(reconciliation.user_id, reconciliation.course_id)
            # Redelivery/duplicate task messages must not rebuild this reset
            # concurrently. Hold the row lock through rebuild and completion.
            reconciliation = UnitResetGradeReconciliation.objects.select_for_update().get(pk=reconciliation_id)
            if reconciliation.status == UnitResetGradeReconciliation.STATUS_COMPLETED:
                return {"status": reconciliation.status, "reconciliation_id": reconciliation.id}
            course_key = CourseKey.from_string(reconciliation.course_id)
            clear_prefetched_course_grades(course_key)
            course_grade = CourseGradeFactory().update(
                user=reconciliation.user,
                course_key=course_key,
                force_update_subsections=True,
            )
            if course_grade is None:
                raise RuntimeError("Open edX did not return a rebuilt course grade")
            clear_prefetched_course_grades(course_key)
            reconciliation.status = UnitResetGradeReconciliation.STATUS_COMPLETED
            reconciliation.last_error = ""
            reconciliation.completed_at = timezone.now()
            reconciliation.save(update_fields=["status", "last_error", "completed_at", "updated_at"])
    except Exception as exc:
        final_attempt = self.request.retries >= self.max_retries
        status = (
            UnitResetGradeReconciliation.STATUS_FAILED
            if final_attempt
            else UnitResetGradeReconciliation.STATUS_RETRYING
        )
        UnitResetGradeReconciliation.objects.filter(pk=reconciliation_id).exclude(
            status=UnitResetGradeReconciliation.STATUS_COMPLETED,
        ).update(
            status=status,
            last_error=str(exc)[:8000],
            completed_at=timezone.now() if final_attempt else None,
            updated_at=timezone.now(),
        )
        log.exception(
            "Unit reset grade reconciliation failed id=%s attempt=%s final=%s",
            reconciliation_id,
            self.request.retries + 1,
            final_attempt,
        )
        if final_attempt:
            raise
        try:
            raise self.retry(exc=exc) from exc
        except Retry:
            raise
        except Exception as publish_error:
            UnitResetGradeReconciliation.objects.filter(pk=reconciliation_id).exclude(
                status=UnitResetGradeReconciliation.STATUS_COMPLETED,
            ).update(
                status=UnitResetGradeReconciliation.STATUS_FAILED,
                last_error=f"Grade rebuild failed: {exc}; retry publish failed: {publish_error}"[:8000],
                completed_at=timezone.now(),
                updated_at=timezone.now(),
            )
            raise

    return {
        "status": UnitResetGradeReconciliation.STATUS_COMPLETED,
        "reconciliation_id": reconciliation_id,
        "percent": getattr(course_grade, "percent", None),
        "letter_grade": getattr(course_grade, "letter_grade", None),
    }
