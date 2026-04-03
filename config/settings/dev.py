from .base import *  # noqa: F401, F403

DEBUG = True

ALLOWED_HOSTS = ["*"]

# Shorter cache for dev
CELERY_TASK_ALWAYS_EAGER = False  # set True to run tasks synchronously in tests

# Django debug toolbar (optional, uncomment when installed)
# INSTALLED_APPS += ["debug_toolbar"]
# MIDDLEWARE = ["debug_toolbar.middleware.DebugToolbarMiddleware"] + MIDDLEWARE
# INTERNAL_IPS = ["127.0.0.1"]

# Show emails in console
EMAIL_BACKEND = "django.core.mail.backends.console.EmailBackend"
