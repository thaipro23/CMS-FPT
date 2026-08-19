"""Open edX plugin registration and pipeline installation."""

from typing import ClassVar

from django.apps import AppConfig
from django.conf import settings
from django.core.exceptions import ImproperlyConfigured

SOCIAL_USER_STAGE = "social_core.pipeline.social_auth.social_user"
CREATE_USER_STAGE = "social_core.pipeline.user.create_user"
LINK_STAGE = "openedx_fpt_auth.pipeline.associate_existing_user"
CREATE_GUARD_STAGE = "openedx_fpt_auth.pipeline.block_supported_provider_user_creation"
LEGACY_STAGE = (
    "common.djangoapps.third_party_auth.feid_pipeline.associate_by_username_only"
)


def install_pipeline(django_settings):
    """Install map-only stages idempotently and remove the unsafe legacy stage."""

    pipeline = list(getattr(django_settings, "SOCIAL_AUTH_PIPELINE", []))
    if not pipeline:
        raise ImproperlyConfigured(
            "FPT Auth requires the Open edX third-party auth pipeline"
        )

    pipeline = [
        stage
        for stage in pipeline
        if stage not in {LEGACY_STAGE, LINK_STAGE, CREATE_GUARD_STAGE}
    ]
    try:
        social_user_index = pipeline.index(SOCIAL_USER_STAGE)
    except ValueError as exc:
        raise ImproperlyConfigured(
            "FPT Auth cannot find the social_user pipeline stage"
        ) from exc

    pipeline.insert(social_user_index + 1, LINK_STAGE)

    if CREATE_USER_STAGE in pipeline:
        create_user_index = pipeline.index(CREATE_USER_STAGE)
        if create_user_index <= pipeline.index(LINK_STAGE):
            raise ImproperlyConfigured("FPT Auth resolver must run before create_user")
        pipeline.insert(create_user_index, CREATE_GUARD_STAGE)

    django_settings.SOCIAL_AUTH_PIPELINE = pipeline


class FPTAuthConfig(AppConfig):
    """LMS-only plugin app for FEID and Google existing-user linking."""

    name = "openedx_fpt_auth"
    verbose_name = "Open edX FPT Authentication"

    plugin_app: ClassVar[dict[str, object]] = {
        "settings_config": {
            "lms.djangoapp": {
                "common": {"relative_path": "settings.common"},
                "production": {"relative_path": "settings.production"},
                "devstack": {"relative_path": "settings.devstack"},
                "test": {"relative_path": "settings.test"},
            },
        },
    }

    def ready(self):
        if settings.FEATURES.get("ENABLE_THIRD_PARTY_AUTH", False):
            install_pipeline(settings)
