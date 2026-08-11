#!/usr/bin/env bash
set -euo pipefail
[[ -n "${KUBECONFIG:-}" ]] || { echo "KUBECONFIG is required." >&2; exit 1; }

copy_secret() {
  local source_name="$1" target_name="$2"
  kubectl -n gramly-crm get secret "${source_name}" -o json | jq \
    --arg name "${target_name}" \
    'del(.metadata.annotations,.metadata.creationTimestamp,.metadata.finalizers,.metadata.managedFields,.metadata.ownerReferences,.metadata.resourceVersion,.metadata.uid) | .metadata.name=$name | .metadata.namespace="gramly-hello"' | \
    kubectl apply -f - >/dev/null
}

copy_secret gramly-crm-runtime gramly-public-runtime
copy_secret gramly-crm-registry gramly-crm-registry
echo "Public web runtime and image pull secrets are ready."
