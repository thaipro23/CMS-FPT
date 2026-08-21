from types import SimpleNamespace
from unittest.mock import patch

from openedx_fpt_auth.backends import extract_roll_numbers
from openedx_fpt_auth.pipeline import associate_existing_user


class FakeQuerySet:
    def __init__(self, users):
        self.users = list(users)

    def order_by(self, *args):
        return self

    def __getitem__(self, item):
        return self.users[item]


class FakeManager:
    def __init__(self, users_by_username):
        self.users_by_username = {
            key.casefold(): list(value) for key, value in users_by_username.items()
        }
        self.lookups = []

    def filter(self, **lookup):
        self.lookups.append(lookup)
        username = lookup["username__iexact"].casefold()
        return FakeQuerySet(self.users_by_username.get(username, []))


def test_extract_roll_numbers_returns_all_unique_values_in_order():
    response = {
        "projectCampuses": [
            {"RollNumber": " EMP001 "},
            {"RollNumber": "PH59017"},
            {"RollNumber": "ph59017"},
            {"RollNumber": ""},
        ]
    }

    assert extract_roll_numbers(response) == ["EMP001", "PH59017"]


def test_feid_maps_later_roll_number_to_cms_username():
    user = SimpleNamespace(pk=7, is_active=True, username="PH59017")
    manager = FakeManager({"PH59017": [user]})
    model = SimpleNamespace(_default_manager=manager)

    response = {
        "projectCampuses": [
            {"RollNumber": "EMP001"},
            {"RollNumber": "PH59017"},
        ]
    }

    with patch("openedx_fpt_auth.pipeline.get_user_model", return_value=model):
        result = associate_existing_user(
            backend=SimpleNamespace(name="feid"),
            details={"email": "ignored@example.invalid"},
            response=response,
            user=None,
            social=None,
        )

    assert result == {"user": user, "is_new": False, "details": {}}
    assert manager.lookups == [
        {"username__iexact": "EMP001"},
        {"username__iexact": "PH59017"},
    ]
