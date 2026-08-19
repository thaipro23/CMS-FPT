"""Validate FPT Auth runtime configuration without printing PII or secrets."""

import json

from django.conf import settings
from django.contrib.auth import get_user_model
from django.core.management.base import BaseCommand, CommandError
from django.db.models import Count, Q
from django.db.models.functions import Lower
from social_django.models import UserSocialAuth

from common.djangoapps.third_party_auth import provider
from openedx_fpt_auth.apps import (
    CREATE_GUARD_STAGE,
    CREATE_USER_STAGE,
    LEGACY_STAGE,
    LINK_STAGE,
    SOCIAL_USER_STAGE,
)
from openedx_fpt_auth.settings.common import (
    FEID_BACKEND_PATH,
    GOOGLE_BACKEND_PATH,
    LEGACY_FEID_BACKEND_PATH,
)


def _duplicate_nonempty_groups(field_name):
    """Count case-insensitive duplicate identity groups without returning PII."""

    users = get_user_model()._default_manager
    return (
        users.filter(**{f"{field_name}__isnull": False})
        .exclude(**{field_name: ""})
        .annotate(normalized_identity=Lower(field_name))
        .values("normalized_identity")
        .annotate(identity_count=Count("pk"))
        .filter(identity_count__gt=1)
        .count()
    )


class Command(BaseCommand):
    help = "Validate FEID/Google existing-user-only auth configuration"

    def handle(self, *args, **options):
        backends = list(getattr(settings, "AUTHENTICATION_BACKENDS", []))
        pipeline = list(getattr(settings, "SOCIAL_AUTH_PIPELINE", []))
        issues = []

        if not settings.FEATURES.get("ENABLE_THIRD_PARTY_AUTH", False):
            issues.append("third_party_auth_disabled")
        if backends.count(FEID_BACKEND_PATH) != 1:
            issues.append("feid_backend_count_not_one")
        if backends.count(GOOGLE_BACKEND_PATH) != 1:
            issues.append("google_backend_count_not_one")
        if LEGACY_FEID_BACKEND_PATH in backends:
            issues.append("legacy_feid_backend_present")
        if not getattr(settings, "FPT_AUTH_EXISTING_USERS_ONLY", False):
            issues.append("existing_users_only_disabled")

        required_stages = (
            SOCIAL_USER_STAGE,
            LINK_STAGE,
            CREATE_GUARD_STAGE,
            CREATE_USER_STAGE,
        )
        if any(stage not in pipeline for stage in required_stages):
            issues.append("map_only_pipeline_missing")
        elif not (
            pipeline.index(SOCIAL_USER_STAGE)
            < pipeline.index(LINK_STAGE)
            < pipeline.index(CREATE_GUARD_STAGE)
            < pipeline.index(CREATE_USER_STAGE)
        ):
            issues.append("map_only_pipeline_order_invalid")
        if LEGACY_STAGE in pipeline:
            issues.append("legacy_direct_login_pipeline_present")
        if not getattr(settings, "SOCIAL_AUTH_GOOGLE_OAUTH2_USE_UNIQUE_USER_ID", False):
            issues.append("google_stable_uid_disabled")

        provider_counts = {}
        for backend_name in ("feid", "google-oauth2"):
            provider_counts[backend_name] = len(
                list(provider.Registry.get_enabled_by_backend_name(backend_name))
            )
            if provider_counts[backend_name] != 1:
                issues.append(f"{backend_name}_enabled_provider_count_not_one")

        supported_links = UserSocialAuth.objects.filter(
            provider__in=("feid", "google-oauth2")
        )
        invalid_uid_count = supported_links.filter(
            Q(uid__isnull=True) | Q(uid__in=("", "None", "null"))
        ).count()
        inactive_link_count = supported_links.filter(user__is_active=False).count()
        duplicate_username_group_count = _duplicate_nonempty_groups("username")
        duplicate_email_group_count = _duplicate_nonempty_groups("email")
        if invalid_uid_count:
            issues.append("invalid_provider_uid_links")
        if inactive_link_count:
            issues.append("inactive_user_links")
        if duplicate_username_group_count:
            issues.append("case_insensitive_username_duplicates")
        if duplicate_email_group_count:
            issues.append("case_insensitive_email_duplicates")

        result = {
            "status": "PASS" if not issues else "FAIL",
            "existing_users_only": bool(
                getattr(settings, "FPT_AUTH_EXISTING_USERS_ONLY", False)
            ),
            "enabled_provider_counts": provider_counts,
            "social_link_count": supported_links.count(),
            "invalid_uid_link_count": invalid_uid_count,
            "inactive_user_link_count": inactive_link_count,
            "duplicate_username_group_count": duplicate_username_group_count,
            "duplicate_email_group_count": duplicate_email_group_count,
            "issues": issues,
        }
        self.stdout.write(json.dumps(result, sort_keys=True))
        if issues:
            raise CommandError("FPT Auth runtime validation failed")
