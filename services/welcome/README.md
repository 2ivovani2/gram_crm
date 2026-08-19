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

Commercial capabilities are protected by database feature flags. The
foundation migration creates Trial, Pro and Business plans with empty prices;
checkout and entitlement enforcement remain disabled until the staged release
explicitly enables them.

## Mini App API foundation

- `POST /api/v1/session/telegram` verifies Telegram `initData`, creates a
  short-lived HttpOnly session and returns a separate CSRF token;
- `GET /api/v1/me` returns the current owner and entitlement snapshot;
- `GET /api/v1/plans` exposes only fully configured prices;
- `POST /api/v1/session/logout` requires the session and `X-CSRF-Token`.

Only session-token and CSRF hashes are stored. Direct browser input and
`initDataUnsafe` are never trusted. The 14-day Business trial starts once, only
after the first customer bot webhook has been configured successfully.

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
