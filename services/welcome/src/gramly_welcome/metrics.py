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
