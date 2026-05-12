import environ
from .base import *  # noqa: F401, F403

_env = environ.Env()

DEBUG = False

# ── Hosts & CSRF ──────────────────────────────────────────────────────────────
# ALLOWED_HOSTS is read from .env (base.py), but ensure gramly.tech is always trusted.
# Add gramly.tech to ALLOWED_HOSTS in .env: ALLOWED_HOSTS=gramly.tech,www.gramly.tech
_domain = _env("DOMAIN", default="gramly.tech")

CSRF_TRUSTED_ORIGINS = [
    f"https://{_domain}",
    f"https://www.{_domain}",
    f"https://crm.{_domain}",
]

# Share session cookie across all subdomains so login on gramly.tech
# carries over to crm.gramly.tech and spam.gramly.tech
SESSION_COOKIE_DOMAIN = f".{_domain}"
CSRF_COOKIE_DOMAIN    = f".{_domain}"

# Bot-facing URLs for CRM and docs links
CRM_URL  = f"https://crm.{_domain}/crm/login/"
DOCS_URL = f"https://{_domain}/docs/"

# Spam app relay: internal Docker hostname for backend, public URL for redirect
SPAM_BACKEND_INTERNAL_URL = "http://spam-backend:8000"
SPAM_APP_URL              = f"https://spam.{_domain}"

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

# ── Proxy-related (behind nginx) ──────────────────────────────────────────────
USE_X_FORWARDED_HOST = True
SECURE_PROXY_SSL_HEADER = ("HTTP_X_FORWARDED_PROTO", "https")

# Only allow webhook from Telegram IP ranges (optional hardening)
# TELEGRAM_ALLOWED_IPS = ["149.154.160.0/20", "91.108.4.0/22"]
