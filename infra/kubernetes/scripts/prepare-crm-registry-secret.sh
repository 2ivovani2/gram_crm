#!/usr/bin/env bash
set -euo pipefail
[[ -n "${KUBECONFIG:-}" ]] || { echo "KUBECONFIG is required." >&2; exit 1; }
registry_server="${REGISTRY_SERVER:-https://index.docker.io/v1/}"
registry_username="${REGISTRY_USERNAME:?REGISTRY_USERNAME is required}"
registry_token="${REGISTRY_TOKEN:?REGISTRY_TOKEN is required}"

kubectl -n gramly-crm create secret docker-registry gramly-crm-registry \
  --docker-server="${registry_server}" \
  --docker-username="${registry_username}" \
  --docker-password="${registry_token}" \
  --dry-run=client -o yaml | kubectl apply -f - >/dev/null
unset registry_token
echo "CRM image pull secret is ready."
