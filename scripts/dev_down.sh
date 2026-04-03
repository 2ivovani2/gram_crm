#!/usr/bin/env bash
# Stop the local dev stack.
# Attempts to delete the test bot webhook before stopping containers
# so Telegram doesn't keep retrying a dead ngrok URL.
#
# Usage: bash scripts/dev_down.sh   (or: make dev-down)

set -Eeuo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
PROJECT_DIR="$(cd "$SCRIPT_DIR/.." && pwd)"
cd "$PROJECT_DIR"

COMPOSE="docker-compose -f docker-compose.yml -f docker-compose.dev.yml -f docker-compose.ngrok.yml"

log()  { echo "==> $*"; }
ok()   { echo "    [OK]   $*"; }
skip() { echo "    [SKIP] $*"; }

# ── 1. Remove webhook (best-effort: skip if web container is not running) ─────
log "Removing Telegram webhook from test bot ..."
if $COMPOSE exec -T web python manage.py setup_webhook --delete 2>/dev/null; then
    ok "Webhook removed."
else
    skip "Web container is not running — webhook not removed."
    skip "Telegram will retry until the URL expires; it is safe to ignore."
fi

# ── 2. Stop and remove containers ─────────────────────────────────────────────
log "Stopping all dev services ..."
$COMPOSE down

echo ""
echo "==> Dev stack stopped."
