#!/usr/bin/env bash
set -euo pipefail

[[ -n "${KUBECONFIG:-}" ]] || {
  echo "KUBECONFIG is required." >&2
  exit 1
}

source_namespace="${1:-gramly-staging}"
source_secret="${2:-gramly-welcome-runtime}"
target_namespace=gramly-crm
target_secret=gramly-cnpg-backup-s3

runtime_secret="$(kubectl -n "${source_namespace}" get secret "${source_secret}" -o json)"
for key in WELCOME_S3_ACCESS_KEY_ID WELCOME_S3_SECRET_ACCESS_KEY; do
  jq -e --arg key "${key}" '.data[$key] | type == "string" and length > 0' \
    >/dev/null <<<"${runtime_secret}" || {
      echo "${source_namespace}/${source_secret} is missing ${key}." >&2
      exit 1
    }
done

# Keep credentials base64-encoded while copying them between namespaces. The
# Vultr EWR S3 endpoint reports the signing region as `us`.
jq \
  --arg namespace "${target_namespace}" \
  --arg name "${target_secret}" \
  --arg region "$(printf %s us | base64)" \
  '{
    apiVersion: "v1",
    kind: "Secret",
    metadata: {
      namespace: $namespace,
      name: $name,
      labels: {"app.kubernetes.io/name": "gramly-cnpg-backup"}
    },
    type: "Opaque",
    data: {
      AWS_ACCESS_KEY_ID: .data.WELCOME_S3_ACCESS_KEY_ID,
      AWS_SECRET_ACCESS_KEY: .data.WELCOME_S3_SECRET_ACCESS_KEY,
      AWS_REGION: $region
    }
  }' <<<"${runtime_secret}" | kubectl apply -f - >/dev/null

unset runtime_secret
echo "CNPG backup credentials are ready in ${target_namespace}/${target_secret}."
