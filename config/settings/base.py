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
    "apps.clients",
    "apps.stats",
    "apps.broadcasts",
    "apps.referrals",
    "apps.withdrawals",
    "apps.telegram_bot",
    "apps.crm",
    "apps.docs",
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
CELERY_TIMEZONE = "Europe/Moscow"
CELERY_TASK_TRACK_STARTED = True
CELERY_BROKER_CONNECTION_RETRY_ON_STARTUP = True
CELERY_TASK_ROUTES = {
    "apps.broadcasts.tasks.*": {"queue": "broadcasts"},
    "apps.stats.tasks.*": {"queue": "default"},
    "apps.crm.tasks.*": {"queue": "default"},
}

# ── Celery Beat Schedule ──────────────────────────────────────────────────────
from celery.schedules import crontab  # noqa: E402

CELERY_BEAT_SCHEDULE = {
    # CRM: check deadline at 00:05 МСК (5 min after midnight)
    "crm-check-deadline": {
        "task": "apps.crm.tasks.crm_check_deadline_task",
        "schedule": crontab(hour=0, minute=5),
    },
    # CRM: weekly report every Monday at 08:00 МСК
    "crm-weekly-report": {
        "task": "apps.crm.tasks.crm_weekly_report_task",
        "schedule": crontab(hour=8, minute=0, day_of_week="monday"),
    },
    # Daily at 09:00 МСК: unassign workers with no activity for 3+ days
    "check-worker-inactivity": {
        "task": "apps.clients.tasks.check_worker_inactivity_task",
        "schedule": crontab(hour=9, minute=0),
    },
    # Removed: admin-reminder-1300, admin-reminder-2000, check-missing-daily-report
    # These depended on DailyReport/MissedDay (legacy system, replaced by client-link model).
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

# Shared Google Sheets link shown to workers and curators as "База каналов"
CHANNELS_DB_URL = env(
    "CHANNELS_DB_URL",
    default="https://docs.google.com/spreadsheets/d/1-3kKQZk3LrBy9XEL0lG8oM1dYdgrvWzEDKXgFt5udjE/edit?gid=0#gid=0",
)

# ── Channel subscription gate ─────────────────────────────────────────────────
# Username (with @) for public channels, or numeric ID for private channels.
# Public channel example:  @grmly
# Private channel example: -1001234567890
# Leave empty to disable the gate entirely.
SUBSCRIPTION_CHANNEL_ID = env("SUBSCRIPTION_CHANNEL_ID", default="@gramlyspam")

# Link shown to non-subscribed users in the inline button.
SUBSCRIPTION_CHANNEL_URL = env(
    "SUBSCRIPTION_CHANNEL_URL",
    default="https://t.me/gramlyspam",
)

# ── Static ────────────────────────────────────────────────────────────────────
STATIC_URL = "/static/"
STATIC_ROOT = BASE_DIR / "staticfiles"

# ── Media — S3-compatible storage (always active) ─────────────────────────────
# All CRM uploads go to S3-compatible storage.
# Dev:  local MinIO container (docker-compose.dev.yml, port 9000).
# Prod: Cloudflare R2 / AWS S3 / any S3-compatible endpoint.
# See config/storage_backends.py and .env.example for full setup docs.

# ── boto3 credentials + bucket ────────────────────────────────────────────────
AWS_ACCESS_KEY_ID       = env("AWS_ACCESS_KEY_ID")
AWS_SECRET_ACCESS_KEY   = env("AWS_SECRET_ACCESS_KEY")
AWS_STORAGE_BUCKET_NAME = env("AWS_STORAGE_BUCKET_NAME")

# Endpoint URL:
#   Dev  → http://minio:9000  (internal Docker DNS, resolved inside container)
#   Prod → https://<account>.r2.cloudflarestorage.com  (omit for AWS S3)
AWS_S3_ENDPOINT_URL = env("AWS_S3_ENDPOINT_URL", default=None)
AWS_S3_REGION_NAME  = env("AWS_S3_REGION_NAME", default="auto")

# Signed URLs:
#   Dev  → false  (MinIO bucket is public-read; no signing overhead)
#   Prod → true   (private bucket, time-limited signed URLs, 1 h TTL)
AWS_QUERYSTRING_AUTH   = env.bool("MEDIA_QUERYSTRING_AUTH", default=True)
AWS_QUERYSTRING_EXPIRE = env.int("MEDIA_QUERYSTRING_EXPIRE", default=3600)

# URL rewriting for dev: boto3 generates URLs with the internal Docker hostname
# (e.g. http://minio:9000/...) which is unreachable from the browser.
# MEDIA_S3_PUBLIC_URL replaces the internal host with a publicly accessible one.
# Dev:  http://localhost:9000
# Prod: leave empty — R2/S3 endpoint URLs are directly accessible.
MEDIA_S3_PUBLIC_URL = env("MEDIA_S3_PUBLIC_URL", default="")

# MEDIA_URL is used by Django admin file widgets as a prefix fallback.
# With S3 backend the actual URL always comes from storage.url() (full S3 URL).
# We leave this empty so Django admin doesn't prepend a local prefix to S3 URLs.
MEDIA_URL = ""

STORAGES = {
    "default": {
        "BACKEND": "config.storage_backends.MediaStorage",
    },
    "staticfiles": {
        "BACKEND": "whitenoise.storage.CompressedManifestStaticFilesStorage",
    },
}

# ── i18n ─────────────────────────────────────────────────────────────────────
LANGUAGE_CODE = "ru"
TIME_ZONE = "Europe/Moscow"
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
                "title": "Выводы",
                "separator": False,
                "items": [
                    {
                        "title": "Заявки на вывод",
                        "icon": "payments",
                        "link": "/django-admin/withdrawals/withdrawalrequest/",
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
                ],
            },
            {
                "title": "Статистика",
                "separator": False,
                "items": [
                    {
                        "title": "Дневные отчёты",
                        "icon": "today",
                        "link": "/django-admin/stats/dailyreport/",
                    },
                    {
                        "title": "Пропущенные дни",
                        "icon": "event_busy",
                        "link": "/django-admin/stats/missedday/",
                    },
                    {
                        "title": "Конфигурация ставок",
                        "icon": "tune",
                        "link": "/django-admin/stats/rateconfig/",
                    },
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
                "title": "CRM",
                "separator": True,
                "items": [
                    {
                        "title": "Пространства",
                        "icon": "workspaces",
                        "link": "/django-admin/crm/workspace/",
                    },
                    {
                        "title": "Участники",
                        "icon": "group",
                        "link": "/django-admin/crm/workspacemembership/",
                    },
                    {
                        "title": "Финансовые записи",
                        "icon": "paid",
                        "link": "/django-admin/crm/financeentry/",
                    },
                    {
                        "title": "Записи по заявкам",
                        "icon": "receipt_long",
                        "link": "/django-admin/crm/applicationentry/",
                    },
                    {
                        "title": "Сводные отчёты",
                        "icon": "summarize",
                        "link": "/django-admin/crm/dailysummaryreport/",
                    },
                    {
                        "title": "Пропуски дедлайна",
                        "icon": "alarm_off",
                        "link": "/django-admin/crm/deadlinemiss/",
                    },
                    {
                        "title": "Недельные планы",
                        "icon": "event_note",
                        "link": "/django-admin/crm/weeklyplan/",
                    },
                ],
            },
            {
                "title": "Документация",
                "separator": True,
                "items": [
                    {
                        "title": "📖 Инструкции для менеджеров",
                        "icon": "menu_book",
                        "link": "/docs/",
                    },
                ],
            },
            {
                "title": "Celery",
                "separator": False,
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
