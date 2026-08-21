"""FEID OAuth2 backend compatible with Open edX Ulmo."""

import json
from collections.abc import Mapping
from typing import ClassVar
from urllib.parse import quote, urlencode

from social_core.backends.oauth import BaseOAuth2PKCE
from social_core.exceptions import AuthMissingParameter


def _parse_mapping(value):
    """Return a mapping from a mapping or JSON object string."""

    if isinstance(value, Mapping):
        return value
    if not isinstance(value, str):
        return None
    try:
        parsed = json.loads(value)
    except (TypeError, ValueError):
        return None
    return parsed if isinstance(parsed, Mapping) else None


def extract_roll_numbers(response):
    """Extract every unique non-empty FEID RollNumber from projectCampuses."""

    project_campuses = response.get("projectCampuses") or []
    if isinstance(project_campuses, str):
        try:
            project_campuses = json.loads(project_campuses)
        except (TypeError, ValueError):
            return []

    if not isinstance(project_campuses, list):
        return []

    roll_numbers = []
    seen = set()
    for entry in project_campuses:
        campus = _parse_mapping(entry)
        if not campus:
            continue
        roll_number = campus.get("RollNumber")
        if roll_number is None:
            continue
        normalized = str(roll_number).strip()
        if not normalized:
            continue
        key = normalized.casefold()
        if key in seen:
            continue
        seen.add(key)
        roll_numbers.append(normalized)
    return roll_numbers


def extract_roll_number(response):
    """Return the first FEID RollNumber for backwards-compatible user details."""

    roll_numbers = extract_roll_numbers(response)
    return roll_numbers[0] if roll_numbers else None


class FEIDOAuth2(BaseOAuth2PKCE):
    """FEID OAuth2/OIDC backend with PKCE and validated state."""

    name = "feid"
    BASE_URL = "https://feid.fpt.edu.vn"
    LOGIN_URL = f"{BASE_URL}/Account/Login"
    AUTHORIZATION_CALLBACK_PATH = "/connect/authorize/callback"
    AUTHORIZATION_URL = f"{BASE_URL}{AUTHORIZATION_CALLBACK_PATH}"
    ACCESS_TOKEN_URL = f"{BASE_URL}/connect/token"
    USER_DATA_URL = f"{BASE_URL}/connect/userinfo"

    # This is an HTTP method, not a credential.
    ACCESS_TOKEN_METHOD = "POST"  # nosec B105
    DEFAULT_SCOPE: ClassVar[list[str]] = [
        "openid",
        "profile",
        "email",
        "offline_access",
    ]
    EXTRA_DATA: ClassVar[list[tuple[str, str, bool]]] = [
        ("refresh_token", "refresh_token", True)
    ]

    # Keep the callback URI identical between authorize and token exchange.
    # State is still required and validated as its own OAuth parameter.
    REDIRECT_STATE = False
    STATE_PARAMETER = True

    def auth_url(self):
        """Build FEID's legacy Account/Login?ReturnUrl=... redirect."""

        state = self.get_or_create_state()
        params = self.auth_params(state)
        params.update(self.get_scope_argument())
        params.update(self.auth_extra_arguments())
        nonce = self.strategy.random_string(32)
        self.strategy.session_set("feid_nonce", nonce)
        params["nonce"] = nonce

        if not self.strategy.session_get("auth_entry"):
            self.strategy.session_set("auth_entry", "login")

        return_url = (
            f"{self.AUTHORIZATION_CALLBACK_PATH}?{urlencode(params, quote_via=quote)}"
        )
        return (
            f"{self.LOGIN_URL}?{urlencode({'ReturnUrl': return_url}, quote_via=quote)}"
        )

    def auth_complete_params(self, state=None):
        """Add PKCE verifier and omit an empty secret for public FEID clients."""

        params = super().auth_complete_params(state=state)
        if not params.get("code_verifier"):
            raise AuthMissingParameter(self, "code_verifier")
        if not params.get("client_secret"):
            params.pop("client_secret", None)
        return params

    def get_user_id(self, details, response):
        """Use stable FEID subject; retain `id` for legacy FEID payloads."""

        for claim in ("sub", "id"):
            subject = response.get(claim)
            if subject is not None and str(subject).strip():
                return str(subject).strip()
        raise AuthMissingParameter(self, "sub")

    def get_user_details(self, response):
        """Expose RollNumber as username without creating or changing a user."""

        full_name = response.get("name") or ""
        first_name, last_name = self.get_user_names(
            full_name,
            response.get("given_name") or "",
            response.get("family_name") or "",
        )[1:]
        return {
            "username": extract_roll_number(response),
            "email": response.get("email") or "",
            "fullname": full_name,
            "first_name": first_name,
            "last_name": last_name,
        }

    def user_data(self, access_token, *args, **kwargs):
        """Fetch FEID userinfo without logging tokens or identity payloads."""

        return self.get_json(
            self.USER_DATA_URL,
            headers={"Authorization": f"Bearer {access_token}"},
        )
