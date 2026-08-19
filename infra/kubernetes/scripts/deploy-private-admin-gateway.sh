#!/usr/bin/env bash
set -euo pipefail

root_dir="$(cd "$(dirname "${BASH_SOURCE[0]}")/../../.." && pwd)"
infra_dir="${root_dir}/infra/kubernetes"
: "${KUBECONFIG:?Set KUBECONFIG to the production VKE kubeconfig}"

# shellcheck source=/dev/null
source "${infra_dir}/bootstrap/versions.env"
helm repo add traefik https://traefik.github.io/charts --force-update >/dev/null
helm repo update traefik >/dev/null
helm upgrade --install traefik-private traefik/traefik \
  --version "${TRAEFIK_CHART_VERSION}" \
  --namespace traefik-private \
  --values "${infra_dir}/platform/gateway/traefik-private-values.yaml" \
  --wait --timeout 10m
kubectl apply -f "${infra_dir}/platform/gateway/private-access-gateways.yaml"
kubectl wait gateway/gramly-infrastructure -n traefik-private \
  --for=condition=Programmed --timeout=5m

echo "Private infrastructure gateway watches the GramlyHello owner route."
