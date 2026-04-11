#!/usr/bin/env bash
# One-command production startup for VPS with a real domain (gramly.tech).
#
# Flow:
#   1. Validate BOT_ENV=prod in .env
#   2. Read DOMAIN from .env
#   3. Obtain Let's Encrypt certificate (if not yet issued)
#   4. Build images and start all services (nginx gets HTTPS config)
#   5. Wait for web service to become healthy
#   6. Register Telegram webhook
#   7. Print final status
#
# Prerequisites on VPS:
#   apt install -y docker.io docker-compose-plugin curl
#   git clone <repo> && cd <repo>
#   cp .env.example .env && nano .env   # set DOMAIN, BOT_ENV=prod, PROD_BOT_TOKEN, etc.
#   # Point gramly.tech A-record to this VPS IP before running
#
# Usage:
#   make prod          (recommended)
#   bash scripts/prod_up.sh

set -Eeuo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
PROJECT_DIR="$(cd "$SCRIPT_DIR/.." && pwd)"
cd "$PROJECT_DIR"

COMPOSE="docker compose -f docker-compose.yml"
WEBHOOK_PATH="/bot/webhook/"
WEB_WAIT_SEC=120

log()  { echo "==> $*"; }
ok()   { echo "    [OK]   $*"; }
skip() { echo "    [SKIP] $*"; }
err()  { echo "    [ERR]  $*" >&2; }

# ── 1. Guard: require BOT_ENV=prod ────────────────────────────────────────────
BOT_ENV_VALUE=$(grep -m1 '^BOT_ENV=' .env 2>/dev/null | cut -d= -f2 | tr -d '[:space:]' || true)
if [ "$BOT_ENV_VALUE" != "prod" ]; then
    err "BOT_ENV is not 'prod' in .env (got: '${BOT_ENV_VALUE:-<empty>}')"
    err "Set BOT_ENV=prod in .env before running 'make prod'."
    exit 1
fi

# ── 2. Read DOMAIN ─────────────────────────────────────────────────────────────
DOMAIN=$(grep -m1 '^DOMAIN=' .env 2>/dev/null | cut -d= -f2 | tr -d '[:space:]' || true)
if [ -z "$DOMAIN" ]; then
    err "DOMAIN is not set in .env. Add: DOMAIN=gramly.tech"
    exit 1
fi
log "Domain: $DOMAIN"

WEBHOOK_URL="https://${DOMAIN}${WEBHOOK_PATH}"
CERTBOT_EMAIL=$(grep -m1 '^CERTBOT_EMAIL=' .env 2>/dev/null | cut -d= -f2 | tr -d '[:space:]' || true)

# ── 3. Ensure media directory exists ──────────────────────────────────────────
mkdir -p "$PROJECT_DIR/media"

# ── 4. Check if Let's Encrypt cert already exists ─────────────────────────────
CERT_EXISTS=false
if $COMPOSE run --rm certbot certificates 2>/dev/null | grep -q "$DOMAIN"; then
    CERT_EXISTS=true
fi

if [ "$CERT_EXISTS" = "false" ]; then
    log "No certificate found for $DOMAIN. Obtaining Let's Encrypt certificate..."

    # Start nginx in HTTP-only mode first so ACME challenge can be served
    cp nginx/prod.conf nginx/prod.conf.bak
    cp nginx/prod-init.conf nginx/prod.conf.tmp
    # Temporarily use init config
    $COMPOSE up -d postgres redis web nginx || true
    log "Waiting for web to come up for ACME challenge..."
    sleep 10

    # Swap nginx config to init (HTTP only) and reload
    docker compose -f docker-compose.yml exec -T nginx sh -c \
        "cp /dev/stdin /etc/nginx/conf.d/default.conf && nginx -s reload" \
        < nginx/prod-init.conf 2>/dev/null || true
    sleep 2

    # Obtain cert
    if [ -n "$CERTBOT_EMAIL" ]; then
        EMAIL_FLAG="--email $CERTBOT_EMAIL --no-eff-email"
    else
        EMAIL_FLAG="--register-unsafely-without-email"
    fi

    $COMPOSE run --rm certbot certonly \
        --webroot \
        --webroot-path /var/www/certbot \
        $EMAIL_FLAG \
        --agree-tos \
        --domains "$DOMAIN" \
        --domains "www.$DOMAIN" \
        --non-interactive

    ok "Certificate obtained for $DOMAIN"

    # Restore full HTTPS nginx config
    $COMPOSE exec -T nginx sh -c \
        "cp /dev/stdin /etc/nginx/conf.d/default.conf && nginx -s reload" \
        < nginx/prod.conf 2>/dev/null || true
