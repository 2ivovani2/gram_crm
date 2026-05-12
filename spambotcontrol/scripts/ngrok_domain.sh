#!/usr/bin/env bash
# Show current ngrok domain and the BotFather /setdomain command needed
# for the Telegram Login Widget (CRM login page) to work.
#
# Usage: make ngrok-domain

set -euo pipefail

NGROK_URL=$(python3 - <<'EOF' 2>/dev/null || true
import urllib.request, json
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

if [ -z "$NGROK_URL" ]; then
    echo ""
    echo "  [ERR] ngrok is not running or tunnel not established."
    echo "        Run 'make dev' first."
    echo ""
    exit 1
fi

DOMAIN=$(echo "$NGROK_URL" | sed 's|https://||')

echo ""
echo "  ngrok URL : $NGROK_URL"
echo "  Domain    : $DOMAIN"
echo ""
echo "  ┌─────────────────────────────────────────────────────────────┐"
echo "  │  Чтобы CRM Login Widget работал — задай домен в BotFather: │"
echo "  │                                                             │"
echo "  │  1. Открой Telegram → @BotFather                           │"
echo "  │  2. Отправь: /setdomain                                     │"
echo "  │  3. Выбери бота: @$(grep -m1 '^TELEGRAM_BOT_USERNAME=' .env 2>/dev/null | cut -d= -f2 | tr -d '[:space:]' || echo 'your_bot')                    │"
printf "  │  4. Отправь домен: %-40s│\n" "$DOMAIN"
echo "  │                                                             │"
echo "  │  ⚡ Совет: возьми статичный ngrok-домен (бесплатно, 1 шт.) │"
echo "  │     dashboard.ngrok.com → Domains → + New Domain           │"
echo "  │     Тогда домен не меняется при перезапуске.               │"
echo "  └─────────────────────────────────────────────────────────────┘"
echo ""
echo "  Или используй dev-bypass на странице /crm/login/ (DEBUG=True)"
echo "  — вход без Telegram-виджета, не требует BotFather настройки."
echo ""
