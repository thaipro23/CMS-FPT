from types import SimpleNamespace
from unittest import TestCase

from openedx_fpt_presence.settings.common import plugin_settings


class PresencePluginSettingsTests(TestCase):
    def test_registers_middleware_and_reuses_default_redis_cache_url(self):
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
        self.assertEqual(
            settings.FPT_PRESENCE_REDIS_URL,
            "redis://:secret@10.205.222.150:6379/1",
        )
        self.assertEqual(settings.FPT_PRESENCE_ACTIVE_WINDOW_SECONDS, 600)
        self.assertEqual(settings.FPT_PRESENCE_TOUCH_INTERVAL_SECONDS, 60)
        self.assertEqual(settings.FPT_PRESENCE_COUNT_CACHE_SECONDS, 30)

    def test_falls_back_to_celery_redis_url_when_cache_is_not_redis(self):
        settings = SimpleNamespace(
            MIDDLEWARE=[],
            CACHES={"default": {"LOCATION": "memcached://cache:11211"}},
            CELERY_BROKER_URL="redis://:pw@redis.example.internal:6379/0",
        )

        plugin_settings(settings)

        self.assertEqual(
            settings.FPT_PRESENCE_REDIS_URL,
            "redis://:pw@redis.example.internal:6379/0",
        )

    def test_does_not_override_explicit_presence_redis_configuration(self):
        settings = SimpleNamespace(
            MIDDLEWARE=[],
            CACHES={
                "default": {
                    "LOCATION": "redis://cache.example.internal:6379/1",
                }
            },
            FPT_PRESENCE_REDIS_URL="redis://presence.example.internal:6379/4",
            FPT_PRESENCE_ACTIVE_WINDOW_SECONDS=900,
        )

        plugin_settings(settings)

        self.assertEqual(
            settings.FPT_PRESENCE_REDIS_URL,
            "redis://presence.example.internal:6379/4",
        )
        self.assertEqual(settings.FPT_PRESENCE_ACTIVE_WINDOW_SECONDS, 900)
