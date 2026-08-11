#!/usr/bin/env bash
set -euo pipefail

root_dir="$(cd "$(dirname "${BASH_SOURCE[0]}")/../../.." && pwd)"
infra_dir="${root_dir}/infra/kubernetes"

# shellcheck source=/dev/null
source "${infra_dir}/bootstrap/versions.env"

if [[ -z "${KUBECONFIG:-}" ]]; then
  echo "KUBECONFIG is not set; refusing to use an implicit cluster context." >&2
  exit 1
fi

kubectl apply --filename "${infra_dir}/namespaces/namespaces.yaml"

helm repo add traefik https://traefik.github.io/charts --force-update >/dev/null
helm repo update traefik >/dev/null

helm upgrade --install traefik-business traefik/traefik \
  --version "${TRAEFIK_CHART_VERSION}" \
  --namespace traefik-business \
  --values "${infra_dir}/platform/gateway/traefik-business-values.yaml" \
  --wait \
  --timeout 10m

helm upgrade --install traefik-collaboration traefik/traefik \
  --version "${TRAEFIK_CHART_VERSION}" \
  --namespace traefik-collaboration \
  --values "${infra_dir}/platform/gateway/traefik-collaboration-values.yaml" \
  --wait \
  --timeout 10m

kubectl apply --filename \
  "${infra_dir}/platform/gateway/private-access-gateways.yaml"
kubectl apply --filename \
  "${infra_dir}/platform/gateway/private-access-network-resources.yaml"

kubectl wait gateway/gramly-business \
  --namespace traefik-business \
  --for=condition=Programmed \
  --timeout=5m
kubectl wait gateway/gramly-collaboration \
  --namespace traefik-collaboration \
  --for=condition=Programmed \
  --timeout=5m
kubectl wait networkresource/business-gateway \
  --namespace traefik-business \
  --for=condition=Ready \
  --timeout=5m
kubectl wait networkresource/collaboration-gateway \
  --namespace traefik-collaboration \
  --for=condition=Ready \
  --timeout=5m

echo "Business and collaboration private access gateways are ready."
