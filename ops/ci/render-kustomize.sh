#!/usr/bin/env bash
set -euo pipefail

repo_root="$(cd "$(dirname "${BASH_SOURCE[0]}")/../.." && pwd)"

render_targets=(
  "infra/kubernetes/base/crm-web"
  "infra/kubernetes/base/crm-background"
  "infra/kubernetes/base/public-web"
  "infra/kubernetes/base/crm-migrations"
  "infra/kubernetes/base/welcome"
  "infra/kubernetes/base/welcome-runtime"
  "infra/kubernetes/base/welcome-autoscaling"
  "infra/kubernetes/base/welcome-migrations"
  "infra/kubernetes/overlays/staging"
  "infra/kubernetes/overlays/staging/welcome-migrations"
  "infra/kubernetes/overlays/production"
  "infra/kubernetes/overlays/production/migrations"
  "infra/kubernetes/overlays/production/welcome"
  "infra/kubernetes/overlays/production/welcome-migrations"
  "infra/kubernetes/overlays/production/welcome-cutover"
)

for target in "${render_targets[@]}"; do
  output="$(mktemp)"
  kubectl kustomize "${repo_root}/${target}" >"${output}"
  [[ -s "${output}" ]] || {
    echo "Kustomize rendered an empty manifest: ${target}" >&2
    exit 1
  }
  if grep -Eq '^kind:[[:space:]]+Secret[[:space:]]*$' "${output}"; then
    echo "Rendered application manifests must not contain Kubernetes Secrets: ${target}" >&2
    exit 1
  fi
  rm -f "${output}"
  echo "rendered ${target}"
done
