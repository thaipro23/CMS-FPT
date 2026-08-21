"""Fail-closed mapping of FEID/Google identities to existing Open edX users."""

import logging

from django.contrib.auth import get_user_model
from social_core.exceptions import AuthForbidden

from .backends import extract_roll_numbers

logger = logging.getLogger(__name__)

FEID_BACKEND = "feid"
GOOGLE_BACKEND = "google-oauth2"
SUPPORTED_BACKENDS = frozenset({FEID_BACKEND, GOOGLE_BACKEND})
MAX_ROLL_NUMBER_LENGTH = 150
MAX_EMAIL_LENGTH = 254


def _deny(backend, reason):
    """Deny without placing email, RollNumber, token or provider payload in logs."""

    logger.warning(
        "[FPT_AUTH] Existing-user link denied backend=%s reason=%s",
        backend.name,
        reason,
    )
    raise AuthForbidden(backend)


def _require_active(backend, user):
    if not getattr(user, "is_active", False):
        _deny(backend, "inactive_user")
    return user


def _mapped_user(backend, user):
    """Return a map-only result and prevent downstream profile synchronization."""

    return {
        "user": _require_active(backend, user),
        "is_new": False,
        # Open edX runs user/profile synchronization later in the standard
        # pipeline. Identity data is needed only to resolve the existing user,
        # so discard it once mapping succeeds.
        "details": {},
    }


def _find_unique_user(backend, **lookup):
    """Return exactly one match while bounding the query result to two rows."""

    manager = get_user_model()._default_manager
    matches = list(manager.filter(**lookup).order_by("pk")[:2])
    if not matches:
        _deny(backend, "user_not_found")
    if len(matches) != 1:
        _deny(backend, "ambiguous_user")
    return _require_active(backend, matches[0])


def _find_unique_feid_user(backend, roll_numbers):
    """Match any FEID RollNumber to CMS auth_user.username, requiring one user."""

    manager = get_user_model()._default_manager
    matches_by_pk = {}

    for roll_number in roll_numbers:
        if not roll_number or len(roll_number) > MAX_ROLL_NUMBER_LENGTH:
            continue
        matches = list(
            manager.filter(username__iexact=roll_number).order_by("pk")[:2]
        )
        for matched_user in matches:
            matches_by_pk[matched_user.pk] = matched_user

    if not matches_by_pk:
        _deny(backend, "user_not_found")
    if len(matches_by_pk) != 1:
        _deny(backend, "ambiguous_user")

    return _require_active(backend, next(iter(matches_by_pk.values())))


def _is_verified_google_email(response):
    value = response.get("email_verified")
    return value is True or (
        isinstance(value, str) and value.strip().casefold() == "true"
    )


def associate_existing_user(
    backend, details, response, user=None, social=None, *args, **kwargs
):
    """Resolve FEID by RollNumber or Google by verified email; never create a user."""

    if backend.name not in SUPPORTED_BACKENDS:
        return None

    # A stable provider+uid association always wins on subsequent logins. It
    # remains valid if a provider later changes email or campus metadata.
    if social is not None:
        if user is None:
            _deny(backend, "orphaned_social_association")
        return _mapped_user(backend, user)

    if backend.name == FEID_BACKEND:
        roll_numbers = [
            value
            for value in extract_roll_numbers(response or {})
            if value and len(value) <= MAX_ROLL_NUMBER_LENGTH
        ]
        if not roll_numbers:
            _deny(backend, "missing_or_invalid_roll_number")
        matched_user = _find_unique_feid_user(backend, roll_numbers)
    else:
        if not _is_verified_google_email(response or {}):
            _deny(backend, "google_email_not_verified")
        email = (response or {}).get("email")
        if not isinstance(email, str):
            _deny(backend, "missing_or_invalid_email")
        email = email.strip()
        if not email or len(email) > MAX_EMAIL_LENGTH or "@" not in email:
            _deny(backend, "missing_or_invalid_email")
        matched_user = _find_unique_user(backend, email__iexact=email)

    # During an explicit account-connect flow, python-social-auth can provide
    # the already authenticated user even though no provider link exists yet.
    # Do not let that bypass the FEID RollNumber / Google email contract.
    if user is not None and getattr(user, "pk", None) != matched_user.pk:
        _deny(backend, "authenticated_user_mismatch")

    return _mapped_user(backend, matched_user)


def block_supported_provider_user_creation(backend, user=None, *args, **kwargs):
    """Defense in depth immediately before python-social-auth create_user."""

    if backend.name in SUPPORTED_BACKENDS and user is None:
        _deny(backend, "user_missing_before_create_user")
