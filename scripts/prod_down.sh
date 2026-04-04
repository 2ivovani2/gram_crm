#!/usr/bin/env bash
# Stop the production stack.
# Removes the Telegram webhook before stopping containers
# so Telegram doesn't keep retrying a dead URL.
#
# Usage: bash scripts/prod_down.sh   (or: make prod-down)

set -Eeuo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
PROJECT_DIR="$(cd "$SCRIPT_DIR/.." && pwd)"
cd "$PROJECT_DIR"

COMPOSE="docker compose -f docker-compose.yml"

log()  { echo "==> $*"; }
ok()   { echo "    [OK]   $*"; }
skip() { echo "    [SKIP] $*"; }

# ── 1. Remove webhook (best-effort) ───────────────────────────────────────────
log "Removing Telegram webhook from prod bot ..."
if $COMPOSE exec -T web python manage.py setup_webhook --delete 2>/dev/null; then
    ok "Webhook removed."
else
    skip "Web container is not running — webhook not removed."
    skip "Telegram will retry until the URL expires; safe to ignore."
fi

# ── 2. Stop containers ────────────────────────────────────────────────────────
log "Stopping all prod services ..."
$COMPOSE down

echo ""
echo "==> Production stack stopped."
