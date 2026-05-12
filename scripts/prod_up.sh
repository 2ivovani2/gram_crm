#!/usr/bin/env bash
# One-command production startup for the unified Gramly stack.
#
# Flow:
#   1. Validate BOT_ENV=prod in spambotcontrol/.env
#   2. Read DOMAIN from root .env
#   3. Obtain Let's Encrypt cert for all four domains via certbot standalone
#      (certbot binds port 80 directly — nginx not needed for this step)
#   4. Build all Docker images
#   5. Start all services
#   6. Wait for web service to become healthy
#   7. Register Telegram webhook
#   8. Print status
#
# Prerequisites on VPS:
#   apt install -y docker.io docker-compose-plugin curl git
#   git clone <repo> && cd full_gramly
#   cp .env.example .env
#       → set POSTGRES_PASSWORD, SPAM_JWT_SECRET_KEY, DOMAIN, CERTBOT_EMAIL
#       → set AWS_ACCESS_KEY_ID, AWS_SECRET_ACCESS_KEY, AWS_STORAGE_BUCKET_NAME
#   cp spambotcontrol/.env.example spambotcontrol/.env
#       → set BOT_ENV=prod, PROD_BOT_TOKEN, SECRET_KEY, TELEGRAM_WEBHOOK_SECRET,
#          ALLOWED_HOSTS=gramly.tech,www.gramly.tech,crm.gramly.tech
#          DEBUG=False, DATABASE_URL=postgres://...:...@postgres:5432/<POSTGRES_DB>
#
# Usage: make prod  (or: bash scripts/prod_up.sh)

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

# ── 1. Guard: require BOT_ENV=prod in spambotcontrol/.env ─────────────────────
if [ ! -f "spambotcontrol/.env" ]; then
    err "spambotcontrol/.env not found."
    err "Run: cp spambotcontrol/.env.example spambotcontrol/.env  and fill it in."
    exit 1
fi
BOT_ENV_VALUE=$(grep -m1 '^BOT_ENV=' spambotcontrol/.env 2>/dev/null | cut -d= -f2 | tr -d '[:space:]' || true)
if [ "$BOT_ENV_VALUE" != "prod" ]; then
    err "BOT_ENV is not 'prod' in spambotcontrol/.env (got: '${BOT_ENV_VALUE:-<empty>}')"
    err "Set BOT_ENV=prod and fill in PROD_BOT_TOKEN, DEBUG=False, ALLOWED_HOSTS etc."
    exit 1
fi

# ── 2. Read DOMAIN from root .env ──────────────────────────────────────────────
if [ ! -f ".env" ]; then
    err ".env not found at project root."
    err "Run: cp .env.example .env  and fill it in."
    exit 1
fi
DOMAIN=$(grep -m1 '^DOMAIN=' .env 2>/dev/null | cut -d= -f2 | tr -d '[:space:]' || true)
if [ -z "$DOMAIN" ]; then
    err "DOMAIN is not set in .env. Add: DOMAIN=gramly.tech"
    exit 1
fi
log "Domain: $DOMAIN"

WEBHOOK_URL="https://${DOMAIN}${WEBHOOK_PATH}"
CERTBOT_EMAIL=$(grep -m1 '^CERTBOT_EMAIL=' .env 2>/dev/null | cut -d= -f2 | tr -d '[:space:]' || true)

# Docker Compose derives the project name from the directory name (lowercased).
PROJECT_NAME=$(basename "$PROJECT_DIR" | tr '[:upper:]' '[:lower:]' | tr -cd 'a-z0-9_-')

# ── 3. Check if Let's Encrypt cert already exists ─────────────────────────────
CERT_EXISTS=false
VOL_MOUNT=$(docker volume inspect "${PROJECT_NAME}_letsencrypt" --format '{{.Mountpoint}}' 2>/dev/null || true)
if [ -n "$VOL_MOUNT" ] && [ -f "$VOL_MOUNT/live/$DOMAIN/fullchain.pem" ]; then
    CERT_EXISTS=true
    ok "Certificate already exists for $DOMAIN — skipping issuance."
