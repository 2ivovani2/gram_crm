#!/usr/bin/env bash
set -euo pipefail

repo_root="$(cd "$(dirname "${BASH_SOURCE[0]}")/../.." && pwd)"
reconcile="${repo_root}/infra/kubernetes/apps/vpn/enable-authentik-group-sync.sh"
audit="${repo_root}/infra/kubernetes/apps/vpn/audit-user-access.sh"

bash -n "${reconcile}" "${audit}"

grep -F 'PEER_LOGIN_EXPIRATION_SECONDS:-604800' "${reconcile}" >/dev/null
grep -F '.settings.peer_login_expiration_enabled = true' "${reconcile}" >/dev/null
grep -F '.settings.peer_login_expiration = $peer_login_expiration' "${reconcile}" >/dev/null
grep -F '.settings.groups_propagation_enabled = true' "${reconcile}" >/dev/null

if grep -Eq -- '--request[[:space:]]+(POST|PUT|PATCH|DELETE)' "${audit}"; then
  echo "VPN audit must remain read-only." >&2
  exit 1
fi

echo "VPN reconciliation and read-only audit contracts are valid."
