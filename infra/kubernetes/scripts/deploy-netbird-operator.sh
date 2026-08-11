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

kubectl get secret netbird-mgmt-api-key --namespace vpn >/dev/null

helm upgrade --install netbird-operator \
  oci://ghcr.io/netbirdio/helm-charts/netbird-operator \
  --version "${NETBIRD_OPERATOR_CHART_VERSION}" \
  --namespace vpn \
  --values "${infra_dir}/apps/vpn/operator-values.yaml" \
  --wait \
  --timeout 10m

kubectl rollout status deployment/netbird-operator --namespace vpn --timeout=5m
echo "NetBird Kubernetes Operator is ready."
