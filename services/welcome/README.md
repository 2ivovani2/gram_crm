# Gramly Welcome

Independent async Telegram event plane for GramlyHello. It owns customer bots,
channels, contacts and delivery state; Django CRM is a separate product and is
not used by this service.

## Guarantees in this stage

- webhook authentication uses both the unguessable path and Telegram secret;
- request bodies are bounded before JSON validation;
- a successful response means the update is committed to PostgreSQL;
- `(source_key, update_id)` makes Telegram retries idempotent;
- database outages return `503`, allowing Telegram to retry;
- workers claim batches with `FOR UPDATE SKIP LOCKED` and expiring leases;
- failed events use bounded exponential retry with jitter and end in `dead`;
- delayed deliveries live in PostgreSQL instead of broker memory;
- fair queue windows cap each source/bot inside a claim so a noisy customer
  cannot occupy the full worker batch;
- delivery and approval workers use fail-closed Valkey rate limits, stream
  S3 media through bounded temporary files, honour Telegram `retry_after` and
  rotate versioned bot-token encryption keys;
- event, delivery and approval queues expose depth, age, retries and worker
  liveness to Prometheus.

GramlyHello has two plans with the same product and infrastructure quotas:
Free and Business. Free appends one centrally managed advertising operation to
the end of each welcome chain and does not include channel rotation. Business
removes advertising and enables rotation. There is no time-limited trial. The
advertising creative is selected once per delivery, survives retries unchanged,
and records only confirmed Telegram impressions and tracked CTA clicks.

## Farewell and rotation

`chat_member` transitions from an active state to `left`/`kicked` create one
durable departure record. A published farewell flow uses the same per-operation
outbox as welcome content. If the person has never opened the customer bot, the
flow is closed as `unreachable`: Telegram does not allow the bot to initiate
that dialog.

Business channels with `can_invite_users` participate in the rotation pool. An
owner can mark up to seven own channels as priority; remaining positions are
randomly selected from the eligible paid pool. Gramly creates named Telegram
invite links and records an impression only after the recommendation is sent.
A conversion requires the exact invite link and a prior impression for the same
Telegram user and destination channel. Organic joins, the channel owner and
re-joins are not counted. Free owners never enter or receive rotation.

## Billing and referrals

Business can be paid for 30 days with Telegram Stars or a verified Crypto Pay
RUB invoice. Both checkout methods are disabled until their plan price and the
corresponding feature flag are configured. Crypto Pay is additionally split by
surface (`crypto_pay_bot_checkout` and `crypto_pay_mini_app_checkout`) so it can
be disabled inside Telegram without a release. The webhook endpoint is
`/welcome/payments/crypto/<WELCOME_CRYPTO_PAY_WEBHOOK_SECRET>/`; its HMAC is
checked over the raw body and the paid invoice is fetched from Crypto Pay again
before any financial mutation.

The first valid `ref_<opaque-code>` source is immutable. A candidate activates
only after connecting a customer bot and making the first confirmed Business
payment. Commission rates are snapshotted at 15%, 25% or 35% and stop exactly
one calendar year after that first payment. The RUB ledger is append-only at
both service and database level. Withdrawals reserve at least 1,000 RUB before
review; the billing worker uses the stable withdrawal `spend_id` for an
idempotent USDT transfer. A permanent failure or rejection restores the reserve
with a compensating entry. Renewal reminders are durable and retried at 7, 3
and 1 day before expiry.

Required runtime secrets are `WELCOME_CRYPTO_PAY_API_TOKEN` and a random
`WELCOME_CRYPTO_PAY_WEBHOOK_SECRET`. Use `https://testnet-pay.crypt.bot` until
the full payment smoke is complete; never commit provider tokens or invoice
payloads. The worker entry point is `welcome-worker-billing`.

## Mini App API foundation

- `POST /api/v1/session/telegram` verifies Telegram `initData`, creates a
  short-lived HttpOnly session and returns a separate CSRF token;
- `GET /api/v1/me` returns the current owner and entitlement snapshot;
- `GET /api/v1/plans` exposes only fully configured prices;
- `POST /api/v1/payments/crypto` creates an idempotent Mini App checkout;
- `GET /api/v1/referrals` and `GET /api/v1/withdrawals` expose partner data;
- `POST /api/v1/withdrawals` atomically reserves referral balance;
- `POST /api/v1/session/logout` requires the session and `X-CSRF-Token`.

Only session-token and CSRF hashes are stored. Direct browser input and
`initDataUnsafe` are never trusted. A Free subscription is assigned after the
first customer bot webhook is configured; a paid Business period temporarily
replaces it and automatically falls back to Free after expiry.

## Local verification

```bash
docker compose up -d welcome-postgres
docker compose run --rm welcome-migrate
docker compose up -d welcome-api
curl http://127.0.0.1:8080/health/live
curl http://127.0.0.1:8080/health/ready
```

The metrics endpoint is internal-only at `/metrics`.

## Staging migration gates

`ops/welcome/migrate_data.py` replaces only the explicitly confirmed target
database inside one transaction. The source connection is read-only and bot
tokens are decrypted with the legacy Django key before being encrypted with
the new versioned keyring. `ops/welcome/copy_media.py` is dry-run by default;
its execute mode verifies SHA-256 metadata and object size after every copy.

```bash
python ops/welcome/migrate_data.py \
  --confirm-target-database gramly_welcome_staging
python ops/welcome/copy_media.py
python ops/welcome/load_test.py \
  --url https://gramly.tech/welcome-staging/client/ID/SECRET/ \
  --source-key bot:ID \
  --rate 100 \
  --seconds 60 \
  --cleanup
```

Secrets are passed through environment variables and must never be placed on
the command line, committed, or printed. The load gate sends neutral Telegram
poll updates, verifies both persistence and worker completion, and `--cleanup`
deletes only the generated update-ID range. Set `WELCOME_LOAD_TEST_SECRET` and
`WELCOME_LOAD_TEST_DATABASE_URL` in the execution environment. The old Welcome
runtime has already been retired; this tooling remains for reproducible
validation and disaster recovery.
