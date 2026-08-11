#!/usr/bin/env bash
set -euo pipefail
root_dir="$(cd "$(dirname "${BASH_SOURCE[0]}")/../../.." && pwd)"
infra_dir="${root_dir}/infra/kubernetes"
source "${infra_dir}/bootstrap/versions.env"
[[ -n "${KUBECONFIG:-}" ]] || { echo "KUBECONFIG is required." >&2; exit 1; }

if ! kubectl -n devtools get secret outline-postgres-app >/dev/null 2>&1; then
  db_password="$(openssl rand -hex 32)"
  kubectl -n devtools create secret generic outline-postgres-app \
    --from-literal=username=outline --from-literal=password="${db_password}" >/dev/null
else
  db_password="$(kubectl -n devtools get secret outline-postgres-app -o jsonpath='{.data.password}' | base64 --decode)"
fi

if ! kubectl -n devtools get secret outline-valkey-config >/dev/null 2>&1; then
  redis_password="$(openssl rand -hex 32)"
  valkey_config="$(printf 'bind 0.0.0.0\nprotected-mode yes\nport 6379\ndir /data\nappendonly yes\nappendfsync everysec\nrequirepass %s\n' "${redis_password}")"
  kubectl -n devtools create secret generic outline-valkey-config \
    --from-literal=valkey.conf="${valkey_config}" >/dev/null
  unset valkey_config
else
  redis_password="$(kubectl -n devtools get secret outline-valkey-config -o jsonpath='{.data.valkey\.conf}' | base64 --decode | sed -n 's/^requirepass //p')"
fi

if ! kubectl -n devtools get secret outline-runtime >/dev/null 2>&1; then
  kubectl -n devtools create secret generic outline-runtime \
    --from-literal=SECRET_KEY="$(openssl rand -hex 32)" \
    --from-literal=UTILS_SECRET="$(openssl rand -hex 32)" \
    --from-literal=DATABASE_URL="postgres://outline:${db_password}@outline-postgres-rw.devtools.svc.cluster.local:5432/outline" \
    --from-literal=REDIS_URL="redis://:${redis_password}@outline-valkey.devtools.svc.cluster.local:6379" >/dev/null
fi
unset db_password redis_password

"${infra_dir}/apps/identity/configure-outline-oidc.sh"
kubectl apply -f "${infra_dir}/apps/devtools/outline-postgres.yaml"
kubectl wait cluster/outline-postgres -n devtools --for=condition=Ready --timeout=10m
sed -e "s|OUTLINE_IMAGE_PLACEHOLDER|${OUTLINE_IMAGE}|g" \
    -e "s|VALKEY_IMAGE_PLACEHOLDER|${VALKEY_IMAGE}|g" \
    "${infra_dir}/apps/devtools/outline.yaml" | kubectl apply -f -
kubectl rollout status deployment/outline-valkey -n devtools --timeout=10m
kubectl rollout status deployment/outline -n devtools --timeout=10m
kubectl apply -f "${infra_dir}/apps/devtools/outline-tls.yaml"
kubectl wait certificate/docs-gramly-tech -n traefik-public --for=condition=Ready --timeout=10m
kubectl apply -f "${infra_dir}/platform/gateway/private-access-gateways.yaml"
kubectl wait gateway/gramly-collaboration -n traefik-collaboration --for=condition=Programmed --timeout=5m
kubectl rollout restart deployment/traefik-collaboration -n traefik-collaboration
kubectl rollout status deployment/traefik-collaboration -n traefik-collaboration --timeout=5m
"${infra_dir}/apps/vpn/ensure-private-app-records.sh"
echo "Outline is ready on the collaboration access plane."
