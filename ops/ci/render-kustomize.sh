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
  "infra/kubernetes/overlays/production/welcome-pause"
  "infra/kubernetes/overlays/production/welcome-cutover"
  "infra/kubernetes/platform/gateway"
  "infra/kubernetes/apps/observability"
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

bash -n \
  "${repo_root}/infra/kubernetes/apps/identity/configure-welcome-admin-oidc.sh" \
  "${repo_root}/infra/kubernetes/apps/vpn/ensure-private-dns-zone.sh" \
  "${repo_root}/infra/kubernetes/apps/vpn/ensure-private-app-records.sh" \
  "${repo_root}/infra/kubernetes/apps/vpn/enable-authentik-group-sync.sh" \
  "${repo_root}/infra/kubernetes/apps/vpn/audit-user-access.sh" \
  "${repo_root}/infra/kubernetes/scripts/deploy-private-admin-gateway.sh" \
  "${repo_root}/ops/welcome/production_smoke.sh" \
  "${repo_root}/ops/welcome/release_production.sh"

"${repo_root}/ops/ci/check-vpn-contracts.sh"
