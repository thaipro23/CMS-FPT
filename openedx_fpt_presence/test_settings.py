from types import SimpleNamespace
from unittest import TestCase

from openedx_fpt_presence.settings.common import discover_redis_url, plugin_settings


class PresencePluginSettingsTests(TestCase):
    def test_registers_middleware_without_freezing_redis_url_during_settings_import(self):
        settings = SimpleNamespace(
            MIDDLEWARE=["django.middleware.common.CommonMiddleware"],
            CACHES={
                "default": {
                    "BACKEND": "django.core.cache.backends.redis.RedisCache",
                    "LOCATION": "redis://:secret@10.205.222.150:6379/1",
                }
            },
        )

        plugin_settings(settings)

        self.assertIn(
            "openedx_fpt_presence.middleware.FPTPresenceMiddleware",
            settings.MIDDLEWARE,
        )
        self.assertFalse(hasattr(settings, "FPT_PRESENCE_REDIS_URL"))
        self.assertEqual(settings.FPT_PRESENCE_ACTIVE_WINDOW_SECONDS, 600)
        self.assertEqual(settings.FPT_PRESENCE_TOUCH_INTERVAL_SECONDS, 60)
        self.assertEqual(settings.FPT_PRESENCE_COUNT_CACHE_SECONDS, 30)

    def test_discovers_default_redis_cache_url_from_final_settings(self):
        settings = SimpleNamespace(
            CACHES={
                "default": {
                    "BACKEND": "django.core.cache.backends.redis.RedisCache",
                    "LOCATION": "redis://:secret@10.205.222.150:6379/1",
                }
            },
        )

        self.assertEqual(
            discover_redis_url(settings),
            "redis://:secret@10.205.222.150:6379/1",
        )

    def test_discovers_celery_redis_url_when_cache_is_not_redis(self):
        settings = SimpleNamespace(
            CACHES={"default": {"LOCATION": "memcached://cache:11211"}},
            CELERY_BROKER_URL="redis://:pw@redis.example.internal:6379/0",
        )

        self.assertEqual(
            discover_redis_url(settings),
            "redis://:pw@redis.example.internal:6379/0",
        )

    def test_explicit_presence_redis_url_wins(self):
        settings = SimpleNamespace(
            CACHES={
                "default": {
                    "LOCATION": "redis://cache.example.internal:6379/1",
                }
            },
            FPT_PRESENCE_REDIS_URL="redis://presence.example.internal:6379/4",
        )

        self.assertEqual(
            discover_redis_url(settings),
            "redis://presence.example.internal:6379/4",
        )

    def test_legacy_presence_redis_dict_does_not_override_live_openedx_redis(self):
        settings = SimpleNamespace(
            CACHES={
                "default": {
                    "LOCATION": "redis://live.example.internal:6379/1",
                }
            },
            FPT_PRESENCE_REDIS={
                "HOST": "redis",
                "PORT": 6379,
                "DB": 0,
            },
        )

        self.assertEqual(
            discover_redis_url(settings),
            "redis://live.example.internal:6379/1",
        )
