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

sed "s|FORGEJO_IMAGE_PLACEHOLDER|${FORGEJO_IMAGE}|" \
  "${infra_dir}/apps/devtools/forgejo.yaml" | kubectl apply --filename -
kubectl wait persistentvolumeclaim/forgejo-data \
  --namespace devtools \
  --for=jsonpath='{.status.phase}'=Bound \
  --timeout=5m
kubectl rollout status deployment/forgejo \
  --namespace devtools \
  --timeout=5m

echo "Forgejo is deployed on the collaboration access plane."
