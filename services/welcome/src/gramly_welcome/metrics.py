from prometheus_client import Counter, Gauge, Histogram

OWNER_ACTIONS = Counter("welcome_owner_actions_total", "Owner action execution results", ["result"])
OWNER_REPLIES = Counter("welcome_owner_replies_total", "Durable owner screen delivery results", ["result"])
OWNER_HEARTBEAT = Gauge("welcome_owner_loop_heartbeat_seconds", "Last owner loop iteration", ["kind", "slot"])

WEBHOOK_REQUESTS = Counter(
    "welcome_webhook_requests_total", "Accepted Telegram webhook requests", ("source", "result")
)
WEBHOOK_LATENCY = Histogram(
    "welcome_webhook_duration_seconds",
    "Telegram webhook request latency",
    ("source",),
    buckets=(0.01, 0.025, 0.05, 0.1, 0.25, 0.5, 1, 2),
)
WORKER_EVENTS = Counter("welcome_worker_events_total", "Processed inbox events", ("result",))
WORKER_ACTIVE = Gauge("welcome_worker_active", "Active Gramly Welcome worker processes", ("kind",))
DELIVERY_INFLIGHT = Gauge(
    "welcome_delivery_inflight", "Concurrent delivery actions in this process", ("lanes",)
)
CIRCUIT_STATE = Gauge(
    "welcome_dependency_circuit_open", "Whether an external dependency circuit is open", ("dependency",)
)
TELEGRAM_CLIENT_CACHE = Counter(
    "welcome_telegram_client_cache_total", "Telegram client pool lookup results", ("result",)
)
DELIVERY_ATTEMPTS = Counter("welcome_delivery_attempts_total", "Telegram delivery attempts", ("result",))
DELIVERY_SCHEDULE_LAG = Histogram(
    "welcome_delivery_schedule_lag_seconds",
    "Delay between the planned and successful Telegram send of a delivery operation",
    buckets=(0.5, 1, 2, 5, 10, 15, 30, 60, 120, 240, 300, 600),
)
DELIVERY_FAILURE_REASONS = Counter(
    "welcome_delivery_failure_reasons_total", "Bounded delivery failure reasons", ("reason", "scope")
)
JOIN_REQUEST_GREETINGS = Counter(
    "welcome_join_request_greetings_total",
    "Join-request greeting flows by terminal result",
    ("result",),
)
JOIN_REQUEST_ACTIVATIONS = Counter(
    "welcome_join_request_activations_total",
    "Long join-request activation attempts by result",
    ("result",),
)
DEPENDENCY_ERRORS = Counter(
    "welcome_dependency_errors_total",
    "Unavailable external dependencies used by Welcome workers",
    ("dependency",),
)
QUEUE_DEPTH = Gauge("welcome_queue_depth", "Durable queue rows by state", ("queue", "status"))
OLDEST_PENDING_AGE = Gauge(
    "welcome_oldest_pending_age_seconds", "Age of the oldest actionable queue row", ("queue",)
)
VIEW_QUEUE_DEPTH = Gauge("welcome_view_queue_depth", "Pending GramlyView inbox updates")
VIEW_QUEUE_AGE = Gauge(
    "welcome_view_oldest_pending_age_seconds", "Age of the oldest pending GramlyView update"
)
VIEW_COLLECTION_FAILURES = Gauge(
    "welcome_view_collection_failures_last_hour", "Failed GramlyView channel collection runs in the last hour"
)
VIEW_LAST_SUCCESS_AGE = Gauge(
    "welcome_view_last_success_age_seconds", "Age of the latest successful GramlyView collection"
)
AD_DELIVERIES = Counter(
    "welcome_free_ad_deliveries_total",
    "Free plan advertising operations by terminal result",
    ("result",),
)
AD_CLICKS = Counter(
    "welcome_free_ad_clicks_total",
    "Tracked clicks on Free plan advertising calls to action",
)
ROTATION_RECOMMENDATIONS = Counter(
    "welcome_rotation_recommendations_total",
    "Rotation recommendations by terminal result",
    ("result",),
)
ROTATION_CONVERSIONS = Counter(
    "welcome_rotation_conversions_total",
    "Attributed subscriptions from Gramly rotation",
)
OWNER_NOTIFICATIONS = Counter(
    "welcome_owner_notifications_total",
    "Owner onboarding and announcement delivery attempts",
    ("kind", "result"),
)
PAYMENT_EVENTS = Counter(
    "welcome_payment_events_total",
    "Verified payment webhook events by provider and result",
    ("provider", "result"),
)
BILLING_OPERATIONS = Counter(
    "welcome_billing_operations_total",
    "Subscription reminders and partner payouts by operation and result",
    ("operation", "result"),
)
PREMIUM_MEDIA = Counter(
    "welcome_premium_media_total",
    "Telegram-native premium media resolution attempts",
    ("kind", "result"),
)
CUSTOM_EMOJI_PRESERVED = Counter(
    "welcome_custom_emoji_preserved_total",
    "Telegram messages that retained every requested custom emoji entity",
)
CUSTOM_EMOJI_DOWNGRADED = Counter(
    "welcome_custom_emoji_downgraded_total",
    "Telegram messages where requested custom emoji entities were removed",
)
CUSTOM_EMOJI_TELEGRAM_ERROR = Counter(
    "welcome_custom_emoji_telegram_error_total",
    "Telegram API errors while sending messages containing custom emoji",
)
TEMPLATE_INSTALLS = Counter(
    "welcome_template_installs_total",
    "Template installation requests by result",
    ("result",),
)
AUDIENCE_CONSENTS = Counter(
    "welcome_audience_consents_total",
    "Audience captcha and consent outcomes",
    ("result",),
)
BROADCAST_RECIPIENTS = Counter(
    "welcome_broadcast_recipients_total",
    "Broadcast recipient terminal outcomes",
    ("result",),
)
