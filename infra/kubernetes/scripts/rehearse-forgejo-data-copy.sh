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
if [[ "${CONFIRM_FORGEJO_DOWNTIME:-false}" != "true" ]]; then
  echo "Set CONFIRM_FORGEJO_DOWNTIME=true after approving a brief source outage." >&2
  exit 1
fi

source_host="root@192.248.148.140"
source_container="forgejo"
mover_pod="forgejo-data-mover"

if [[ "$(kubectl get deployment forgejo --namespace devtools \
  --output jsonpath='{.spec.replicas}')" != "0" ]]; then
  echo "Target Forgejo must remain stopped during data copy." >&2
  exit 1
fi

sed "s|FORGEJO_IMAGE_PLACEHOLDER|${FORGEJO_IMAGE}|" \
  "${infra_dir}/apps/devtools/forgejo-data-mover.yaml" | kubectl apply --filename -
kubectl wait pod/${mover_pod} \
  --namespace devtools \
  --for=condition=Ready \
  --timeout=5m

if ! kubectl exec --namespace devtools "${mover_pod}" -- \
  sh -c 'test -z "$(find /data -mindepth 1 -maxdepth 1 ! -name lost+found -print -quit)"'; then
  echo "Target Forgejo PVC is not empty; refusing to overwrite it." >&2
  exit 1
fi

source_stopped=false
restore_source() {
  if [[ "${source_stopped}" == "true" ]]; then
    ssh -o BatchMode=yes "${source_host}" \
      "docker start ${source_container} >/dev/null" || true
  fi
}
trap restore_source EXIT

ssh -o BatchMode=yes "${source_host}" \
  "docker stop --time 30 ${source_container} >/dev/null"
source_stopped=true

ssh -o BatchMode=yes "${source_host}" \
  'tar --numeric-owner -C /root/forgejo/data -cf - .' | \
  kubectl exec --namespace devtools --stdin "${mover_pod}" -- \
    tar --numeric-owner -C /data -xf -

restore_source
source_stopped=false
trap - EXIT

kubectl exec --namespace devtools "${mover_pod}" -- \
  sh -c 'test -s /data/gitea/conf/app.ini && test -s /data/gitea/gitea.db'
kubectl exec --namespace devtools "${mover_pod}" -- \
  /sbin/su-exec git forgejo \
  --config /data/gitea/conf/app.ini \
  --work-path /data/gitea \
  doctor check --default
kubectl exec --namespace devtools "${mover_pod}" -- \
  /sbin/su-exec git sh -c \
  'find /data/git/repositories -mindepth 2 -maxdepth 2 -type d -name "*.git" -exec git -C {} fsck --strict \;'

source_status="$(ssh -o BatchMode=yes "${source_host}" \
  "docker inspect --format '{{.State.Running}}' ${source_container}")"
if [[ "${source_status}" != "true" ]]; then
  echo "Source Forgejo failed to restart." >&2
  exit 1
fi

echo "Forgejo data rehearsal completed; source is running and target remains stopped."
