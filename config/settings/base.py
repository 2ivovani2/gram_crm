"""
Base settings — shared between dev and prod.
Never import this module directly; use dev.py or prod.py.
"""
from pathlib import Path
import environ

BASE_DIR = Path(__file__).resolve().parent.parent.parent

env = environ.Env()
environ.Env.read_env(BASE_DIR / ".env")

SECRET_KEY = env("SECRET_KEY")
DEBUG = env.bool("DEBUG", default=False)
ALLOWED_HOSTS = env.list("ALLOWED_HOSTS", default=["*"])

# ── Apps ──────────────────────────────────────────────────────────────────────
DJANGO_APPS = [
    "unfold",
    "unfold.contrib.filters",
    "unfold.contrib.inlines",
    "django.contrib.admin",
    "django.contrib.auth",
    "django.contrib.contenttypes",
    "django.contrib.sessions",
    "django.contrib.messages",
    "django.contrib.staticfiles",
]

THIRD_PARTY_APPS = [
    "django_celery_results",
    "django_celery_beat",
]

LOCAL_APPS = [
    "apps.common",
    "apps.users",
    "apps.invites",
    "apps.stats",
    "apps.broadcasts",
    "apps.referrals",
    "apps.telegram_bot",
]

INSTALLED_APPS = DJANGO_APPS + THIRD_PARTY_APPS + LOCAL_APPS

# ── Middleware ────────────────────────────────────────────────────────────────
MIDDLEWARE = [
    "django.middleware.security.SecurityMiddleware",
    "whitenoise.middleware.WhiteNoiseMiddleware",  # must be directly after SecurityMiddleware
    "django.contrib.sessions.middleware.SessionMiddleware",
    "django.middleware.common.CommonMiddleware",
    "django.middleware.csrf.CsrfViewMiddleware",
    "django.contrib.auth.middleware.AuthenticationMiddleware",
    "django.contrib.messages.middleware.MessageMiddleware",
    "django.middleware.clickjacking.XFrameOptionsMiddleware",
]

ROOT_URLCONF = "config.urls"
WSGI_APPLICATION = "config.wsgi.application"
ASGI_APPLICATION = "config.asgi.application"

TEMPLATES = [
    {
        "BACKEND": "django.template.backends.django.DjangoTemplates",
        "DIRS": [BASE_DIR / "templates"],
        "APP_DIRS": True,
        "OPTIONS": {
            "context_processors": [
                "django.template.context_processors.debug",
                "django.template.context_processors.request",
                "django.contrib.auth.context_processors.auth",
                "django.contrib.messages.context_processors.messages",
            ],
        },
    },
]

# ── Database ──────────────────────────────────────────────────────────────────
DATABASES = {
    "default": env.db("DATABASE_URL"),
}
DEFAULT_AUTO_FIELD = "django.db.models.BigAutoField"

# ── Auth ──────────────────────────────────────────────────────────────────────
AUTH_USER_MODEL = "users.User"
AUTH_PASSWORD_VALIDATORS = [
    {"NAME": "django.contrib.auth.password_validation.UserAttributeSimilarityValidator"},
    {"NAME": "django.contrib.auth.password_validation.MinimumLengthValidator"},
    {"NAME": "django.contrib.auth.password_validation.CommonPasswordValidator"},
    {"NAME": "django.contrib.auth.password_validation.NumericPasswordValidator"},
]

# ── Cache / Redis ─────────────────────────────────────────────────────────────
REDIS_URL = env("REDIS_URL", default="redis://redis:6379/0")

CACHES = {
    "default": {
        "BACKEND": "django.core.cache.backends.redis.RedisCache",
        "LOCATION": REDIS_URL,
    }
}

# ── Celery ────────────────────────────────────────────────────────────────────
CELERY_BROKER_URL = REDIS_URL
CELERY_RESULT_BACKEND = "django-db"
CELERY_CACHE_BACKEND = "django-cache"
CELERY_ACCEPT_CONTENT = ["json"]
CELERY_TASK_SERIALIZER = "json"
CELERY_RESULT_SERIALIZER = "json"
CELERY_TIMEZONE = "UTC"
CELERY_TASK_TRACK_STARTED = True
CELERY_TASK_ROUTES = {
    "apps.broadcasts.tasks.*": {"queue": "broadcasts"},
}

# ── Telegram ──────────────────────────────────────────────────────────────────
# BOT_ENV controls which bot token is active:
#   "dev"  → TEST_BOT_TOKEN  (test bot, used locally with ngrok)
#   "prod" → PROD_BOT_TOKEN  (production bot, used on the live server)
BOT_ENV = env("BOT_ENV", default="dev")

if BOT_ENV == "prod":
    TELEGRAM_BOT_TOKEN = env("PROD_BOT_TOKEN")
else:
    TELEGRAM_BOT_TOKEN = env("TEST_BOT_TOKEN")

# Used to construct referral deep-links: https://t.me/{TELEGRAM_BOT_USERNAME}?start=ref_{token}
TELEGRAM_BOT_USERNAME = env("TELEGRAM_BOT_USERNAME", default="")

