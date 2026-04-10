# Dev Log — spambotcontrol

Chronological record of architectural decisions, changes, and rationale.
Updated after every significant step.

---

## 2026-04-05 — Referral system rework + withdrawal mechanism

### What was done

Replaced the global `ReferralSettings.rate_percent` model with per-user rates. Added full withdrawal request flow (worker → admin notifications → approve/reject → balance recalculation).

#### Balance calculation
`balance = attracted_count × personal_rate + Σ(ref.attracted_count × referral_rate for ref in direct referrals) − Σ(approved withdrawals)`

Recalculated automatically when admin changes `attracted_count`, `personal_rate`, or `referral_rate` of any user. Also recalculates the referrer's balance when a referral's `attracted_count` changes.

#### New app: `apps/withdrawals/`
- `WithdrawalRequest` model: `user`, `amount`, `method` (cryptobot/usdt_trc20), `details`, `status` (pending/approved/rejected), `processed_by`, `processed_at`, `admin_notifications` (JSONField — list of `{telegram_id, message_id}`)
- `WithdrawalService`: create, approve (deducts balance), reject, save_admin_notifications, get_list
- `WithdrawalRequestAdmin` (Django admin, unfold)

#### Worker withdrawal flow
1. Button "💸 Вывод средств" on main menu and profile
2. Choose method: CryptoBot or USDT TRC20
3. Enter details (validated: @username regex or TRC20 address regex `^T[a-zA-Z0-9]{33}$`)
4. Request saved; user gets confirmation; all admins notified via bot message with approve/reject buttons

#### Admin withdrawal handling
- "💸 Выводы" section in admin main menu
- List with pagination, card view
- Approve: balance recalculated (deducts amount), user notified, all other admin notification messages edited to "Обработано {admin}"
- Reject: status updated, user notified, other admin messages edited
- First admin to act locks the request — others see "already processed" on callback

#### User model changes
- Added `personal_rate` (Decimal, default 0) — rate per direct subscriber
- Added `referral_rate` (Decimal, default 0) — rate per referral's subscriber
- `attracted_count` help_text updated

#### User card (admin bot)
- Shows `personal_rate`, `referral_rate`
- Two new buttons: "💰 Личная ставка", "🤝 Ставка за рефералов" → FSM to set per-user rates

#### Migrations
- `users/0004_user_personal_rate_user_referral_rate_and_more.py`
- `withdrawals/0001_initial.py`

#### Design decisions
- Per-user rates (not global) — each worker can have individual economics
- Balance recalculation is always deterministic (counts × rates − withdrawn), never cumulative increments — prevents drift from manual edits
- `admin_notifications` as JSONField on WithdrawalRequest — avoids extra table, safe for concurrent admin edits
- Withdrawal only possible if `balance > 0` — checked at entry point and before save

---

## 2026-04-04 — Production VPS deployment + user guides

### What was done

Prepared a complete one-command production deployment for VPS `45.135.164.155` (no domain, HTTPS via self-signed SSL certificate). Added usage documentation for the bot and Django admin panel.

#### Problem
Telegram requires HTTPS for webhooks. Without a domain, a standard Let's Encrypt certificate is not possible. Solution: self-signed certificate — Telegram explicitly supports this via the `certificate` parameter in `setWebhook`.

#### SSL approach
- `openssl req -newkey rsa:2048 -subj "/CN=<VPS_IP>"` generates a 10-year self-signed cert
- Nginx serves HTTPS on port 443 with this cert
- `setup_webhook --certificate /app/ssl/webhook.pem` uploads the cert to Telegram at registration time
- Cert is stored in `ssl/` directory (gitignored — never commit private keys)
- If VPS_IP changes, the cert is automatically regenerated on next `make prod`

#### Files created/modified

- **`nginx/prod.conf`** — HTTPS nginx config (listens on 443 with self-signed cert, HTTP:80 → HTTPS redirect)
- **`docker-compose.yml`** — nginx now mounts `nginx/prod.conf` and `./ssl:/etc/ssl/bot:ro`
- **`scripts/prod_up.sh`** — one-command prod startup: validates BOT_ENV=prod → reads VPS_IP → generates SSL cert → builds images → starts all services → waits for web health (exec curl inside container) → registers webhook with cert
- **`scripts/prod_down.sh`** — stops prod stack + deletes webhook
- **`apps/telegram_bot/management/commands/setup_webhook.py`** — added `--certificate <path>` argument; when provided, uploads PEM file to Telegram via `FSInputFile`
- **`Makefile`** — added `make prod`, `make prod-down`, `make logs-prod`
- **`.env.example`** — added `VPS_IP` variable with explanation
- **`.gitignore`** — added `ssl/` entry
- **`docs/bot_guide.md`** — full usage guide for workers and admins (bot commands, flows, all sections)
- **`docs/django_admin_guide.md`** — full guide for Django admin panel (all models, common operations)
- **`CLAUDE.md`** — updated commands, env vars, files table, added "Production deployment" section

