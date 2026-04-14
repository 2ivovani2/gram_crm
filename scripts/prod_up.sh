#!/usr/bin/env bash
# One-command production startup for VPS with a real domain.
#
# Flow:
#   1. Validate BOT_ENV=prod
#   2. Read DOMAIN from .env
#   3. Obtain Let's Encrypt certificate (if not yet issued)
#      3a. Start nginx in HTTP-only mode (prod-init.conf)
#      3b. Run certbot webroot challenge
#      3c. Reload nginx with HTTPS config (prod.conf)
#   4. Build images, start all services
#   5. Wait for web to become healthy
#   6. Register Telegram webhook
#   7. Print status

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

# Derive project name the same way docker compose does (lowercased dir name)
PROJECT_NAME=$(basename "$PROJECT_DIR" | tr '[:upper:]' '[:lower:]' | tr -cd 'a-z0-9_-')

# ── 3. Check if Let's Encrypt cert already exists (via volume mountpoint) ─────
CERT_EXISTS=false
VOL_MOUNT=$(docker volume inspect "${PROJECT_NAME}_letsencrypt" --format '{{.Mountpoint}}' 2>/dev/null || true)
if [ -n "$VOL_MOUNT" ] && [ -f "$VOL_MOUNT/live/$DOMAIN/fullchain.pem" ]; then
    CERT_EXISTS=true
    ok "Certificate already exists for $DOMAIN, skipping issuance."
fi

# ── 5. Obtain cert (first deploy only) ────────────────────────────────────────
if [ "$CERT_EXISTS" = "false" ]; then
    log "No certificate found for $DOMAIN. Obtaining Let's Encrypt certificate..."

    # 5a. Start only postgres + redis (web runs on 8000, port 80 must be FREE for certbot)
    log "Starting postgres and redis..."
    $COMPOSE up -d postgres redis
    log "Waiting for postgres and redis..."
    sleep 5

    # 5b. Run certbot standalone — it binds port 80 itself, no nginx needed
    if [ -n "$CERTBOT_EMAIL" ]; then
        EMAIL_FLAG="--email $CERTBOT_EMAIL --no-eff-email"
    else
        EMAIL_FLAG="--register-unsafely-without-email"
    fi

    log "Running certbot standalone on port 80..."
    docker run --rm \
        -p 80:80 \
        -v "${PROJECT_NAME}_letsencrypt:/etc/letsencrypt" \
        certbot/certbot:latest certonly \
            --standalone \
            $EMAIL_FLAG \
            --agree-tos \
            --domains "$DOMAIN" \
            --domains "www.$DOMAIN" \
            --non-interactive

    ok "Certificate obtained for $DOMAIN"
fi

# ── 6. Build images ────────────────────────────────────────────────────────────
log "Building Docker images ..."
$COMPOSE build

# ── 7. Start all services ─────────────────────────────────────────────────────
log "Starting services ..."
$COMPOSE up -d postgres redis web celery_worker celery_beat nginx certbot

# ── 8. Wait for web service ────────────────────────────────────────────────────
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
        err "Web service did not become healthy in ${WEB_WAIT_SEC}s. Check: make logs-prod"
        exit 1
    fi
done
echo ""

# ── 9. Register Telegram webhook ──────────────────────────────────────────────
log "Registering Telegram webhook: $WEBHOOK_URL"

WEBHOOK_REGISTERED=false
if $COMPOSE exec -T web python manage.py setup_webhook --url "$WEBHOOK_URL" 2>&1; then
    WEBHOOK_REGISTERED=true
else
    WEBHOOK_SECRET=$(grep -m1 '^TELEGRAM_WEBHOOK_SECRET=' .env 2>/dev/null | cut -d= -f2 | tr -d '[:space:]' || true)
    PROD_TOKEN=$(grep -m1 '^PROD_BOT_TOKEN=' .env 2>/dev/null | cut -d= -f2 | tr -d '[:space:]' || true)
    echo ""
    echo "  Register webhook manually from a local machine:"
    printf "  curl -F \"url=%s\" \\\n" "$WEBHOOK_URL"
    printf "       -F \"secret_token=%s\" \\\n" "${WEBHOOK_SECRET:-YOUR_SECRET}"
    echo  "       -F \"drop_pending_updates=true\" \\"
    printf "       \"https://api.telegram.org/bot%s/setWebhook\"\n" "${PROD_TOKEN:-YOUR_TOKEN}"
fi

# ── 10. Print status ───────────────────────────────────────────────────────────
echo ""
echo "╔══════════════════════════════════════════════════════════════╗"
printf "║  Domain     : %-47s║\n" "https://${DOMAIN}/"
printf "║  Webhook    : %-47s║\n" "$WEBHOOK_URL"
printf "║  Admin      : %-47s║\n" "https://${DOMAIN}/django-admin/"
printf "║  CRM        : %-47s║\n" "https://${DOMAIN}/crm/"
if [ "$WEBHOOK_REGISTERED" = "true" ]; then
    echo "║  Webhook    : registered ✓                                   ║"
else
    echo "║  Webhook    : needs manual registration (see above)          ║"
fi
echo "║                                                              ║"
echo "║  make prod-down     — stop everything + delete webhook       ║"
echo "║  make logs-prod     — follow logs                            ║"
echo "╚══════════════════════════════════════════════════════════════╝"
