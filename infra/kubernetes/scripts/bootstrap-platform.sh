#!/usr/bin/env bash
set -euo pipefail

root_dir="$(cd "$(dirname "${BASH_SOURCE[0]}")/../../.." && pwd)"
infra_dir="$root_dir/infra/kubernetes"

# shellcheck source=/dev/null
source "$infra_dir/bootstrap/versions.env"

"$infra_dir/scripts/preflight.sh"

kubectl apply --server-side -f \
  "https://github.com/kubernetes-sigs/gateway-api/releases/download/${GATEWAY_API_VERSION}/standard-install.yaml"

kubectl apply -f "$infra_dir/namespaces/namespaces.yaml"

helm upgrade --install metrics-server metrics-server/metrics-server \
  --version "$METRICS_SERVER_CHART_VERSION" \
  --namespace kube-system \
  --values "$infra_dir/platform/metrics-server/values.yaml" \
  --wait --timeout 10m

helm upgrade --install cert-manager oci://quay.io/jetstack/charts/cert-manager \
  --version "$CERT_MANAGER_CHART_VERSION" \
  --namespace cert-manager \
  --create-namespace \
  --values "$infra_dir/platform/cert-manager/values.yaml" \
  --wait --timeout 10m

helm upgrade --install traefik-public traefik/traefik \
  --version "$TRAEFIK_CHART_VERSION" \
  --namespace traefik-public \
  --create-namespace \
  --values "$infra_dir/platform/gateway/traefik-public-values.yaml" \
  --wait --timeout 15m

helm upgrade --install traefik-private traefik/traefik \
  --version "$TRAEFIK_CHART_VERSION" \
  --namespace traefik-private \
  --create-namespace \
  --values "$infra_dir/platform/gateway/traefik-private-values.yaml" \
  --wait --timeout 15m

echo "Platform bootstrap complete."
kubectl get pods --all-namespaces
kubectl get services --all-namespaces
