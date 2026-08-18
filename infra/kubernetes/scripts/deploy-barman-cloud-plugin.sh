#!/usr/bin/env bash
set -euo pipefail

[[ -n "${KUBECONFIG:-}" ]] || {
  echo "KUBECONFIG is required." >&2
  exit 1
}

infra_dir="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
# shellcheck disable=SC1091
source "${infra_dir}/bootstrap/versions.env"

[[ "${BARMAN_CLOUD_PLUGIN_VERSION:-}" =~ ^v[0-9]+\.[0-9]+\.[0-9]+$ ]] || {
  echo "BARMAN_CLOUD_PLUGIN_VERSION must be pinned in bootstrap/versions.env." >&2
  exit 1
}

manifest_url="https://github.com/cloudnative-pg/plugin-barman-cloud/releases/download/${BARMAN_CLOUD_PLUGIN_VERSION}/manifest.yaml"
kubectl apply --server-side -f "${manifest_url}"
kubectl -n cnpg-system rollout status deployment/barman-cloud --timeout=5m
kubectl get crd objectstores.barmancloud.cnpg.io >/dev/null

echo "Barman Cloud Plugin ${BARMAN_CLOUD_PLUGIN_VERSION} is ready."