#### Design decisions

- **Self-signed cert vs nip.io + Let's Encrypt**: self-signed chosen for simplicity — no external dependencies, works offline, no rate limits, valid 10 years.
- **Health check via `docker exec`** instead of external HTTP: in prod, no ports are exposed from the `web` container (only nginx exposes 80/443), so health check uses `docker-compose exec -T web curl http://localhost:8000/health/`.
- **No docker-compose.prod.yml overlay**: base `docker-compose.yml` IS the prod config. Dev uses `docker-compose.dev.yml` overlay on top.

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

## 2026-04-08 — Feature pack: curator role, daily report, reminders, rate config, stats

### What was done

Complete implementation of 9 features requested in one session.

#### Curator role

- `UserRole.CURATOR` added to `users.User`
- `IsCurator` aiogram filter; `IsActivatedWorker` updated to accept curators
- `CuratorCallback(prefix="cur")` — separate namespace for curator callbacks
- Curator main menu: My referrals, My invite codes, Stats, Withdrawal
- Curator can list, view, toggle, and create own invite keys (with ownership check — cannot access other curators' or admins' keys)
- On activation via curator's key: `referred_by` auto-set to curator
- `/start` branches correctly: admin → admin menu, curator → curator menu, worker → worker menu
- Admin can change role via user card: "🎓 Назначить куратором" / "👷 Назначить воркером"

#### Daily report + broadcast

- `DailyReport` model: unique per date, stores link/client_nick/client_rate/total_applications + computed rates
- `RateConfig` singleton: `worker_share`, `referral_share` Decimals, `compute(client_rate)` → dict
- 4-step FSM (link → client_nick → client_rate → total_applications → confirm)
- After confirm: `send_daily_broadcast_task.delay(report.id)` → Celery sends to all active workers + curators
- `broadcast_sent` flag prevents double-send

#### Admin reminders (Celery beat)

- 13:00 МСК (10:00 UTC) and 20:00 МСК (17:00 UTC): gentle reminder if no report yet
- Every 15 min after 23:01 МСК: urgent ВНИМАНИЕ! if no report yet
- All implemented with `zoneinfo.ZoneInfo("Europe/Moscow")` (no pytz)

#### Enhanced admin stats

- ASCII weekly bar chart (Mon → Sun, 8 `█` blocks scaled to max)
- Financial summary: income/debts per day and per week
- Workers + curators counts; top-1 by attracted_count

#### Withdrawal minimum

- `MIN_WITHDRAWAL_AMOUNT = Decimal("700")` in `WithdrawalService`
- Check in `WithdrawalService.create()` (service layer) AND at bot entry point (handler layer)
- User sees alert with current balance if below minimum

#### "База каналов" button

- `CHANNELS_DB_URL` setting with default Google Sheets URL
- URL button in worker main menu, worker profile, curator main menu

#### Settings → RateConfig

- Removed legacy global `rate_percent` FSM
- New `AdminSetRateConfigState` two-step FSM (worker_share% → referral_share%), with validation that sum ≤ 100%

#### Activation notifications

- On worker activation: all admins + key creator (if curator) receive bot notification
- Shows user name, telegram_id, username, and curator name

#### Migrations

- `users/0005_curator_role.py` — adds `curator` to choices
- `stats/0003_rateconfig_dailyreport.py` — creates `RateConfig` and `DailyReport`

#### Bug fixes applied during audit

- `start.py`: `send_curator_main_menu` was called with extra `channels_url` arg (TypeError) — fixed
- `keyboards.py`: curator withdrawal button used `CuratorCallback` (no handler) — fixed to `WorkerCallback`
- `tasks.py`: `pytz` not in dependencies — replaced with Python 3.11 `zoneinfo`
- `profile.py`: `get_profile_keyboard()` called without `channels_db_url` — fixed
- `curator/invites.py`: view/toggle/activations buttons used `AdminInviteCallback` with `IsAdmin()` handlers → curators couldn't interact — added full curator key management handlers with ownership check

---

## 2026-04-10 — Bug fixes, timezone, rate_percent removal, web stats dashboard

### What was done

#### Bug fixes

