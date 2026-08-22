from types import SimpleNamespace
from unittest.mock import patch

import pytest
from social_core.exceptions import AuthForbidden
from social_core.pipeline.social_auth import associate_user
from social_core.pipeline.user import create_user, user_details

from common.djangoapps.third_party_auth import pipeline as openedx_tpa_pipeline
from openedx_fpt_auth.pipeline import (
    associate_existing_user,
    block_supported_provider_user_creation,
    set_logged_in_cookies_for_fpt_sso,
)


class FakeQuerySet:
    def __init__(self, users):
        self.users = list(users)

    def order_by(self, *args):
        return self

    def __getitem__(self, item):
        return self.users[item]


class FakeManager:
    def __init__(self, users):
        self.users = users
        self.lookups = []

    def filter(self, **lookup):
        self.lookups.append(lookup)
        return FakeQuerySet(self.users)


def run_with_users(users, **kwargs):
    manager = FakeManager(users)
    model = SimpleNamespace(_default_manager=manager)
    with patch("openedx_fpt_auth.pipeline.get_user_model", return_value=model):
        result = associate_existing_user(**kwargs)
    return result, manager


def backend(name):
    return SimpleNamespace(name=name)


def active_user(pk=1):
    return SimpleNamespace(pk=pk, is_active=True)


def test_feid_maps_only_by_roll_number_case_insensitively():
    user = active_user()
    result, manager = run_with_users(
        [user],
        backend=backend("feid"),
        details={"email": "ignored@fpt.edu.vn"},
        response={"projectCampuses": [{"RollNumber": " PH59017 "}]},
    )
    assert result == {"user": user, "is_new": False, "details": {}}
    assert manager.lookups == [{"username__iexact": "PH59017"}]


def test_feid_never_falls_back_to_email():
    with pytest.raises(AuthForbidden):
        run_with_users(
            [active_user()],
            backend=backend("feid"),
            details={"email": "teacher@fpt.edu.vn"},
            response={"email": "teacher@fpt.edu.vn", "projectCampuses": []},
        )


def test_google_maps_only_verified_email_case_insensitively():
    user = active_user()
    result, manager = run_with_users(
        [user],
        backend=backend("google-oauth2"),
        details={"username": "ignored"},
        response={"email": " Teacher@FPT.edu.vn ", "email_verified": True},
    )
    assert result == {"user": user, "is_new": False, "details": {}}
    assert manager.lookups == [{"email__iexact": "Teacher@FPT.edu.vn"}]


@pytest.mark.parametrize("verified", [False, None, "false"])
def test_google_rejects_unverified_email(verified):
    with pytest.raises(AuthForbidden):
        run_with_users(
            [active_user()],
            backend=backend("google-oauth2"),
            details={},
            response={"email": "teacher@fpt.edu.vn", "email_verified": verified},
        )


@pytest.mark.parametrize("users", [[], [active_user(1), active_user(2)]])
def test_missing_or_ambiguous_existing_user_is_denied(users):
    with pytest.raises(AuthForbidden):
        run_with_users(
            users,
            backend=backend("feid"),
            details={},
            response={"projectCampuses": [{"RollNumber": "PH59017"}]},
        )


def test_inactive_user_is_denied_for_new_or_existing_social_link():
    inactive = SimpleNamespace(pk=1, is_active=False)
    with pytest.raises(AuthForbidden):
        run_with_users(
            [inactive],
            backend=backend("feid"),
            details={},
            response={"projectCampuses": [{"RollNumber": "PH59017"}]},
        )
    with pytest.raises(AuthForbidden):
        associate_existing_user(
            backend=backend("google-oauth2"),
            details={},
            response={},
            user=inactive,
            social=object(),
        )


def test_existing_stable_social_link_is_reused_without_lookup():
    user = active_user()
    result = associate_existing_user(
        backend=backend("google-oauth2"),
        details={},
        response={},
        user=user,
        social=object(),
    )
    assert result == {"user": user, "is_new": False, "details": {}}


