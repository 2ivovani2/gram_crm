import environ
from .base import *  # noqa: F401, F403

_env = environ.Env()

DEBUG = False

# ── Hosts & CSRF ──────────────────────────────────────────────────────────────
# ALLOWED_HOSTS is read from the runtime Secret. Keep the canonical Django
# entrypoints trusted as a fail-safe: operational scripts may extend the list,
# but must not be able to accidentally disable the Telegram webhook.
_domain = _env("DOMAIN", default="gramly.tech")
ALLOWED_HOSTS = list(
    dict.fromkeys(
        [
            *ALLOWED_HOSTS,  # noqa: F405
            _domain,
            f"www.{_domain}",
            f"crm.{_domain}",
            f"bot.{_domain}",
        ]
    )
)

CSRF_TRUSTED_ORIGINS = [
    f"https://{_domain}",
    f"https://www.{_domain}",
    f"https://crm.{_domain}",
]

# Keep authenticated CRM sessions host-only. Public landing/webhook hosts must
# never receive the CRM session or CSRF cookies.
SESSION_COOKIE_DOMAIN = None
CSRF_COOKIE_DOMAIN = None

# Bot-facing URLs for CRM and docs links
CRM_URL  = f"https://crm.{_domain}/crm/login/"
DOCS_URL = f"https://docs.{_domain}/"

# Telegram Login Widget (CRM): same COOP fix as dev — popup must postMessage back.
# Only needed if COOP is being set; Django's default is same-origin which breaks it.
SECURE_CROSS_ORIGIN_OPENER_POLICY = None

# ── Security headers ──────────────────────────────────────────────────────────
SECURE_BROWSER_XSS_FILTER = True
SECURE_CONTENT_TYPE_NOSNIFF = True
X_FRAME_OPTIONS = "DENY"
# HSTS: enable after confirming HTTPS works. nginx also sends this header.
SECURE_HSTS_SECONDS = 31536000
SECURE_HSTS_INCLUDE_SUBDOMAINS = True
SECURE_HSTS_PRELOAD = True
SECURE_SSL_REDIRECT = True
SESSION_COOKIE_SECURE = True
CSRF_COOKIE_SECURE = True

# ── Proxy-related (behind nginx) ──────────────────────────────────────────────
USE_X_FORWARDED_HOST = True
SECURE_PROXY_SSL_HEADER = ("HTTP_X_FORWARDED_PROTO", "https")

# Only allow webhook from Telegram IP ranges (optional hardening)
# TELEGRAM_ALLOWED_IPS = ["149.154.160.0/20", "91.108.4.0/22"]