- **`stats/tasks.py`**: `check_missing_daily_report_task` referenced undefined `_URGENT_CACHE_KEY` → runtime `NameError` every 15 min. Removed the dead variable reference (per-design, sends repeat every 15 min without cache lock).
- **`broadcasts/tasks.py`**: Synchronous Django ORM calls (`BroadcastService.log_delivery`, `UserService.mark_blocked_bot`) were made directly inside `async def _deliver()`. In a sync Celery task using `asyncio.run()` this blocks the event loop and can cause connection pool issues. Refactored to collect results as plain tuples inside the async function and perform all ORM writes synchronously after `asyncio.run()` completes.
- **`stats/models.py`**: `DailyReport.date` default was `datetime.date.today` (OS local date = UTC in Docker). Changed to `timezone.localdate` so new reports default to Moscow date.

#### Moscow timezone everywhere

- `config/settings/base.py`: `TIME_ZONE = "UTC"` → `"Europe/Moscow"`, `CELERY_TIMEZONE = "UTC"` → `"Europe/Moscow"`
- Beat schedule: crontab hours updated from UTC offsets (10, 17) to Moscow time (13, 20) — simpler, correct with `CELERY_TIMEZONE = "Europe/Moscow"`
- `stats/services.py`: All `datetime.date.today()` calls replaced with `timezone.localdate()` (respects Django `TIME_ZONE`)
- `stats/models.py`: Same fix for the `DailyReport.date` field default

#### Removed `rate_percent` (ReferralSettings cleanup)

The `ReferralSettings` model with `rate_percent` was replaced by per-user `personal_rate`/`referral_rate` on the User model in the 2026-04-05 session. The old model was left in the DB.

- `apps/referrals/models.py`: Removed `ReferralSettings` class entirely
- `apps/referrals/services.py`: Removed `get_settings()` and `set_rate()` methods (only `ReferralLink`-related methods remain)
- `apps/referrals/admin.py`: Removed `ReferralSettingsAdmin`
- `apps/referrals/migrations/0002_remove_referralsettings.py`: Migration drops the table

#### Web stats dashboard

New URL: `/stats/` — protected by `staff_member_required` (Django superuser login at `/django-admin/`).

- `apps/stats/views.py`: `StatsDashboardView` — aggregates last 30 days of `DailyReport`, user counts, top workers, financial summary
- `templates/stats_dashboard.html`: Dark-theme dashboard with two Chart.js line charts (applications + finance), recent reports table, top-10 workers list, KPI strip
- `config/urls.py`: Added `path("stats/", StatsDashboardView.as_view(), ...)`

---

## 2026-04-10 (2) — Broadcast fixes, queue fix, landing redesign v2

### Audit findings and fixes

#### Broadcast bugs — root cause found and fixed

**Bug 1: `IntegrityError` → broadcast stuck in RUNNING forever**
- `BroadcastDeliveryLog` has `unique_together = [["broadcast", "user"]]`
- `BroadcastService.log_delivery()` used `objects.create()` — on any retry or duplicate run, this raises `IntegrityError`
- In PostgreSQL, an `IntegrityError` corrupts the active transaction → subsequent `Broadcast.objects.update(status=DONE)` also fails
- Result: broadcast stuck in `RUNNING` state, never completing
- Fix: replaced `objects.create()` with `objects.update_or_create()` in `apps/broadcasts/services.py:log_delivery()`

**Bug 2: Stats/reminder tasks never run — wrong Celery queue name**
- `CELERY_TASK_ROUTES` was routing `apps.stats.tasks.*` → `"celery"` (which IS the Celery default queue name)
- `docker-compose.yml` celery_worker command: `-Q default,broadcasts` — does NOT listen to the `"celery"` named queue
- Result: 13:00/20:00/23:01 reminder tasks piled up in `celery` queue, never consumed
- Fix: changed route to `"default"` and added `celery` to worker's `-Q` flag: `-Q celery,default,broadcasts`

**Bug 3: Dead link in Django admin sidebar**
- UNFOLD sidebar had link to `/django-admin/referrals/referralsettings/` — table was dropped in migration 0002
- Fix: removed the dead nav item from `config/settings/base.py` UNFOLD config

#### Landing page — v2 redesign

Complete rewrite of `templates/landing.html`:
- **Split-screen hero**: text column (left) + floating dashboard mockup (right)
- Dashboard mockup shows: KPI strip (заявки/доход/воркеры), bar chart, workers list with avatars and badges
- Two floating animated pills on top of mockup: "20 сообщений/сек" (green dot) and "248 получателей" (top-right)
- `/stats/` link added in 4 places: navbar (highlighted blue), hero buttons, dedicated stats-promo block, CTA section
- Stats-promo block: full-width call-to-action for the `/stats/` dashboard with description
- Adaptive: mockup hidden on mobile (<1024px), hero goes single-column, stats strip goes 2×2 grid
- `skill ui-ux-pro-max` — NOT available (Unknown skill). `Magic MCP / 21st.dev` — NOT configured. Full manual premium implementation used as fallback.

