#!/usr/bin/env bash
# One-command local dev startup:
#   1. Start postgres, redis, ngrok, web, celery (nginx skipped in dev)
#   2. Read static ngrok domain from .env (NGROK_DOMAIN)
#   3. Wait for web service to be healthy (via Docker internal network)
#   4. Register Telegram webhook for test bot using the ngrok URL
#   5. Print final status
#
# All traffic in dev goes through ngrok — no localhost:8000 exposure.
# Access the app at https://<NGROK_DOMAIN>.
#
# Usage: bash scripts/dev_up.sh   (or: make dev)

set -Eeuo pipefail

# ── Config ────────────────────────────────────────────────────────────────────
SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
PROJECT_DIR="$(cd "$SCRIPT_DIR/.." && pwd)"
cd "$PROJECT_DIR"

COMPOSE="docker compose -f docker-compose.yml -f docker-compose.dev.yml -f docker-compose.ngrok.yml"
WEBHOOK_PATH="/bot/webhook/"
WEB_WAIT_SEC=90

# ── Helpers ───────────────────────────────────────────────────────────────────
log()  { echo "==> $*"; }
ok()   { echo "    [OK]   $*"; }
skip() { echo "    [SKIP] $*"; }
err()  { echo "    [ERR]  $*" >&2; }

# ── Guard: refuse to start dev stack if BOT_ENV=prod ─────────────────────────
BOT_ENV_VALUE=$(grep -m1 '^BOT_ENV=' .env 2>/dev/null | cut -d= -f2 | tr -d '[:space:]' || true)
if [ "$BOT_ENV_VALUE" = "prod" ]; then
    err "BOT_ENV=prod detected in .env"
    err "Dev stack uses test bot only. Set BOT_ENV=dev before running 'make dev'."
    exit 1
fi

# ── Read NGROK_DOMAIN from .env ───────────────────────────────────────────────
NGROK_DOMAIN=$(grep -m1 '^NGROK_DOMAIN=' .env 2>/dev/null | cut -d= -f2 | tr -d '[:space:]' || true)
if [ -z "$NGROK_DOMAIN" ]; then
    err "NGROK_DOMAIN is not set in .env"
    err "Add: NGROK_DOMAIN=your-static-domain.ngrok-free.app"
    exit 1
fi
NGROK_URL="https://${NGROK_DOMAIN}"
WEBHOOK_URL="${NGROK_URL}${WEBHOOK_PATH}"
log "ngrok domain: $NGROK_DOMAIN"

# ── 1. Start services (nginx excluded from dev) ───────────────────────────────
mkdir -p "$PROJECT_DIR/media"
log "Starting services: postgres redis ngrok web celery_worker celery_beat ..."
$COMPOSE up -d postgres redis ngrok web celery_worker celery_beat

# ── 2. Wait for web service (via Docker internal exec, no localhost needed) ───
log "Waiting for web service health check (up to ${WEB_WAIT_SEC}s) ..."
for i in $(seq 1 "$WEB_WAIT_SEC"); do
    if $COMPOSE exec -T web curl -sf --max-time 2 http://localhost:8000/health/ -o /dev/null 2>/dev/null; then
        ok "Web service is healthy (${i}s)."
        break
    fi
    printf "    waiting... %ds\r" "$i"
    sleep 1
    if [ "$i" -eq "$WEB_WAIT_SEC" ]; then
        echo ""
        err "Web service did not become healthy in ${WEB_WAIT_SEC}s."
        err "Check logs: $COMPOSE logs web"
        exit 1
    fi
done
echo ""

# ── 3. Register webhook ───────────────────────────────────────────────────────
log "Registering Telegram webhook: $WEBHOOK_URL"
$COMPOSE exec -T web python manage.py setup_webhook --url "$WEBHOOK_URL"

# ── 4. Final status ───────────────────────────────────────────────────────────
echo ""
log "Webhook info:"
$COMPOSE exec -T web python manage.py setup_webhook --info

echo ""
echo "╔════════════════════════════════════════════════════════════╗"
echo "║  Dev stack ready.                                          ║"
printf "║  Bot env  : %-47s║\n" "dev (test bot)"
printf "║  URL      : %-47s║\n" "$NGROK_URL"
printf "║  Webhook  : %-47s║\n" "$WEBHOOK_URL"
echo "║  Inspector: http://localhost:4040                          ║"
printf "║  Admin    : %-47s║\n" "${NGROK_URL}/django-admin/"
printf "║  CRM      : %-47s║\n" "${NGROK_URL}/crm/"
printf "║  Stats    : %-47s║\n" "${NGROK_URL}/stats/"
echo "║                                                            ║"
echo "║  make logs         — follow logs                          ║"
echo "║  make webhook-info — check webhook status                  ║"
echo "║  make dev-down     — stop everything                       ║"
echo "╚════════════════════════════════════════════════════════════╝"
