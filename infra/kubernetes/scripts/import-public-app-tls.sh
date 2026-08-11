#!/usr/bin/env bash
set -euo pipefail
root_dir="$(cd "$(dirname "${BASH_SOURCE[0]}")/../../.." && pwd)"
[[ -n "${KUBECONFIG:-}" ]] || { echo "KUBECONFIG is required." >&2; exit 1; }
certificate="${1:?Usage: import-public-app-tls.sh fullchain.pem privkey.pem}"
private_key="${2:?Usage: import-public-app-tls.sh fullchain.pem privkey.pem}"

kubectl -n traefik-public create secret tls gramly-tech-tls \
  --cert="${certificate}" --key="${private_key}" \
  --dry-run=client -o yaml | kubectl apply -f - >/dev/null
kubectl apply -f "${root_dir}/infra/kubernetes/platform/gateway/public-gateway.yaml" >/dev/null
kubectl wait gateway/gramly-public -n traefik-public --for=condition=Programmed --timeout=5m
echo "Public Gramly TLS listeners are ready."