#### Files changed
- `apps/broadcasts/services.py` — `log_delivery`: `create` → `update_or_create`
- `config/settings/base.py` — stats task route `"celery"` → `"default"`; removed dead referralsettings UNFOLD link
- `docker-compose.yml` — celery worker `-Q default,broadcasts` → `-Q celery,default,broadcasts`
- `templates/landing.html` — full v2 redesign with split hero, dashboard mockup, stats links

#### How to verify broadcasts work
1. `docker-compose up -d`
2. In bot: create broadcast, confirm, launch
3. `docker logs spambotcontrol-celery_worker-1 | grep -E "Broadcast|completed|error"`
4. Should see: `"Broadcast N completed: N sent"`
5. Check `BroadcastDeliveryLog` in Django admin → entries with status `sent`

---

## 2026-04-10 (3) — Channel subscription gate (middleware)

### What was done

Added a global channel subscription check that blocks all non-admin users from using the bot unless they are subscribed to the required channel.

#### Architecture

Implemented as `SubscriptionMiddleware` in `apps/telegram_bot/subscription.py` — an aiogram `BaseMiddleware` registered as an **outer** middleware on the Dispatcher. This ensures:
- Every `message` and `callback_query` passes through the gate
- No handler-level copy-paste needed
- Admins bypass transparently (role check via `db_user.role == UserRole.ADMIN`)

#### Middleware execution order (LIFO)

aiogram outer middlewares execute in LIFO order (last registered = first executed).
Registration order in `bot.py`:

```
dp.message.outer_middleware(SubscriptionMiddleware())  ← 1st registered → runs 2nd
dp.message.outer_middleware(UserMiddleware())           ← 2nd registered → runs 1st
```

Actual call chain: `UserMiddleware → SubscriptionMiddleware → Filters → Handler`

This guarantees `db_user` is already set in `data` when `SubscriptionMiddleware` runs.

#### Member status handling

| Telegram status | Treated as |
|----------------|-----------|
| `creator` | subscribed ✅ |
| `administrator` | subscribed ✅ |
| `member` | subscribed ✅ |
| `restricted` | subscribed ✅ (still in channel) |
| `left` | not subscribed ❌ |
| `kicked` | not subscribed ❌ |
| unknown status | not subscribed ❌ (safe default) |
| API error | **fail-open** — user allowed, error logged |

#### Fail-open policy

If `getChatMember` returns a `TelegramAPIError` (bot not admin in channel, rate limit, etc.), the middleware **allows** the user through and logs an error. This prevents a misconfigured bot from locking out all users silently.

#### Configuration

Two new env vars:

```
SUBSCRIPTION_CHANNEL_ID=-1001234567890   # numeric ID, bot must be member of channel
SUBSCRIPTION_CHANNEL_URL=https://t.me/+srQfQzCb_6gyY2Rh  # shown in inline button
```

If `SUBSCRIPTION_CHANNEL_ID` is empty, the gate is disabled entirely (backward compat).

#### Files changed

- `apps/telegram_bot/subscription.py` — created: `check_channel_membership()` + `SubscriptionMiddleware`
- `apps/telegram_bot/bot.py` — registered `SubscriptionMiddleware` in correct LIFO order
- `config/settings/base.py` — added `SUBSCRIPTION_CHANNEL_ID` and `SUBSCRIPTION_CHANNEL_URL` settings
- `.env.example` — documented the two new vars with instructions on how to get numeric channel ID

#### Manual verification

**User not subscribed:**
1. Set `SUBSCRIPTION_CHANNEL_ID` in `.env`, restart
2. Leave the channel with a test account
3. Send any message or tap any button → should see "🔒 Доступ ограничен" with "📢 Подписаться на канал" button
4. Handler must NOT execute

**User subscribed:**
1. Join the channel with the test account
2. Send any message or tap any button → normal bot response, no gate

**Admin not subscribed:**
1. Leave the channel with the admin account
2. Perform any admin action → should work normally (gate bypassed)

**API error (bot not in channel):**
1. Remove bot from the channel
2. Any user action → bot should still work (fail-open), ERROR logged in `celery_worker` / `web` logs

<!-- Add new entries above this line in the same format -->