def test_unlinked_authenticated_user_must_match_provider_identity():
    current_user = active_user(1)
    matched_user = active_user(2)

    with pytest.raises(AuthForbidden):
        run_with_users(
            [matched_user],
            backend=backend("feid"),
            details={},
            response={"projectCampuses": [{"RollNumber": "PH59017"}]},
            user=current_user,
            social=None,
        )


def test_mapped_provider_details_cannot_update_existing_user_profile():
    user = SimpleNamespace(
        pk=1,
        is_active=True,
        username="PH59017",
        email="student@fpt.edu.vn",
        first_name="Existing",
    )
    changed_calls = []

    class Strategy:
        storage = SimpleNamespace(
            user=SimpleNamespace(
                changed=lambda changed_user: changed_calls.append(changed_user)
            )
        )

        @staticmethod
        def setting(name, default=None, backend=None):
            return default

    mapped = associate_existing_user(
        backend=backend("feid"),
        details={"first_name": "Provider Name"},
        response={"projectCampuses": [{"RollNumber": "PH59017"}]},
        user=user,
        social=object(),
    )
    user_details(
        strategy=Strategy(),
        details=mapped["details"],
        backend=backend("feid"),
        user=user,
    )

    assert user.first_name == "Existing"
    assert changed_calls == []


def test_other_providers_are_not_modified():
    assert (
        associate_existing_user(
            backend=backend("azuread-oauth2"), details={}, response={}
        )
        is None
    )
    assert (
        block_supported_provider_user_creation(backend("azuread-oauth2"), user=None)
        is None
    )


def test_create_user_guard_fails_closed_for_supported_providers():
    for name in ("feid", "google-oauth2"):
        with pytest.raises(AuthForbidden):
            block_supported_provider_user_creation(backend(name), user=None)
        assert (
            block_supported_provider_user_creation(backend(name), user=active_user())
            is None
        )


def test_standard_pipeline_creates_social_link_but_not_auth_user():
    user = active_user()
    social = SimpleNamespace(user=user)
    calls = []

    class UserStorage:
        @staticmethod
        def create_social_auth(linked_user, uid, provider):
            calls.append((linked_user, uid, provider))
            return social

    class Strategy:
        storage = SimpleNamespace(
            user=UserStorage(),
            is_integrity_error=lambda error: False,
        )

        @staticmethod
        def create_user(**fields):
            raise AssertionError("auth_user creation is forbidden")

    google_backend = SimpleNamespace(name="google-oauth2", strategy=Strategy())
    create_result = create_user(
        strategy=google_backend.strategy,
        details={"email": "teacher@fpt.edu.vn"},
        backend=google_backend,
        user=user,
    )
    associate_result = associate_user(
        backend=google_backend,
        uid="google-subject",
        user=user,
        social=None,
    )

    assert create_result == {"is_new": False}
    assert calls == [(user, "google-subject", "google-oauth2")]
    assert associate_result == {
        "social": social,
        "user": user,
        "new_association": True,
    }


def test_cookie_stage_delegates_to_unwrapped_openedx_partial():
    """Avoid nesting Open edX's @partial.partial and duplicating current_partial."""

    google_backend = backend("google-oauth2")
    user = SimpleNamespace(has_usable_password=lambda: True)
    strategy = SimpleNamespace()
    current_partial = SimpleNamespace(backend="google-oauth2")

    assert hasattr(set_logged_in_cookies_for_fpt_sso, "__wrapped__")

    with patch.object(
        openedx_tpa_pipeline.set_logged_in_cookies,
        "__wrapped__",
        return_value="delegated",
    ) as upstream:
        result = set_logged_in_cookies_for_fpt_sso.__wrapped__(
            backend=google_backend,
            user=user,
            strategy=strategy,
            auth_entry="login",
            current_partial=current_partial,
        )

    assert result == "delegated"
    upstream.assert_called_once_with(
        backend=google_backend,
        user=user,
        strategy=strategy,
        auth_entry="login",
        current_partial=current_partial,
    )
