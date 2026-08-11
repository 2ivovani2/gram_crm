#!/usr/bin/env bash
set -euo pipefail
root_dir="$(cd "$(dirname "${BASH_SOURCE[0]}")/../../.." && pwd)"
infra_dir="${root_dir}/infra/kubernetes"
[[ -n "${KUBECONFIG:-}" ]] || { echo "KUBECONFIG is required." >&2; exit 1; }
crm_image="${CRM_IMAGE:?CRM_IMAGE must be an immutable amd64 image reference}"

kubectl apply -f "${infra_dir}/apps/crm/postgres.yaml"
kubectl apply -f "${infra_dir}/apps/crm/data-services.yaml"
kubectl wait cluster/gramly-crm-postgres -n gramly-crm --for=condition=Ready --timeout=15m
kubectl rollout status deployment/gramly-crm-valkey -n gramly-crm --timeout=10m
kubectl rollout status deployment/gramly-crm-minio -n gramly-crm --timeout=10m

# Background schedulers deliberately stay off during rehearsal: the old server
# remains the only system allowed to accrue balances or send Telegram messages.
kubectl -n gramly-crm delete job gramly-crm-migrate --ignore-not-found=true >/dev/null
sed "s|CRM_IMAGE_PLACEHOLDER|${crm_image}|g" "${infra_dir}/apps/crm/migrate-job.yaml" | kubectl apply -f -
kubectl wait job/gramly-crm-migrate -n gramly-crm --for=condition=Complete --timeout=10m
sed "s|CRM_IMAGE_PLACEHOLDER|${crm_image}|g" "${infra_dir}/apps/crm/app.yaml" | kubectl apply -f -
kubectl rollout status deployment/gramly-crm-web -n gramly-crm --timeout=10m
echo "CRM rehearsal web tier is ready; worker and beat remain intentionally disabled."
