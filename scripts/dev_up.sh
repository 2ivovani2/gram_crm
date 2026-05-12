#!/usr/bin/env bash
# One-command local dev startup for the unified Gramly stack.
#
# Architecture:
#   User → ngrok (HTTPS) → nginx:80 → path routing → backends
#
# Single entry point via ngrok domain:
#   https://<ngrok>/           → Django (landing, /crm/, /docs/, /health/)
#   https://<ngrok>/bot/webhook/ → Telegram webhook
#   https://<ngrok>/spam/       → autosending Next.js frontend
#   https://<ngrok>/api/        → autosending FastAPI backend
#
# What this script does:
#   1. Validates BOT_ENV=dev in spambotcontrol/.env
#   2. Reads NGROK_DOMAIN from root .env
#   3. Starts all services (nginx included — it's the unified entry point)
#   4. Waits for web service to become healthy
#   5. Registers Telegram webhook at https://<ngrok>/bot/webhook/
#   6. Prints the single URL that exposes the entire system
#
# Prerequisites:
#   cp .env.example .env
#       → set NGROK_AUTHTOKEN, NGROK_DOMAIN, POSTGRES_PASSWORD, AWS_*, SPAM_JWT_SECRET_KEY
#   cp spambotcontrol/.env.example spambotcontrol/.env
#       → set BOT_ENV=dev, TEST_BOT_TOKEN, TELEGRAM_WEBHOOK_SECRET, SECRET_KEY
#
# Usage: make dev   (or: bash scripts/dev_up.sh)

set -Eeuo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
PROJECT_DIR="$(cd "$SCRIPT_DIR/.." && pwd)"
cd "$PROJECT_DIR"

COMPOSE="docker compose -f docker-compose.yml -f docker-compose.dev.yml -f docker-compose.ngrok.yml"
WEBHOOK_PATH="/bot/webhook/"
WEB_WAIT_SEC=90

log()  { echo "==> $*"; }
ok()   { echo "    [OK]   $*"; }
err()  { echo "    [ERR]  $*" >&2; }

# ── Guard: spambotcontrol must be in dev mode ─────────────────────────────────
if [ ! -f "spambotcontrol/.env" ]; then
    err "spambotcontrol/.env not found."
    err "Run: cp spambotcontrol/.env.example spambotcontrol/.env  and fill it in."
    exit 1
fi
BOT_ENV_VALUE=$(grep -m1 '^BOT_ENV=' spambotcontrol/.env 2>/dev/null | cut -d= -f2 | tr -d '[:space:]' || true)
if [ "$BOT_ENV_VALUE" = "prod" ]; then
    err "BOT_ENV=prod detected in spambotcontrol/.env"
    err "Dev stack uses test bot only. Set BOT_ENV=dev."
    exit 1
fi

# ── Guard: root .env must exist ────────────────────────────────────────────────
if [ ! -f ".env" ]; then
    err ".env not found at project root."
    err "Run: cp .env.example .env  and fill in NGROK_AUTHTOKEN, NGROK_DOMAIN, etc."
    exit 1
fi

# ── Read NGROK_DOMAIN ──────────────────────────────────────────────────────────
NGROK_DOMAIN=$(grep -m1 '^NGROK_DOMAIN=' .env 2>/dev/null | cut -d= -f2 | tr -d '[:space:]' || true)
if [ -z "$NGROK_DOMAIN" ]; then
    err "NGROK_DOMAIN is not set in .env"
    err "Add: NGROK_DOMAIN=your-static-domain.ngrok-free.app"
    exit 1
fi
BASE_URL="https://${NGROK_DOMAIN}"
WEBHOOK_URL="${BASE_URL}${WEBHOOK_PATH}"
log "Entry point: $BASE_URL"

# ── Start all services ─────────────────────────────────────────────────────────
# nginx is the unified entry point — start it alongside the app services.
# ngrok tunnels to nginx:80, so ALL traffic goes through the same contour.
# Startup order: infra → web (wait healthy) → nginx → ngrok
# spam-frontend may take longer to start (next dev) — nginx serves 502 until ready.
log "Starting services..."
$COMPOSE up -d \
    postgres redis \
    minio minio-init \
    web celery_worker celery_beat \
    spam-backend spam-frontend \
    nginx \
    ngrok

# ── Wait for Django web service ────────────────────────────────────────────────
log "Waiting for web service health check (up to ${WEB_WAIT_SEC}s)..."
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
        err "Check: docker compose -f docker-compose.yml -f docker-compose.dev.yml logs web"
        exit 1
    fi
done
echo ""

# ── Register Telegram webhook ──────────────────────────────────────────────────
log "Registering Telegram webhook: $WEBHOOK_URL"
$COMPOSE exec -T web python manage.py setup_webhook --url "$WEBHOOK_URL"

echo ""
log "Webhook info:"
$COMPOSE exec -T web python manage.py setup_webhook --info

# ── Final status ───────────────────────────────────────────────────────────────
echo ""
echo "╔══════════════════════════════════════════════════════════════════╗"
echo "║  Dev stack ready. Single entry point — everything via ngrok.    ║"
echo "║                                                                  ║"
printf "║  Landing    : %-51s║\n" "${BASE_URL}/"
printf "║  CRM        : %-51s║\n" "${BASE_URL}/crm/"
printf "║  Docs       : %-51s║\n" "${BASE_URL}/docs/"
printf "║  Spam app   : %-51s║\n" "${BASE_URL}/spam/"
printf "║  API        : %-51s║\n" "${BASE_URL}/api/"
printf "║  Webhook    : %-51s║\n" "${WEBHOOK_URL}"
echo "║                                                                  ║"
echo "║  Inspector  : http://localhost:4040   (ngrok)                   ║"
echo "║  MinIO S3   : http://localhost:9000   (S3 API + file URLs)      ║"
echo "║  MinIO UI   : http://localhost:9001   (console)                 ║"
echo "║  Postgres   : localhost:5432                                    ║"
echo "║  Redis      : localhost:6379                                    ║"
echo "║                                                                  ║"
echo "║  make logs         — web + celery logs                          ║"
echo "║  make logs-spam    — spam-backend + spam-frontend logs          ║"
echo "║  make webhook-info — check webhook status                       ║"
echo "║  make dev-down     — stop everything                            ║"
echo "╚══════════════════════════════════════════════════════════════════╝"
echo ""
echo "  Note: /spam/ routes may return 502 for ~30-60s while Next.js dev"
echo "  server finishes starting. All other routes are immediately ready."
