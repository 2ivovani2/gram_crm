# Dev Log — spambotcontrol

Chronological record of architectural decisions, changes, and rationale.
Updated after every significant step.

---

## 2026-04-03 — Webhook-only architecture + dual-bot setup

### What was done

Implemented webhook-only Telegram bot infrastructure with separate test and prod bots.
This replaces the single-token approach and adds ngrok support for local development.

#### Files modified

- **`config/settings/base.py`**
  - Replaced `TELEGRAM_BOT_TOKEN = env("TELEGRAM_BOT_TOKEN")` with BOT_ENV-based selection:
    - `BOT_ENV=dev` → reads `TEST_BOT_TOKEN`
    - `BOT_ENV=prod` → reads `PROD_BOT_TOKEN`
  - All downstream code (`bot.py`, `tasks.py`, `webhook.py`) unchanged — they read `settings.TELEGRAM_BOT_TOKEN`

- **`.env.example`**
  - Added: `BOT_ENV`, `TEST_BOT_TOKEN`, `PROD_BOT_TOKEN`, `NGROK_AUTHTOKEN`
  - Removed: `TELEGRAM_BOT_TOKEN` (old single-token approach)
  - Documented: all vars with comments explaining dev vs prod usage

- **`.env`**
  - Migrated from `TELEGRAM_BOT_TOKEN` to `TEST_BOT_TOKEN` + `PROD_BOT_TOKEN` + `BOT_ENV=dev`
  - Added `NGROK_AUTHTOKEN` placeholder

- **`apps/telegram_bot/management/commands/setup_webhook.py`**
  - Added: prints active `BOT_ENV` and first 10 chars of token before each operation
  - Fixed: removed "switch to polling" from `--delete` help text (polling is forbidden)
  - Improved: error message for missing `TELEGRAM_WEBHOOK_URL` now gives actionable hints

#### Files created

- **`docker-compose.ngrok.yml`**
  - Defines `ngrok` service using `ngrok/ngrok:latest` image
  - Tunnels `web:8000` to a public HTTPS URL
  - Exposes ngrok inspector UI on `localhost:4040`
  - Used as third override: `docker-compose -f ...yml -f ...dev.yml -f ...ngrok.yml up`

- **`scripts/update_ngrok_webhook.sh`**
  - Reads current ngrok tunnel URL via `localhost:4040/api/tunnels`
  - Updates `TELEGRAM_WEBHOOK_URL` in `.env` in-place (macOS + Linux compatible)
  - Calls `python manage.py setup_webhook` inside the web container
  - Must be re-run each time ngrok restarts (URL changes on free plan)

- **`docs/dev_log.md`** (this file)
- **`docs/`** directory created

### Why this was done

**Requirement:** Webhook-only mode (no polling in any environment).
**Requirement:** Two separate Telegram bots — test and prod — to avoid cross-contamination of data and webhooks.
**Requirement:** Local development must also use webhook, not polling.

Key design decisions:
1. **Single `BOT_ENV` switch** instead of separate `.env` files per environment — simpler, less duplication.
2. **ngrok as a Docker service** (not installed on host) — keeps dev environment fully containerized and reproducible.
3. **`update_ngrok_webhook.sh` script** — automates the most error-prone step (getting ngrok URL and registering it with Telegram). Manual copy-paste of ngrok URLs is a common source of mistakes.
4. **Token selection in `base.py`** not in `dev.py`/`prod.py` — because `TELEGRAM_BOT_TOKEN` is consumed by base-level code that both environments share. If it were in `dev.py`, prod would fail to load it.
5. **Kept `settings.TELEGRAM_BOT_TOKEN` as the internal name** — zero changes to `bot.py`, `tasks.py`, `webhook.py`, Celery tasks. Selection happens once at settings load time.

### What to do next

- Fill in real `TEST_BOT_TOKEN` and `PROD_BOT_TOKEN` in `.env` from @BotFather
- Get `NGROK_AUTHTOKEN` from `dashboard.ngrok.com` and add to `.env`
- Run `docker-compose -f docker-compose.yml -f docker-compose.dev.yml -f docker-compose.ngrok.yml up -d`
- Run `bash scripts/update_ngrok_webhook.sh`
- Verify with `python manage.py setup_webhook --info`
- Consider ngrok static domain (paid plan) to avoid re-registering webhook on every restart

---

---

## 2026-04-03 — One-command dev flow (make dev)

### What was done

Replaced the multi-step manual dev startup with a single `make dev` command that fully
orchestrates the local webhook-only dev environment.

#### Files created

- **`Makefile`** — targets: `dev`, `dev-down`, `logs`, `webhook-info`
- **`scripts/dev_up.sh`** — full orchestration:
  1. Guard against `BOT_ENV=prod` being used in dev
  2. `docker-compose up -d` for all services except nginx
  3. Poll `localhost:4040/api/tunnels` until ngrok tunnel is up (max 40s)
  4. Poll `localhost:8000/health/` until web is healthy (max 90s)
  5. Call `python manage.py setup_webhook --url <ngrok_url>/bot/webhook/`
  6. Print final status box
- **`scripts/dev_down.sh`** — stop stack + best-effort webhook removal from test bot
- **`scripts/wait_for_http.sh`** — generic HTTP readiness poller with timeout

#### Files modified

- **`apps/telegram_bot/management/commands/setup_webhook.py`**
  - Added `--url` parameter: allows passing webhook URL directly, bypasses `settings.TELEGRAM_WEBHOOK_URL`
  - This is the key that makes one-command flow work without runtime env file injection

- **`docker-compose.dev.yml`**
  - Added explicit comments about nginx exclusion in dev startup command

- **`docker-compose.ngrok.yml`**
  - Removed `depends_on: web` — ngrok starts independently of web; it queues requests until target is available

#### Files deleted

- **`scripts/update_ngrok_webhook.sh`** — superseded by `scripts/dev_up.sh`

### Why this was done

**Requirement:** All of startup, ngrok tunnel, and webhook registration in one command.

Key design decision: **`--url` parameter on `setup_webhook`**, not runtime env file injection.

Two alternatives were considered:
1. **Runtime env file** (`dev.runtime.env`): generate file with ngrok URL, restart web container
   - Rejected: requires container restart; breaks running state; adds file lifecycle complexity
2. **`--url` parameter**: pass URL directly to management command running inside already-live container
   - Accepted: zero container restarts; idempotent; clean; containers don't need to know the URL at startup

The key insight: `TELEGRAM_WEBHOOK_URL` is only needed by the `setup_webhook` management command,
not by the running application. Telegram sends updates to whatever URL is registered — the app
just validates the secret token and processes the update, without knowing its own URL.

### What to do next

- `cp .env.example .env` and fill: `TEST_BOT_TOKEN`, `NGROK_AUTHTOKEN`, `TELEGRAM_WEBHOOK_SECRET`
- `make dev` — should work end-to-end

<!-- Add new entries above this line in the same format -->
