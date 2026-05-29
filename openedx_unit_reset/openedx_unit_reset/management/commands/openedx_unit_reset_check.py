from django.conf import settings
from django.core.management.base import BaseCommand

from openedx_unit_reset.models import UnitResetAudit, UnitResetControl
from openedx_unit_reset.services import get_modulestore, get_student_module_model


class Command(BaseCommand):
    help = "Check openedx_unit_reset plugin installation."

    def handle(self, *args, **options):
        self.stdout.write(self.style.SUCCESS("openedx_unit_reset import OK"))
        self.stdout.write(f"UNIT_RESET_DEFAULT_COOLDOWN_SECONDS={getattr(settings, 'UNIT_RESET_DEFAULT_COOLDOWN_SECONDS', None)}")
        self.stdout.write(f"UNIT_RESET_REQUIRE_COOLDOWN={getattr(settings, 'UNIT_RESET_REQUIRE_COOLDOWN', None)}")
        self.stdout.write(f"Control table model={UnitResetControl._meta.db_table}")
        self.stdout.write(f"Audit table model={UnitResetAudit._meta.db_table}")

        try:
            StudentModule = get_student_module_model()
            self.stdout.write(self.style.SUCCESS(f"StudentModule import OK: {StudentModule.__module__}.{StudentModule.__name__}"))
        except Exception as exc:
            self.stdout.write(self.style.ERROR(f"StudentModule import FAILED: {exc}"))
            raise

        try:
            store = get_modulestore()
            self.stdout.write(self.style.SUCCESS(f"modulestore import OK: {store.__class__.__name__}"))
        except Exception as exc:
            self.stdout.write(self.style.ERROR(f"modulestore import FAILED: {exc}"))
            raise
