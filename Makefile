.PHONY: dev dev-down logs webhook-info help

COMPOSE_DEV = docker-compose \
	-f docker-compose.yml \
	-f docker-compose.dev.yml \
	-f docker-compose.ngrok.yml

# ── Primary targets ───────────────────────────────────────────────────────────

## Start local dev stack (webhook-only, test bot, ngrok tunnel)
dev:
	@bash scripts/dev_up.sh

## Stop local dev stack and remove test bot webhook
dev-down:
	@bash scripts/dev_down.sh

## Follow logs of web + celery_worker
logs:
	$(COMPOSE_DEV) logs -f web celery_worker

## Show current Telegram webhook info for the active bot
webhook-info:
	$(COMPOSE_DEV) exec web python manage.py setup_webhook --info

## Show available targets
help:
	@grep -E '^##' Makefile | sed 's/^## //'
