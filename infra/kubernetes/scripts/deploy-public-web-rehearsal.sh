#!/usr/bin/env bash
set -euo pipefail
root_dir="$(cd "$(dirname "${BASH_SOURCE[0]}")/../../.." && pwd)"
infra_dir="${root_dir}/infra/kubernetes"
[[ -n "${KUBECONFIG:-}" ]] || { echo "KUBECONFIG is required." >&2; exit 1; }
crm_image="${CRM_IMAGE:?CRM_IMAGE must be an immutable amd64 image reference}"

"${infra_dir}/scripts/prepare-public-web-secrets.sh"
kubectl apply -f "${infra_dir}/platform/gateway/public-gateway.yaml"
sed "s|CRM_IMAGE_PLACEHOLDER|${crm_image}|g" "${infra_dir}/apps/crm/public-web.yaml" | kubectl apply -f -
kubectl rollout status deployment/gramly-public-web -n gramly-hello --timeout=10m
echo "Public landing/webhook rehearsal tier is ready; DNS and Telegram webhooks are unchanged."
