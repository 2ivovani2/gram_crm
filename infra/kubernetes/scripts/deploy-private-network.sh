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

kubectl get deployment netbird-operator --namespace vpn >/dev/null

# Desktop clients only install a deterministic managed resolver when NetBird
# has a primary nameserver group. Reconcile it before private zones/routes so a
# fresh connection does not depend on a later network-interface change.
"${infra_dir}/apps/vpn/ensure-primary-dns.sh"
"${infra_dir}/apps/vpn/ensure-private-dns-zone.sh"

sed "s|NETBIRD_ROUTING_IMAGE_PLACEHOLDER|${NETBIRD_ROUTING_IMAGE}|" \
  "${infra_dir}/apps/vpn/network-router.yaml" | kubectl apply --filename -

kubectl wait networkrouter/gramly-private \
  --namespace vpn \
  --for=condition=Ready \
  --timeout=10m

kubectl rollout status deployment/networkrouter-gramly-private \
  --namespace vpn \
  --timeout=10m

kubectl apply --filename \
  "${infra_dir}/platform/gateway/private-ingress-network-resource.yaml"
kubectl wait networkresource/traefik-private \
  --namespace traefik-private \
  --for=condition=Ready \
  --timeout=10m

echo "NetBird private NetworkRouter and admin-only ingress resource are ready."
