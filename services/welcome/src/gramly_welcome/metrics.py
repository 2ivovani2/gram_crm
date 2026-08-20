from prometheus_client import Counter, Gauge, Histogram

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
DELIVERY_ATTEMPTS = Counter("welcome_delivery_attempts_total", "Telegram delivery attempts", ("result",))
JOIN_REQUEST_GREETINGS = Counter(
    "welcome_join_request_greetings_total",
    "Join-request greeting flows by terminal result",
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
