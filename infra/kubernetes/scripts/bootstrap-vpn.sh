#!/usr/bin/env bash
set -euo pipefail

root_dir="$(cd "$(dirname "${BASH_SOURCE[0]}")/../../.." && pwd)"
infra_dir="$root_dir/infra/kubernetes"

# shellcheck source=/dev/null
source "$infra_dir/bootstrap/versions.env"

"$infra_dir/scripts/preflight.sh"

if ! kubectl -n vpn get secret netbird-config >/dev/null 2>&1; then
  "$infra_dir/apps/vpn/create-secrets.sh"
fi

kubectl apply -f "$infra_dir/apps/vpn/database.yaml"
kubectl -n identity wait database/identity-postgres-netbird \
  --for=jsonpath='{.status.applied}'=true --timeout=5m

sed \
  -e "s|NETBIRD_SERVER_IMAGE_PLACEHOLDER|$NETBIRD_SERVER_IMAGE|" \
  -e "s|NETBIRD_DASHBOARD_IMAGE_PLACEHOLDER|$NETBIRD_DASHBOARD_IMAGE|" \
  "$infra_dir/apps/vpn/netbird.yaml" \
  | kubectl apply -f -

kubectl -n vpn rollout status deployment/netbird-server --timeout=10m
kubectl -n vpn rollout status deployment/netbird-dashboard --timeout=5m

echo "NetBird is healthy inside the cluster. Public routing is a separate step."
