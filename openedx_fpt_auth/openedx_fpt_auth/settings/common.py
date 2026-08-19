"""Common LMS settings for FPT FEID/Google authentication."""

FEID_BACKEND_PATH = "openedx_fpt_auth.backends.FEIDOAuth2"
LEGACY_FEID_BACKEND_PATH = "common.djangoapps.third_party_auth.feid_backend.FEIDOAuth2"
GOOGLE_BACKEND_PATH = "social_core.backends.google.GoogleOAuth2"


def install_backends(settings):
    """Register both OAuth backends once, preserving all unrelated backends."""

    existing = [
        backend
        for backend in list(getattr(settings, "AUTHENTICATION_BACKENDS", []))
        if backend
        not in {FEID_BACKEND_PATH, LEGACY_FEID_BACKEND_PATH, GOOGLE_BACKEND_PATH}
    ]
    settings.AUTHENTICATION_BACKENDS = [
        FEID_BACKEND_PATH,
        GOOGLE_BACKEND_PATH,
        *existing,
    ]


def plugin_settings(settings):
    """Register both OAuth backends before third_party_auth imports its models."""

    install_backends(settings)

    # Stable provider subjects are used for persistent UserSocialAuth links.
    settings.SOCIAL_AUTH_GOOGLE_OAUTH2_USE_UNIQUE_USER_ID = True
    settings.SOCIAL_AUTH_FEID_PKCE_CODE_CHALLENGE_METHOD = "S256"
    settings.SOCIAL_AUTH_FEID_PKCE_CODE_VERIFIER_LENGTH = 64
    settings.FPT_AUTH_EXISTING_USERS_ONLY = True
