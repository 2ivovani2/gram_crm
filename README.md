# Gramly Platform

Gramly is a monorepo for the internal CRM, public product site and Telegram
automation services.

## Layout

- `services/crm` — Django CRM, control bot and CRM background jobs.
- `services/welcome` — standalone Gramly Welcome service (introduced in the
  next migration phase).
- `frontend` — Vite sources for the landing and CRM interface.
- `infra` — reproducible cloud and Kubernetes configuration.
- `ops` — operational migration, smoke-test and release utilities.

## Local development

```bash
cp .env.example .env
make dev
```

The CRM is available at `http://localhost:8000`, PostgreSQL at `localhost:5432`,
Valkey at `localhost:6379`, and the MinIO console at `localhost:9001`.
Use `make dev-tunnel` only when Telegram must reach a local webhook.

## Checks

```bash
make install
make build
make check
make lint
make typecheck
make security
make manifests
make test
make test-frontend
```

Never edit or squash historical Django migrations after they have reached
production. New schema changes require a new migration and a successful
backup before the production migration Job runs.

## Releases

Changes are merged through protected `main`. CI builds immutable images,
deploys staging after merge and requires an explicit production promotion.
Secrets, kubeconfigs, generated static files and local service data never
belong in Git.

Forgejo Actions runs backend, frontend, manifest, security and production-image
build gates for every pull request. A green image build does not publish or
deploy anything; immutable publishing and staging promotion are introduced in
the dedicated release phase.
