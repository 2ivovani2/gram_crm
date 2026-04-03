#!/usr/bin/env bash
# One-command local dev startup:
#   1. Start postgres, redis, ngrok, web, celery (nginx skipped in dev)
#   2. Wait for ngrok tunnel to be established
#   3. Wait for web service to be healthy
#   4. Register Telegram webhook for test bot using the ngrok URL
#   5. Print final status
#
# Usage: bash scripts/dev_up.sh   (or: make dev)

set -Eeuo pipefail

# ── Config ────────────────────────────────────────────────────────────────────
SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
PROJECT_DIR="$(cd "$SCRIPT_DIR/.." && pwd)"
cd "$PROJECT_DIR"

COMPOSE="docker-compose -f docker-compose.yml -f docker-compose.dev.yml -f docker-compose.ngrok.yml"
NGROK_API="http://localhost:4040/api/tunnels"
WEB_HEALTH="http://localhost:8000/health/"
WEBHOOK_PATH="/bot/webhook/"
NGROK_WAIT_SEC=40
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

# ── 1. Start services (nginx excluded from dev) ───────────────────────────────
log "Starting services: postgres redis ngrok web celery_worker celery_beat ..."
$COMPOSE up -d postgres redis ngrok web celery_worker celery_beat

# ── 2. Wait for ngrok tunnel ──────────────────────────────────────────────────
log "Waiting for ngrok tunnel (up to ${NGROK_WAIT_SEC}s) ..."
NGROK_URL=""
for i in $(seq 1 "$NGROK_WAIT_SEC"); do
    NGROK_URL=$(python3 - <<'EOF' 2>/dev/null || true
import urllib.request, json, sys
try:
    with urllib.request.urlopen("http://localhost:4040/api/tunnels", timeout=2) as r:
        tunnels = json.load(r).get("tunnels", [])
        https = [t for t in tunnels if t["proto"] == "https"]
        if https:
            print(https[0]["public_url"])
except Exception:
    pass
EOF
)
    if [ -n "$NGROK_URL" ]; then
        ok "ngrok public URL: $NGROK_URL"
        break
    fi
    printf "    waiting for ngrok... %ds\r" "$i"
    sleep 1
done
echo ""  # clear the \r line

if [ -z "$NGROK_URL" ]; then
    err "ngrok did not establish a tunnel in ${NGROK_WAIT_SEC}s"
    err "Check ngrok logs: $COMPOSE logs ngrok"
    err "Verify NGROK_AUTHTOKEN is set correctly in .env"
    exit 1
fi

WEBHOOK_URL="${NGROK_URL}${WEBHOOK_PATH}"

# ── 3. Wait for web service ───────────────────────────────────────────────────
log "Waiting for web service health check (up to ${WEB_WAIT_SEC}s) ..."
bash "$SCRIPT_DIR/wait_for_http.sh" "$WEB_HEALTH" "$WEB_WAIT_SEC"

# ── 4. Register webhook ───────────────────────────────────────────────────────
log "Registering Telegram webhook: $WEBHOOK_URL"
$COMPOSE exec -T web python manage.py setup_webhook --url "$WEBHOOK_URL"

# ── 5. Final status ───────────────────────────────────────────────────────────
echo ""
log "Webhook info:"
$COMPOSE exec -T web python manage.py setup_webhook --info

echo ""
echo "╔══════════════════════════════════════════════════════╗"
echo "║  Dev stack ready.                                    ║"
printf "║  Bot env  : %-41s║\n" "dev (test bot)"
printf "║  Webhook  : %-41s║\n" "$WEBHOOK_URL"
echo "║  Inspector: http://localhost:4040                    ║"
echo "║                                                      ║"
echo "║  make logs         — follow logs                     ║"
echo "║  make webhook-info — check webhook status            ║"
echo "║  make dev-down     — stop everything                 ║"
echo "╚══════════════════════════════════════════════════════╝"
