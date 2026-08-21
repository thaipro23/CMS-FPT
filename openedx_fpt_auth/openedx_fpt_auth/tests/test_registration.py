from types import SimpleNamespace

from openedx_fpt_auth.apps import (
    CREATE_GUARD_STAGE,
    CREATE_USER_STAGE,
    FPT_COOKIE_STAGE,
    LEGACY_STAGE,
    LINK_STAGE,
    OPENEDX_COOKIE_STAGE,
    SOCIAL_USER_STAGE,
    install_pipeline,
)
from openedx_fpt_auth.settings.common import (
    FEID_BACKEND_PATH,
    GOOGLE_BACKEND_PATH,
    LEGACY_FEID_BACKEND_PATH,
    install_backends,
    plugin_settings,
)


def test_plugin_settings_replace_legacy_backend_and_enable_stable_google_uid():
    settings = SimpleNamespace(
        AUTHENTICATION_BACKENDS=[
            LEGACY_FEID_BACKEND_PATH,
            "django.contrib.auth.backends.ModelBackend",
        ]
    )
    plugin_settings(settings)
    assert settings.AUTHENTICATION_BACKENDS[:2] == [
        FEID_BACKEND_PATH,
        GOOGLE_BACKEND_PATH,
    ]
    assert LEGACY_FEID_BACKEND_PATH not in settings.AUTHENTICATION_BACKENDS
    assert settings.SOCIAL_AUTH_GOOGLE_OAUTH2_USE_UNIQUE_USER_ID is True
    assert settings.FPT_AUTH_EXISTING_USERS_ONLY is True


def test_backend_finalizer_removes_duplicates_after_production_settings():
    settings = SimpleNamespace(
        AUTHENTICATION_BACKENDS=[
            GOOGLE_BACKEND_PATH,
            FEID_BACKEND_PATH,
            GOOGLE_BACKEND_PATH,
            "django.contrib.auth.backends.ModelBackend",
        ]
    )
    install_backends(settings)
    assert settings.AUTHENTICATION_BACKENDS == [
        FEID_BACKEND_PATH,
        GOOGLE_BACKEND_PATH,
        "django.contrib.auth.backends.ModelBackend",
    ]


def test_pipeline_installation_is_idempotent_and_before_create_user():
    settings = SimpleNamespace(
        SOCIAL_AUTH_PIPELINE=[
            "social_core.pipeline.social_auth.social_details",
            SOCIAL_USER_STAGE,
            LEGACY_STAGE,
            "common.djangoapps.third_party_auth.pipeline.get_username",
            CREATE_USER_STAGE,
            "social_core.pipeline.social_auth.associate_user",
            OPENEDX_COOKIE_STAGE,
        ]
    )
    install_pipeline(settings)
    install_pipeline(settings)

    pipeline = settings.SOCIAL_AUTH_PIPELINE
    assert LEGACY_STAGE not in pipeline
    assert pipeline.count(LINK_STAGE) == 1
    assert pipeline.count(CREATE_GUARD_STAGE) == 1
    assert pipeline.count(FPT_COOKIE_STAGE) == 1
    assert OPENEDX_COOKIE_STAGE not in pipeline
    assert pipeline.index(SOCIAL_USER_STAGE) < pipeline.index(LINK_STAGE)
    assert pipeline.index(LINK_STAGE) < pipeline.index(CREATE_GUARD_STAGE)
    assert pipeline.index(CREATE_GUARD_STAGE) < pipeline.index(CREATE_USER_STAGE)
