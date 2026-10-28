import os

from fakeredis import FakeConnection

# Tests must not depend on a real SECRET (CI forks have no repo secrets).
os.environ.setdefault("SECRET", "test-secret-key-not-for-production")

from .settings import *  # noqa: F403

CACHES = {
    "default": {
        "BACKEND": "django_redis.cache.RedisCache",
        "LOCATION": REDIS_URL,  # noqa: F405
        "TIMEOUT": 18000,  # 5 hours
        "OPTIONS": {
            "CONNECTION_POOL_KWARGS": {"connection_class": FakeConnection},
        },
    },
}

CELERY_TASK_ALWAYS_EAGER = True

TESTING = True

# Keep test output bounded: the console handler writes to the real stderr,
# bypassing unittest --buffer, so INFO logs flood parallel test runs.
LOGGING["handlers"]["console"]["level"] = "WARNING"  # noqa: F405
LOGGING["root"]["level"] = "WARNING"  # noqa: F405

# Steam API key for testing
STEAM_API_KEY = "test_steam_api_key"
