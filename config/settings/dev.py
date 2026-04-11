import environ

from .base import *  # noqa: F401, F403

_env = environ.Env()

DEBUG = True

# ngrok domain (static): set NGROK_DOMAIN in .env, e.g. chigger-robust-unicorn.ngrok-free.app
_ngrok_domain = _env("NGROK_DOMAIN", default="")

ALLOWED_HOSTS = ["localhost", "127.0.0.1", ".ngrok-free.app", ".ngrok.io"]

# CSRF: Django 4.x checks Origin header against this list for HTTPS POST requests.
# Must include the full scheme+domain for every trusted origin.
CSRF_TRUSTED_ORIGINS = [
    "http://localhost:8000",
    "http://127.0.0.1:8000",
]
if _ngrok_domain:
    CSRF_TRUSTED_ORIGINS.append(f"https://{_ngrok_domain}")

# ngrok terminates TLS and forwards HTTP internally.
# Without this, request.build_absolute_uri() returns http:// instead of https://
# which breaks Telegram Login Widget (data-auth-url must be absolute HTTPS).
SECURE_PROXY_SSL_HEADER = ("HTTP_X_FORWARDED_PROTO", "https")

# Telegram Login Widget uses window.opener.postMessage() from oauth.telegram.org
# back to our login page. Django's default COOP: same-origin severs the
# window.opener reference for cross-origin popups → widget silently fails.
# Setting to None removes the header so the popup can communicate back.
SECURE_CROSS_ORIGIN_OPENER_POLICY = None

# Shorter cache for dev
CELERY_TASK_ALWAYS_EAGER = False  # set True to run tasks synchronously in tests

# Django debug toolbar (optional, uncomment when installed)
# INSTALLED_APPS += ["debug_toolbar"]
# MIDDLEWARE = ["debug_toolbar.middleware.DebugToolbarMiddleware"] + MIDDLEWARE
# INTERNAL_IPS = ["127.0.0.1"]

# Show emails in console
EMAIL_BACKEND = "django.core.mail.backends.console.EmailBackend"
