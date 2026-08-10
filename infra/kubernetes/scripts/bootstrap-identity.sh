#!/usr/bin/env bash
set -euo pipefail

root_dir="$(cd "$(dirname "${BASH_SOURCE[0]}")/../../.." && pwd)"
infra_dir="$root_dir/infra/kubernetes"

# shellcheck source=/dev/null
source "$infra_dir/bootstrap/versions.env"

"$infra_dir/scripts/preflight.sh"

helm upgrade --install cloudnative-pg cnpg/cloudnative-pg \
  --version "$CLOUDNATIVE_PG_CHART_VERSION" \
  --namespace cnpg-system \
  --create-namespace \
  --values "$infra_dir/platform/database/cloudnative-pg-values.yaml" \
  --wait --timeout 10m

if ! kubectl -n identity get secret identity-postgres-app >/dev/null 2>&1; then
  "$infra_dir/apps/identity/create-secrets.sh"
fi

kubectl apply -f "$infra_dir/apps/identity/postgres.yaml"
kubectl -n identity wait cluster/identity-postgres \
  --for=condition=Ready --timeout=15m

helm upgrade --install authentik authentik/authentik \
  --version "$AUTHENTIK_CHART_VERSION" \
  --namespace identity \
  --values "$infra_dir/apps/identity/authentik-values.yaml" \
  --wait --timeout 15m

kubectl -n identity rollout status deployment/authentik-server --timeout=5m
kubectl -n identity rollout status deployment/authentik-worker --timeout=5m

"$infra_dir/apps/identity/ensure-admin-user.sh"

echo "Authentik is healthy inside the cluster. Public routing is a separate step."
