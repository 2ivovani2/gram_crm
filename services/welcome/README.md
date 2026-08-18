# Gramly Welcome

Async Telegram event plane. This service is introduced in parallel with the
legacy Django consumer; production routing remains on Django until the staged
data migration and explicit cutover.

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

The delivery consumer is available only through Compose's `welcome-cutover`
profile and the isolated staging overlay. Production routing remains on Django
until data/media migration and the explicit cutover gate pass. Do not route
production `/welcome/*` to this service before that gate.

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
`WELCOME_LOAD_TEST_DATABASE_URL` in the execution environment. Keep the legacy
consumer authoritative until the final delta copy during the approved
maintenance window.
