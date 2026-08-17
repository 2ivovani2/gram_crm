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
- delivery claims take at most one item per bot per batch.

The delivery consumer is disabled in normal compose and production manifests
until the next MR migrates encrypted tokens and media, implements streaming S3
delivery, and passes staging smoke/load gates. Do not route `/welcome/*` to this
service before that cutover gate.

## Local verification

```bash
docker compose up -d welcome-postgres
docker compose run --rm welcome-migrate
docker compose up -d welcome-api
curl http://127.0.0.1:8080/health/live
curl http://127.0.0.1:8080/health/ready
```

The metrics endpoint is internal-only at `/metrics`.
