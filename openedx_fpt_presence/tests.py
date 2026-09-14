from types import SimpleNamespace
from unittest import TestCase
from unittest.mock import Mock, patch

from django.test import RequestFactory, SimpleTestCase

from openedx_fpt_presence.middleware import FPTPresenceMiddleware
from openedx_fpt_presence.service import ACTIVE_KEY, PresenceService
from openedx_fpt_presence.views import count_view


class MutableClock:
    def __init__(self, value):
        self.value = float(value)

    def __call__(self):
        return self.value


class FakeRedis:
    def __init__(self):
        self.zsets = {}
        self.values = {}
        self.zadd_calls = []

    def zadd(self, key, mapping):
        self.zadd_calls.append((key, dict(mapping)))
        bucket = self.zsets.setdefault(key, {})
        bucket.update({str(member): float(score) for member, score in mapping.items()})
        return 1

    @staticmethod
    def _parse_bound(value):
        text = str(value)
        exclusive = text.startswith("(")
        if exclusive:
            text = text[1:]
        if text == "+inf":
            return float("inf"), exclusive
        if text == "-inf":
            return float("-inf"), exclusive
        return float(text), exclusive

    def zremrangebyscore(self, key, minimum, maximum):
        min_value, min_exclusive = self._parse_bound(minimum)
        max_value, max_exclusive = self._parse_bound(maximum)
        bucket = self.zsets.setdefault(key, {})
        removed = 0
        for member, score in list(bucket.items()):
            above_min = score > min_value if min_exclusive else score >= min_value
            below_max = score < max_value if max_exclusive else score <= max_value
            if above_min and below_max:
                del bucket[member]
                removed += 1
        return removed

    def zcount(self, key, minimum, maximum):
        min_value, min_exclusive = self._parse_bound(minimum)
        max_value, max_exclusive = self._parse_bound(maximum)
        total = 0
        for score in self.zsets.get(key, {}).values():
            above_min = score > min_value if min_exclusive else score >= min_value
            below_max = score < max_value if max_exclusive else score <= max_value
            total += bool(above_min and below_max)
        return total

    def get(self, key):
        return self.values.get(key)

    def set(self, key, value, ex=None):
        self.values[key] = str(value)
        return True


class BrokenRedis:
    def __getattr__(self, _name):
        def broken(*_args, **_kwargs):
            raise RuntimeError("redis unavailable")
        return broken


class PresenceServiceTests(TestCase):
    def test_same_user_is_touched_once_inside_throttle_window(self):
        clock = MutableClock(1_000)
        redis_client = FakeRedis()
        service = PresenceService(redis_client, clock=clock, touch_interval_seconds=60)

        self.assertTrue(service.touch(42))
        clock.value = 1_059
        self.assertFalse(service.touch(42))
        self.assertEqual(redis_client.zadd_calls, [(ACTIVE_KEY, {"42": 1_000.0})])

        clock.value = 1_060
        self.assertTrue(service.touch(42))
        self.assertEqual(redis_client.zadd_calls[-1], (ACTIVE_KEY, {"42": 1_060.0}))

    def test_count_only_includes_users_active_in_last_ten_minutes(self):
        clock = MutableClock(1_000)
        redis_client = FakeRedis()
        service = PresenceService(
            redis_client,
            clock=clock,
            active_window_seconds=600,
            touch_interval_seconds=0,
            count_cache_seconds=0,
        )

        service.touch(1)
        clock.value = 1_500
        service.touch(2)
        clock.value = 1_601

        self.assertEqual(service.count(), 1)
        self.assertNotIn("1", redis_client.zsets[ACTIVE_KEY])
        self.assertIn("2", redis_client.zsets[ACTIVE_KEY])

    def test_safe_methods_fail_open_when_redis_is_unavailable(self):
        service = PresenceService(BrokenRedis(), clock=MutableClock(1_000))
        self.assertFalse(service.touch_safely(42))
        self.assertIsNone(service.count_safely())


def make_request(path="/courses/course-v1:FPT+DEMO+FA26", *, authenticated=True, method="GET"):
    return SimpleNamespace(
        path_info=path,
        method=method,
        user=SimpleNamespace(is_authenticated=authenticated, pk=123),
    )


class PresenceMiddlewareTests(TestCase):
    def setUp(self):
        self.response = object()
        self.get_response = Mock(return_value=self.response)
        self.middleware = FPTPresenceMiddleware(self.get_response)

    @patch("openedx_fpt_presence.middleware.get_presence_service")
    def test_anonymous_user_is_not_tracked(self, get_service):
        self.assertIs(self.middleware(make_request(authenticated=False)), self.response)
        get_service.assert_not_called()

    @patch("openedx_fpt_presence.middleware.get_presence_service")
    def test_authenticated_request_is_tracked(self, get_service):
        self.assertIs(self.middleware(make_request()), self.response)
        get_service.return_value.touch_safely.assert_called_once_with(123)

    @patch("openedx_fpt_presence.middleware.get_presence_service")
    def test_count_endpoint_does_not_refresh_presence(self, get_service):
        self.assertIs(
            self.middleware(make_request("/api/fpt-presence/v1/count")),
            self.response,
        )
        get_service.assert_not_called()

    @patch("openedx_fpt_presence.middleware.get_presence_service")
    def test_presence_failure_never_breaks_original_response(self, get_service):
        get_service.return_value.touch_safely.side_effect = RuntimeError("redis down")
        self.assertIs(self.middleware(make_request()), self.response)


class PresenceCountViewTests(SimpleTestCase):
    def setUp(self):
        self.factory = RequestFactory()

    @patch("openedx_fpt_presence.views.get_presence_service")
    def test_returns_online_count(self, get_service):
        get_service.return_value.count_safely.return_value = 1284
        response = count_view(self.factory.get("/api/fpt-presence/v1/count"))
        self.assertEqual(response.status_code, 200)
        self.assertJSONEqual(response.content, {"online": 1284})

    @patch("openedx_fpt_presence.views.get_presence_service")
    def test_returns_null_when_redis_is_unavailable(self, get_service):
        get_service.return_value.count_safely.return_value = None
        response = count_view(self.factory.get("/api/fpt-presence/v1/count"))
        self.assertEqual(response.status_code, 200)
        self.assertJSONEqual(response.content, {"online": None})

    def test_rejects_non_get_requests(self):
        response = count_view(self.factory.post("/api/fpt-presence/v1/count"))
        self.assertEqual(response.status_code, 405)
