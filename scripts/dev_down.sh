#!/usr/bin/env bash
# Stop the unified Gramly dev stack and remove the Telegram webhook.
# Usage: make dev-down

set -Eeuo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
PROJECT_DIR="$(cd "$SCRIPT_DIR/.." && pwd)"
cd "$PROJECT_DIR"

COMPOSE="docker compose -f docker-compose.yml -f docker-compose.dev.yml -f docker-compose.ngrok.yml"

log() { echo "==> $*"; }
ok()  { echo "    [OK]   $*"; }

log "Removing Telegram webhook..."
$COMPOSE exec -T web python manage.py setup_webhook --delete 2>/dev/null \
    && ok "Webhook deleted." \
    || ok "Webhook was not set or web container is not running."

log "Stopping services..."
$COMPOSE down

ok "Dev stack stopped."
