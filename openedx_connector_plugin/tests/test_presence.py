from __future__ import annotations

import importlib.util
from pathlib import Path
import unittest


PLUGIN = Path(__file__).resolve().parents[1] / 'openedx_ai_connector'
CORE_MODULE = PLUGIN / 'presence_core.py'


class FakeCache:
    def __init__(self):
        self.values = {}
        self.add_calls = []
        self.set_calls = []

    def add(self, key, value, timeout=None):
        self.add_calls.append((key, value, timeout))
        if key in self.values:
            return False
        self.values[key] = value
        return True

    def get(self, key):
        return self.values.get(key)

    def set(self, key, value, timeout=None):
        self.set_calls.append((key, value, timeout))
        self.values[key] = value


class FakeRedis:
    def __init__(self):
        self.scores = {}
        self.zadd_calls = []
        self.zcount_calls = []

    def zadd(self, key, mapping):
        self.zadd_calls.append((key, mapping))
        self.scores.update(mapping)

    def zcount(self, key, minimum, maximum):
        self.zcount_calls.append((key, minimum, maximum))
        upper = float('inf') if maximum == '+inf' else float(maximum)
        return sum(1 for score in self.scores.values() if float(minimum) <= score <= upper)


class PresenceCoreTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.assert_module_exists = CORE_MODULE.exists()
        if not cls.assert_module_exists:
            raise AssertionError('Missing presence_core.py implementation')
        spec = importlib.util.spec_from_file_location('presence_core_under_test', CORE_MODULE)
        cls.module = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(cls.module)

    def test_meaningful_paths_are_tracked_but_mechanical_paths_are_not(self):
        for path in (
            '/courses/course-v1:FPL+COM109+FA26/course/',
            '/api/course_home/outline/course-v1:FPL+COM109+FA26',
            '/api/contentstore/v1/container/block-v1:test/children',
        ):
            with self.subTest(path=path):
                self.assertTrue(self.module.should_track_path(path))

        for path in (
            '/api/presence/v1/count',
            '/api/presence/v1/count/',
            '/health',
            '/health/',
            '/favicon.ico',
            '/static/app.js',
            '/csrf/api/v1/token',
            '/login_refresh',
        ):
            with self.subTest(path=path):
                self.assertFalse(self.module.should_track_path(path))

    def test_touch_is_throttled_to_one_redis_write_per_user_per_minute(self):
        cache = FakeCache()
        redis = FakeRedis()

        self.assertTrue(self.module.touch_user(20618, cache, redis, now=1_000))
        self.assertFalse(self.module.touch_user(20618, cache, redis, now=1_010))

        self.assertEqual(len(redis.zadd_calls), 1)
        key, mapping = redis.zadd_calls[0]
        self.assertEqual(key, self.module.PRESENCE_ZSET_KEY)
        self.assertEqual(mapping, {'20618': 1_000})
        self.assertEqual(cache.add_calls[0][2], 60)

    def test_active_count_uses_ten_minute_window_and_thirty_second_cache(self):
        cache = FakeCache()
        redis = FakeRedis()
        redis.scores = {
            'old': 399,
            'edge': 400,
            'recent': 999,
            'now': 1_000,
        }

        count = self.module.get_active_count(cache, redis, now=1_000)
        self.assertEqual(count, 3)
        self.assertEqual(redis.zcount_calls, [(self.module.PRESENCE_ZSET_KEY, 400, '+inf')])
        self.assertEqual(cache.set_calls[-1][2], 30)

        redis.scores['new-user'] = 1_000
        self.assertEqual(self.module.get_active_count(cache, redis, now=1_005), 3)
        self.assertEqual(len(redis.zcount_calls), 1)

    def test_invalid_user_id_is_ignored(self):
        cache = FakeCache()
        redis = FakeRedis()
        self.assertFalse(self.module.touch_user(None, cache, redis, now=1_000))
        self.assertFalse(self.module.touch_user(0, cache, redis, now=1_000))
        self.assertEqual(redis.zadd_calls, [])

    def test_plugin_wiring_mounts_presence_url_and_middleware(self):
        apps_source = (PLUGIN / 'apps.py').read_text(encoding='utf-8')
        urls_source = (PLUGIN / 'urls.py').read_text(encoding='utf-8')
        settings_source = (PLUGIN / 'settings' / 'common.py').read_text(encoding='utf-8')

        self.assertIn('presence/v1/', apps_source)
        self.assertIn('presence_count', urls_source)
        self.assertIn('PresenceMiddleware', settings_source)
        self.assertIn('AuthenticationMiddleware', settings_source)


if __name__ == '__main__':
    unittest.main()
