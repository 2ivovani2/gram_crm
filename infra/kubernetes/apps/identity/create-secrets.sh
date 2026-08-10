#!/usr/bin/env bash
set -euo pipefail

if [[ -z "${KUBECONFIG:-}" ]]; then
  echo "KUBECONFIG is not set; refusing to use an implicit cluster context." >&2
  exit 1
fi

if kubectl -n identity get secret identity-postgres-app >/dev/null 2>&1 \
  || kubectl -n identity get secret authentik-runtime >/dev/null 2>&1; then
  echo "Identity secrets already exist; refusing to rotate them implicitly." >&2
  exit 1
fi

db_password="$(openssl rand -hex 32)"
secret_key="$(openssl rand -hex 64)"
bootstrap_password="$(openssl rand -hex 16)"
bootstrap_token="$(openssl rand -hex 32)"

kubectl -n identity create secret generic identity-postgres-app \
  --type=kubernetes.io/basic-auth \
  --from-literal=username=authentik \
  --from-literal=password="$db_password"

kubectl -n identity create secret generic authentik-runtime \
  --from-literal=AUTHENTIK_SECRET_KEY="$secret_key" \
  --from-literal=AUTHENTIK_POSTGRESQL__PASSWORD="$db_password" \
  --from-literal=AUTHENTIK_BOOTSTRAP_EMAIL=avyaroslavskiy@miem.hse.ru \
  --from-literal=AUTHENTIK_BOOTSTRAP_PASSWORD="$bootstrap_password" \
  --from-literal=AUTHENTIK_BOOTSTRAP_TOKEN="$bootstrap_token" \
  --from-literal=GRAMLY_INITIAL_ADMIN_PASSWORD="$bootstrap_password"

unset db_password secret_key bootstrap_password bootstrap_token
echo "Identity secrets created. Values were not written to disk or stdout."
