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
