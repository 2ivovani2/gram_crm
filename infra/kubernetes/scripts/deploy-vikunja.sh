#!/usr/bin/env bash
set -euo pipefail
root_dir="$(cd "$(dirname "${BASH_SOURCE[0]}")/../../.." && pwd)"
infra_dir="${root_dir}/infra/kubernetes"
source "${infra_dir}/bootstrap/versions.env"
[[ -n "${KUBECONFIG:-}" ]] || { echo "KUBECONFIG is required." >&2; exit 1; }

if ! kubectl -n devtools get secret vikunja-postgres-app >/dev/null 2>&1; then
  db_password="$(openssl rand -base64 48 | tr -d '\n')"
  kubectl -n devtools create secret generic vikunja-postgres-app --from-literal=username=vikunja --from-literal=password="${db_password}" >/dev/null
  unset db_password
fi
if ! kubectl -n devtools get secret vikunja-runtime >/dev/null 2>&1; then
  jwt_secret="$(openssl rand -hex 32)"
  kubectl -n devtools create secret generic vikunja-runtime --from-literal=jwt-secret="${jwt_secret}" >/dev/null
  unset jwt_secret
fi

"${infra_dir}/apps/identity/configure-vikunja-oidc.sh"
kubectl apply -f "${infra_dir}/apps/devtools/vikunja-tls.yaml"
kubectl wait certificate/tasks-gramly-tech -n traefik-public --for=condition=Ready --timeout=10m
kubectl apply -f "${infra_dir}/platform/gateway/private-access-gateways.yaml"
kubectl wait gateway/gramly-collaboration -n traefik-collaboration --for=condition=Programmed --timeout=5m
kubectl apply -f "${infra_dir}/apps/devtools/vikunja-postgres.yaml"
kubectl wait cluster/vikunja-postgres -n devtools --for=condition=Ready --timeout=10m
sed "s|VIKUNJA_IMAGE_PLACEHOLDER|${VIKUNJA_IMAGE}|" "${infra_dir}/apps/devtools/vikunja.yaml" | kubectl apply -f -
kubectl rollout status deployment/vikunja -n devtools --timeout=10m
echo "Vikunja is ready on the collaboration access plane."
