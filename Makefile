.PHONY: dev dev-down prod prod-down logs logs-prod webhook-info help

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

# ── Prod targets ──────────────────────────────────────────────────────────────

## Start production stack on VPS (generates SSL cert, registers webhook)
prod:
	@bash scripts/prod_up.sh

## Stop production stack and remove prod bot webhook
prod-down:
	@bash scripts/prod_down.sh

## Follow logs of web + celery_worker (prod)
logs-prod:
	$(COMPOSE_PROD) logs -f web celery_worker

# ── Shared targets ────────────────────────────────────────────────────────────

## Show current Telegram webhook info for the active bot
webhook-info:
	$(COMPOSE_DEV) exec web python manage.py setup_webhook --info

## Show available targets
help:
	@grep -E '^##' Makefile | sed 's/^## //'
