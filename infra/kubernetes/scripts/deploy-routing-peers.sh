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

kubectl get secret netbird-routing-peer --namespace vpn >/dev/null

sed "s|NETBIRD_CLIENT_IMAGE_PLACEHOLDER|${NETBIRD_CLIENT_IMAGE}|" \
  "${infra_dir}/apps/vpn/routing-peers.yaml" | kubectl apply --filename -

kubectl rollout status deployment/netbird-routing-peer --namespace vpn --timeout=5m
echo "NetBird routing peers are ready. Configure the 10.99.132.82/32 resource and access policy in NetBird."
