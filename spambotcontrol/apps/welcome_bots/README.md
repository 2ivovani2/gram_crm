# Gramly Welcome

Multi-tenant Telegram product for managing customer-owned welcome bots. It is
hosted by the existing Django/Celery stack but is isolated from CRM models and
from the CRM Telegram dispatcher.

## Telegram constraints

- A bot cannot initiate a private conversation. A channel subscriber must have
  started the customer bot, or Telegram will reject the welcome delivery.
- Bot API exposes new join requests but cannot list requests that existed before
  this system received them. "Accept accumulated" therefore means requests
  already persisted by Gramly Welcome.
- Telegram `file_id` values are bot-specific. Media received by the interface bot
  is downloaded to S3/MinIO, then uploaded by the customer bot on delivery.
- Gender is not present in Bot API user data. Contacts default to the explicit
  `unknown`/"Трансформеры" bucket; the schema supports a future opt-in classifier.

## Webhooks

- Interface: `/welcome/webhook/`, protected by `WELCOME_WEBHOOK_SECRET`.
- Customer bots: `/welcome/client/<public UUID>/<path secret>/`, protected both
  by the unguessable path and a different per-bot Telegram webhook header.
- Customer tokens are encrypted at rest and never returned by the UI/admin.

Configure after deployment:

```bash
docker compose exec web python manage.py setup_welcome_webhooks --customers
```

The interface is disabled (HTTP 404) while `WELCOME_BOT_TOKEN` is empty, so the
CRM can be deployed before the production Telegram bot is created.
