from urllib.parse import parse_qs, unquote, urlparse

import pytest
from social_core.exceptions import AuthMissingParameter, AuthStateForbidden

from openedx_fpt_auth.backends import FEIDOAuth2, extract_roll_number


class FakeStrategy:
    def __init__(self):
        self.session = {}
        self.data = {}
        self.settings = {
            "KEY": "client-id",
            "SECRET": "",
            "PKCE_CODE_CHALLENGE_METHOD": "S256",
            "PKCE_CODE_VERIFIER_LENGTH": 64,
        }

    def request_data(self):
        return self.data

    def absolute_uri(self, uri=None):
        return uri or "https://cms.fpl.edu.vn"

    def setting(self, name, default=None, backend=None):
        return self.settings.get(name, default)

    def session_get(self, name, default=None):
        return self.session.get(name, default)

    def session_set(self, name, value):
        self.session[name] = value

    def random_string(self, length=32, chars=None):
        return "x" * length


def make_backend():
    strategy = FakeStrategy()
    backend = FEIDOAuth2(
        strategy, redirect_uri="https://cms.fpl.edu.vn/auth/complete/feid/"
    )
    return backend, strategy


def test_extract_roll_number_from_list_and_json_entries():
    assert (
        extract_roll_number({"projectCampuses": [{"RollNumber": " PH59017 "}]})
        == "PH59017"
    )
    assert (
        extract_roll_number(
            {"projectCampuses": '["{\\"RollNumber\\": \\"PH59018\\"}"]'}
        )
        == "PH59018"
    )


def test_extract_roll_number_uses_first_non_empty_legacy_value():
    response = {
        "projectCampuses": [
            {"RollNumber": ""},
            {"RollNumber": "PH1"},
            {"RollNumber": "PH2"},
        ]
    }
    assert extract_roll_number(response) == "PH1"


@pytest.mark.parametrize("value", [None, {}, "not-json", '{"RollNumber":"PH1"}'])
def test_extract_roll_number_rejects_invalid_project_campuses(value):
    assert extract_roll_number({"projectCampuses": value}) is None


def test_auth_url_uses_pkce_state_and_legacy_return_url():
    backend, strategy = make_backend()
    auth_url = backend.auth_url()
    outer = parse_qs(urlparse(auth_url).query)
    inner = parse_qs(urlparse(unquote(outer["ReturnUrl"][0])).query)

    assert auth_url.startswith("https://feid.fpt.edu.vn/Account/Login?")
    assert inner["client_id"] == ["client-id"]
    assert inner["redirect_uri"] == ["https://cms.fpl.edu.vn/auth/complete/feid/"]
    assert inner["state"] == ["x" * 32]
    assert inner["code_challenge_method"] == ["S256"]
    assert inner["nonce"] == ["x" * 32]
    assert "feid_code_verifier" in strategy.session
    assert strategy.session["feid_nonce"] == "x" * 32
    assert strategy.session["auth_entry"] == "login"


def test_oauth_state_is_validated_not_skipped():
    backend, strategy = make_backend()
    backend.auth_url()
    strategy.data = {"state": "wrong-state"}
    backend.data = strategy.data
    with pytest.raises(AuthStateForbidden):
        backend.validate_state()


def test_token_params_require_pkce_and_omit_empty_secret():
    backend, strategy = make_backend()
    backend.auth_url()
    backend.data = {"code": "auth-code"}
    params = backend.auth_complete_params("x" * 32)
    assert params["code_verifier"] == "x" * 64
    assert "client_secret" not in params

    strategy.session.pop("feid_code_verifier")
    with pytest.raises(AuthMissingParameter):
        backend.auth_complete_params("x" * 32)


def test_feid_uid_prefers_sub_and_supports_legacy_id():
    backend, _ = make_backend()
    assert backend.get_user_id({}, {"sub": "subject", "id": "legacy"}) == "subject"
    assert backend.get_user_id({}, {"id": "legacy"}) == "legacy"
    with pytest.raises(AuthMissingParameter):
        backend.get_user_id({}, {})
