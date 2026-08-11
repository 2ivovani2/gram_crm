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

replicas="$(kubectl get deployment forgejo \
  --namespace devtools \
  --output jsonpath='{.spec.replicas}')"
if [[ "${replicas}" != "0" ]]; then
  echo "Forgejo target unexpectedly has ${replicas} replicas; refusing to continue." >&2
  exit 1
fi

echo "Forgejo target storage, stopped deployment, service, and HTTP route are prepared."