else
    skip "Certificate already exists for $DOMAIN, skipping issuance."
fi

# ── 5. Build images ────────────────────────────────────────────────────────────
log "Building Docker images ..."
$COMPOSE build

# ── 6. Start all services ─────────────────────────────────────────────────────
log "Starting services ..."
$COMPOSE up -d postgres redis web celery_worker celery_beat nginx certbot

# ── 7. Wait for web service ────────────────────────────────────────────────────
log "Waiting for web service (up to ${WEB_WAIT_SEC}s) ..."
for i in $(seq 1 "$WEB_WAIT_SEC"); do
    if $COMPOSE exec -T web curl -sf --max-time 3 http://localhost:8000/health/ -o /dev/null 2>/dev/null; then
        ok "Web service is healthy (${i}s)."
        break
    fi
    printf "    waiting... %ds\r" "$i"
    sleep 1
    if [ "$i" -eq "$WEB_WAIT_SEC" ]; then
        echo ""
        err "Web service did not become healthy in ${WEB_WAIT_SEC}s."
        err "Check logs: make logs-prod"
        exit 1
    fi
done
echo ""

# ── 8. Register Telegram webhook ──────────────────────────────────────────────
log "Registering Telegram webhook: $WEBHOOK_URL"

WEBHOOK_REGISTERED=false
if $COMPOSE exec -T web python manage.py setup_webhook --url "$WEBHOOK_URL" 2>&1; then
    WEBHOOK_REGISTERED=true
else
    echo ""
    echo "  ┌──────────────────────────────────────────────────────────────┐"
    echo "  │  Could not reach api.telegram.org from VPS.                  │"
    echo "  │  Register webhook manually from a local machine:             │"
    echo "  │                                                               │"
    WEBHOOK_SECRET=$(grep -m1 '^TELEGRAM_WEBHOOK_SECRET=' .env 2>/dev/null | cut -d= -f2 | tr -d '[:space:]' || true)
    PROD_TOKEN=$(grep -m1 '^PROD_BOT_TOKEN=' .env 2>/dev/null | cut -d= -f2 | tr -d '[:space:]' || true)
    printf "  │  curl -F \"url=%s\" \\\\\n" "$WEBHOOK_URL"
    printf "  │       -F \"secret_token=%s\" \\\\\n" "${WEBHOOK_SECRET:-YOUR_SECRET}"
    echo  "  │       -F \"drop_pending_updates=true\" \\"
    printf "  │       \"https://api.telegram.org/bot%s/setWebhook\"\n" "${PROD_TOKEN:-YOUR_TOKEN}"
    echo "  └──────────────────────────────────────────────────────────────┘"
fi

# ── 9. Print status ────────────────────────────────────────────────────────────
echo ""
echo "╔══════════════════════════════════════════════════════════════╗"
echo "║  Production stack ready.                                     ║"
printf "║  Domain     : %-47s║\n" "https://${DOMAIN}/"
printf "║  Webhook    : %-47s║\n" "$WEBHOOK_URL"
printf "║  Admin      : %-47s║\n" "https://${DOMAIN}/django-admin/"
printf "║  CRM        : %-47s║\n" "https://${DOMAIN}/crm/"
printf "║  Stats      : %-47s║\n" "https://${DOMAIN}/stats/"
if [ "$WEBHOOK_REGISTERED" = "true" ]; then
    echo "║  Webhook    : registered ✓                                   ║"
else
    echo "║  Webhook    : needs manual registration (see above)          ║"
fi
echo "║                                                              ║"
echo "║  make prod-down     — stop everything + delete webhook       ║"
echo "║  make logs-prod     — follow logs                            ║"
echo "╚══════════════════════════════════════════════════════════════╝"