fi

# ── 4. Obtain cert (first deploy only) ────────────────────────────────────────
if [ "$CERT_EXISTS" = "false" ]; then
    log "No certificate found. Obtaining Let's Encrypt certificate..."
    log "DNS A-records must point to this VPS: $DOMAIN, www.$DOMAIN, crm.$DOMAIN, spam.$DOMAIN"
    log ""

    # Start only postgres + redis so we don't bind port 80 yet.
    log "Starting postgres and redis..."
    $COMPOSE up -d postgres redis
    sleep 5

    if [ -n "$CERTBOT_EMAIL" ]; then
        EMAIL_FLAG="--email $CERTBOT_EMAIL --no-eff-email"
    else
        EMAIL_FLAG="--register-unsafely-without-email"
    fi

    # certbot standalone binds port 80 itself — no nginx needed for this step.
    log "Running certbot standalone (port 80 must be free)..."
    docker run --rm \
        -p 80:80 \
        -v "${PROJECT_NAME}_letsencrypt:/etc/letsencrypt" \
        certbot/certbot:latest certonly \
            --standalone \
            $EMAIL_FLAG \
            --agree-tos \
            --domains "$DOMAIN" \
            --domains "www.$DOMAIN" \
            --domains "crm.$DOMAIN" \
            --domains "spam.$DOMAIN" \
            --non-interactive

    ok "Certificate obtained for $DOMAIN (+ www, crm, spam subdomains)"
fi

# ── 5. Build all images ────────────────────────────────────────────────────────
log "Building Docker images..."
$COMPOSE build

# ── 6. Start all services ─────────────────────────────────────────────────────
log "Starting all services..."
$COMPOSE up -d

# ── 7. Wait for web service ────────────────────────────────────────────────────
log "Waiting for web service (up to ${WEB_WAIT_SEC}s)..."
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
        err "Check: make logs"
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
    WEBHOOK_SECRET=$(grep -m1 '^TELEGRAM_WEBHOOK_SECRET=' spambotcontrol/.env 2>/dev/null | cut -d= -f2 | tr -d '[:space:]' || true)
    PROD_TOKEN=$(grep -m1 '^PROD_BOT_TOKEN=' spambotcontrol/.env 2>/dev/null | cut -d= -f2 | tr -d '[:space:]' || true)
    echo ""
    echo "  Register webhook manually from another machine:"
    printf "  curl -F \"url=%s\" \\\n" "$WEBHOOK_URL"
    printf "       -F \"secret_token=%s\" \\\n" "${WEBHOOK_SECRET:-YOUR_SECRET}"
    echo  "       -F \"drop_pending_updates=true\" \\"
    printf "       \"https://api.telegram.org/bot%s/setWebhook\"\n" "${PROD_TOKEN:-YOUR_TOKEN}"
fi

# ── 9. Print status ────────────────────────────────────────────────────────────
echo ""
echo "╔══════════════════════════════════════════════════════════════╗"
printf "║  Landing    : %-47s║\n" "https://${DOMAIN}/"
printf "║  CRM        : %-47s║\n" "https://crm.${DOMAIN}/crm/login/"
printf "║  Spam app   : %-47s║\n" "https://spam.${DOMAIN}/"
printf "║  Admin      : %-47s║\n" "https://${DOMAIN}/django-admin/"
printf "║  Webhook    : %-47s║\n" "$WEBHOOK_URL"
if [ "$WEBHOOK_REGISTERED" = "true" ]; then
    echo "║  Registered : ✓                                              ║"
else
    echo "║  Registered : needs manual registration (see above)          ║"
fi
echo "║                                                              ║"
echo "║  make prod-down  — stop everything + delete webhook          ║"
echo "║  make logs       — follow web + celery logs                  ║"
echo "║  make logs-spam  — follow spam service logs                  ║"
echo "╚══════════════════════════════════════════════════════════════╝"
