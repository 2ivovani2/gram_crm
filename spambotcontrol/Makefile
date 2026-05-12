.PHONY: dev dev-down prod prod-down prod-renew-cert logs logs-prod minio-console webhook-info webhook-info-prod crm-setup crm-setup-prod help

COMPOSE_DEV = docker compose \
	-f docker-compose.yml \
	-f docker-compose.dev.yml \
	-f docker-compose.ngrok.yml

COMPOSE_PROD = docker compose \
	-f docker-compose.yml

# ── Dev targets ───────────────────────────────────────────────────────────────

## Start local dev stack (webhook-only, test bot, ngrok tunnel)
dev:
	@bash scripts/dev_up.sh

## Stop local dev stack and remove test bot webhook
dev-down:
	@bash scripts/dev_down.sh

## Follow logs of web + celery_worker (dev)
logs:
	$(COMPOSE_DEV) logs -f web celery_worker

## Open MinIO console in browser (dev only — local S3 UI at localhost:9001)
minio-console:
	@echo "MinIO Console: http://localhost:9001"
	@open http://localhost:9001 2>/dev/null || xdg-open http://localhost:9001 2>/dev/null || true

# ── Prod targets ──────────────────────────────────────────────────────────────

## Start production stack (Let's Encrypt SSL, registers webhook)
prod:
	@bash scripts/prod_up.sh

## Stop production stack and remove prod bot webhook
prod-down:
	@bash scripts/prod_down.sh

## Follow logs of web + celery_worker (prod)
logs-prod:
	$(COMPOSE_PROD) logs -f web celery_worker

## Force Let's Encrypt certificate renewal
prod-renew-cert:
	$(COMPOSE_PROD) exec certbot certbot renew --force-renewal

# ── Shared targets ────────────────────────────────────────────────────────────

## Show current Telegram webhook info (dev bot)
webhook-info:
	$(COMPOSE_DEV) exec web python manage.py setup_webhook --info

## Show current Telegram webhook info (prod bot)
webhook-info-prod:
	$(COMPOSE_PROD) exec web python manage.py setup_webhook --info

## Setup CRM workspace for dev. Add owner: make crm-setup OWNER=<telegram_id>
crm-setup:
	$(COMPOSE_DEV) exec web python manage.py setup_crm $(if $(OWNER),--add-owner $(OWNER),)

## Setup CRM workspace for prod. Add owner: make crm-setup-prod OWNER=<telegram_id>
crm-setup-prod:
	$(COMPOSE_PROD) exec web python manage.py setup_crm $(if $(OWNER),--add-owner $(OWNER),)

## Show available targets
help:
	@grep -E '^##' Makefile | sed 's/^## //'
