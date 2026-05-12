# ─────────────────────────────────────────────────────────────────────────────
# Gramly — root Makefile
# All commands operate on the unified compose stack at the project root.
#
# Dev prerequisites:
#   cp .env.example .env  → fill BOT_ENV=dev, TEST_BOT_TOKEN, SECRET_KEY, NGROK_*, AWS_*, POSTGRES_PASSWORD, SPAM_JWT_SECRET_KEY
#
# Prod prerequisites (on VPS):
#   apt install -y docker.io docker-compose-plugin curl git
#   cp .env.example .env  → fill BOT_ENV=prod, PROD_BOT_TOKEN, SECRET_KEY, DOMAIN, CERTBOT_EMAIL, POSTGRES_PASSWORD, AWS_*
# ─────────────────────────────────────────────────────────────────────────────

COMPOSE     = docker compose -f docker-compose.yml
COMPOSE_DEV = docker compose -f docker-compose.yml -f docker-compose.dev.yml -f docker-compose.ngrok.yml
DOMAIN     ?= gramly.tech

.PHONY: dev dev-down \
        prod prod-down prod-build \
        logs logs-web logs-spam \
        cert-renew webhook-set webhook-info webhook-del \
        crm-setup ps shell-web shell-spam

# ── Dev (local, one command) ──────────────────────────────────────────────────

dev:
	@bash scripts/dev_up.sh

dev-down:
	@bash scripts/dev_down.sh

# ── Production (VPS, one command) ────────────────────────────────────────────

prod:
	@bash scripts/prod_up.sh

prod-down:
	@bash scripts/prod_down.sh

prod-build:
	$(COMPOSE) build --pull

# ── Let's Encrypt renewal ─────────────────────────────────────────────────────

cert-renew:
	$(COMPOSE) exec certbot certbot renew --quiet

# ── Logs ──────────────────────────────────────────────────────────────────────

logs:
	$(COMPOSE) logs -f web celery_worker

logs-web:
	$(COMPOSE) logs -f web

logs-spam:
	$(COMPOSE) logs -f spam-backend spam-frontend

# ── Webhook management ────────────────────────────────────────────────────────

webhook-set:
	$(COMPOSE) exec web python manage.py setup_webhook \
		--url https://$(DOMAIN)/bot/webhook/

webhook-info:
	$(COMPOSE) exec web python manage.py setup_webhook --info

webhook-del:
	$(COMPOSE) exec web python manage.py setup_webhook --delete

# ── CRM first-run setup ───────────────────────────────────────────────────────

crm-setup:
	@[ "$(OWNER)" ] || (echo "Usage: make crm-setup OWNER=<telegram_id>"; exit 1)
	$(COMPOSE) exec web python manage.py setup_crm --owner $(OWNER)

# ── Utility ───────────────────────────────────────────────────────────────────

ps:
	$(COMPOSE) ps

shell-web:
	$(COMPOSE) exec web bash

shell-spam:
	$(COMPOSE) exec spam-backend bash