# Secret token Telegram sends in X-Telegram-Bot-Api-Secret-Token header.
# Must be identical on the bot API side (set via setup_webhook) and here.
TELEGRAM_WEBHOOK_SECRET = env("TELEGRAM_WEBHOOK_SECRET", default="")

# Full public HTTPS URL where Telegram delivers updates, including path.
# Dev:  https://<id>.ngrok-free.app/bot/webhook/
# Prod: https://yourdomain.com/bot/webhook/
TELEGRAM_WEBHOOK_URL = env("TELEGRAM_WEBHOOK_URL", default="")

# ── Static ────────────────────────────────────────────────────────────────────
STATIC_URL = "/static/"
STATIC_ROOT = BASE_DIR / "staticfiles"
STORAGES = {
    "staticfiles": {
        "BACKEND": "whitenoise.storage.CompressedManifestStaticFilesStorage",
    },
}

# ── i18n ─────────────────────────────────────────────────────────────────────
LANGUAGE_CODE = "ru"
TIME_ZONE = "UTC"
USE_I18N = True
USE_TZ = True

# ── Logging ───────────────────────────────────────────────────────────────────
LOGGING = {
    "version": 1,
    "disable_existing_loggers": False,
    "formatters": {
        "verbose": {
            "format": "[{levelname}] {asctime} {name}: {message}",
            "style": "{",
        },
    },
    "handlers": {
        "console": {
            "class": "logging.StreamHandler",
            "formatter": "verbose",
        },
    },
    "root": {"handlers": ["console"], "level": "INFO"},
    "loggers": {
        "django": {"handlers": ["console"], "level": "INFO", "propagate": False},
        "apps": {"handlers": ["console"], "level": "DEBUG", "propagate": False},
        "aiogram": {"handlers": ["console"], "level": "INFO", "propagate": False},
        "celery": {"handlers": ["console"], "level": "INFO", "propagate": False},
    },
}

# ── Django Unfold admin theme ─────────────────────────────────────────────────
UNFOLD = {
    "SITE_TITLE": "SpamBotControl",
    "SITE_HEADER": "SpamBotControl",
    "SITE_URL": "/",
    "SITE_SYMBOL": "robot",          # Material symbol shown in the sidebar header
    "SHOW_HISTORY": True,
    "SHOW_VIEW_ON_SITE": False,
    "THEME": "dark",                 # "dark" | "light" — default to dark
    "COLORS": {
        "primary": {
            "50":  "240 249 255",    # indigo/blue palette
            "100": "224 242 254",
            "200": "186 230 253",
            "300": "125 211 252",
            "400": "56  189 248",
            "500": "14  165 233",
            "600": "2   132 199",
            "700": "3   105 161",
            "800": "7   89  133",
            "900": "12  74  110",
            "950": "8   47  73",
        },
    },
    "SIDEBAR": {
        "show_search": True,
        "show_all_applications": False,
        "navigation": [
            {
                "title": "Пользователи",
                "separator": False,
                "items": [
                    {
                        "title": "Все пользователи",
                        "icon": "group",
                        "link": "/django-admin/users/user/",
                    },
                ],
            },
            {
                "title": "Инвайты",
                "separator": False,
                "items": [
                    {
                        "title": "Инвайт-ключи",
                        "icon": "key",
                        "link": "/django-admin/invites/invitekey/",
                    },
                    {
                        "title": "Активации инвайтов",
                        "icon": "check_circle",
                        "link": "/django-admin/invites/inviteactivation/",
                    },
                ],
            },
            {
                "title": "Рассылки",
                "separator": False,
                "items": [
                    {
                        "title": "Рассылки",
                        "icon": "campaign",
                        "link": "/django-admin/broadcasts/broadcast/",
                    },
                    {
                        "title": "Логи доставки",
                        "icon": "receipt_long",
                        "link": "/django-admin/broadcasts/broadcastdeliverylog/",
                    },
                ],
            },
            {
                "title": "Рефералы",
                "separator": False,
                "items": [
                    {
                        "title": "Реферальные ссылки",
                        "icon": "link",
                        "link": "/django-admin/referrals/referrallink/",
                    },
                    {
                        "title": "Настройки программы",
                        "icon": "settings",
                        "link": "/django-admin/referrals/referralsettings/",
                    },
                ],
            },
            {
                "title": "Статистика",
                "separator": False,
                "items": [
                    {
                        "title": "По пользователям",
                        "icon": "bar_chart",
                        "link": "/django-admin/stats/userdailystats/",
                    },
                    {
                        "title": "Системная",
                        "icon": "monitoring",
                        "link": "/django-admin/stats/systemstats/",
                    },
                ],
            },
            {
                "title": "Celery",
                "separator": True,
                "items": [
                    {
                        "title": "Результаты задач",
                        "icon": "task_alt",
                        "link": "/django-admin/django_celery_results/taskresult/",
                    },
                    {
                        "title": "Периодические задачи",
                        "icon": "schedule",
                        "link": "/django-admin/django_celery_beat/periodictask/",
                    },
                ],
            },
        ],
    },
}
