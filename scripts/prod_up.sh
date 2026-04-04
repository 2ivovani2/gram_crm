#!/usr/bin/env bash
# One-command production startup for VPS (no domain, self-signed SSL):
#   1. Validate BOT_ENV=prod in .env
#   2. Read VPS_IP from .env (or prompt if missing)
#   3. Generate self-signed SSL cert for the VPS IP (if not exists)
#   4. Build images and start all services
#   5. Wait for the web service to become healthy
#   6. Register Telegram webhook with the self-signed certificate
#   7. Print final status
#
# Prerequisites on VPS:
#   apt install -y docker.io docker-compose-plugin openssl curl
#   git clone <repo> && cd <repo>
#   cp .env.example .env && nano .env   # set BOT_ENV=prod, PROD_BOT_TOKEN, VPS_IP, etc.
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
SSL_DIR="$PROJECT_DIR/ssl"
CERT_FILE="$SSL_DIR/webhook.pem"
KEY_FILE="$SSL_DIR/webhook.key"
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

# ── 2. Read VPS_IP ─────────────────────────────────────────────────────────────
VPS_IP=$(grep -m1 '^VPS_IP=' .env 2>/dev/null | cut -d= -f2 | tr -d '[:space:]' || true)
if [ -z "$VPS_IP" ]; then
    read -rp "==> Enter your VPS IP address: " VPS_IP
    if [ -z "$VPS_IP" ]; then
        err "VPS_IP is required. Add VPS_IP=<your_ip> to .env or enter it above."
        exit 1
    fi
fi
log "VPS IP: $VPS_IP"

WEBHOOK_URL="https://${VPS_IP}${WEBHOOK_PATH}"

# ── 3. Generate self-signed SSL certificate ────────────────────────────────────
mkdir -p "$SSL_DIR"
if [ -f "$CERT_FILE" ] && [ -f "$KEY_FILE" ]; then
    CERT_CN=$(openssl x509 -noout -subject -in "$CERT_FILE" 2>/dev/null | grep -oP 'CN\s*=\s*\K[^,/]+' || true)
    if [ "$CERT_CN" = "$VPS_IP" ]; then
        skip "SSL certificate already exists for $VPS_IP, reusing."
    else
        log "SSL certificate exists but CN=$CERT_CN differs from VPS_IP=$VPS_IP — regenerating."
        openssl req -newkey rsa:2048 -sha256 -nodes \
            -keyout "$KEY_FILE" \
            -x509 -days 3650 \
            -out "$CERT_FILE" \
            -subj "/CN=${VPS_IP}" 2>/dev/null
        ok "SSL certificate regenerated: $CERT_FILE"
    fi
else
    log "Generating self-signed SSL certificate for IP $VPS_IP ..."
    openssl req -newkey rsa:2048 -sha256 -nodes \
        -keyout "$KEY_FILE" \
        -x509 -days 3650 \
        -out "$CERT_FILE" \
        -subj "/CN=${VPS_IP}" 2>/dev/null
    ok "SSL certificate generated: $CERT_FILE"
fi

# ── 4. Build images ────────────────────────────────────────────────────────────
log "Building Docker images ..."
$COMPOSE build

# ── 5. Start all services ─────────────────────────────────────────────────────
log "Starting services: postgres redis web celery_worker celery_beat nginx ..."
$COMPOSE up -d postgres redis web celery_worker celery_beat nginx

# ── 6. Wait for web service ────────────────────────────────────────────────────
log "Waiting for web service (up to ${WEB_WAIT_SEC}s) ..."
for i in $(seq 1 "$WEB_WAIT_SEC"); do
    if $COMPOSE exec -T web curl -sf -H "Host: ${VPS_IP}" http://localhost:8000/health/ -o /dev/null 2>/dev/null; then
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

# ── 7. Register Telegram webhook with self-signed cert ────────────────────────
log "Registering Telegram webhook: $WEBHOOK_URL"
WEBHOOK_SECRET=$(grep -m1 '^TELEGRAM_WEBHOOK_SECRET=' .env 2>/dev/null | cut -d= -f2 | tr -d '[:space:]' || true)
PROD_TOKEN=$(grep -m1 '^PROD_BOT_TOKEN=' .env 2>/dev/null | cut -d= -f2 | tr -d '[:space:]' || true)

WEBHOOK_REGISTERED=false
if $COMPOSE exec -T web python manage.py setup_webhook \
    --url "$WEBHOOK_URL" \
    --certificate /app/ssl/webhook.pem 2>&1 || false; then
    WEBHOOK_REGISTERED=true
else
    echo ""
    echo "  ┌────────────────────────────────────────────────────���────────┐"
    echo "  │  VPS не может достучаться до api.telegram.org               │"
    echo "  │  (типично для российских хостингов)                         │"
    echo "  │                                                              │"
    echo "  │  Зарегистрируй webhook вручную с локальной машины:          │"
    echo "  │                                                              │"
    echo "  │  1. Скопируй сертификат на локалку:                         │"
    printf "  │     scp root@%s:$(pwd)/ssl/webhook.pem ./webhook.pem\n" "$VPS_IP"
    echo "  │                                                              │"
    echo "  │  2. Запусти curl:                                            │"
    echo "  │                                                              │"
    printf "  │  curl -F \"url=%s\" \\\\\n" "$WEBHOOK_URL"
    echo  "  │       -F \"certificate=@webhook.pem\" \\"
    printf "  │       -F \"secret_token=%s\" \\\\\n" "${WEBHOOK_SECRET:-YOUR_SECRET}"
    echo  "  │       -F \"drop_pending_updates=true\" \\"
    printf "  │       \"https://api.telegram.org/bot%s/setWebhook\"\n" "${PROD_TOKEN:-YOUR_TOKEN}"
    echo "  └─────────────────────────────────────────────────────────────┘"
fi

# ── 8. Print status ────────────────────────────────────────────────────────────
echo ""
echo "╔══════════════════════════════════════════════════════════╗"
echo "║  Production stack ready.                                 ║"
printf "║  Bot env   : %-43s║\n" "prod (production bot)"
printf "║  Webhook   : %-43s║\n" "$WEBHOOK_URL"
printf "║  Admin     : %-43s║\n" "https://${VPS_IP}/admin/"
if [ "$WEBHOOK_REGISTERED" = "true" ]; then
    echo "║  Webhook   : registered ✓                                ║"
else
    echo "║  Webhook   : требует ручной регистрации (см. выше)       ║"
fi
echo "║                                                          ║"
echo "║  make prod-down     — stop everything + delete webhook   ║"
echo "║  make logs-prod     — follow logs                        ║"
echo "╚══════════════════════════════════════════════════════════╝"
