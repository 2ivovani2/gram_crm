#!/usr/bin/env bash
set -euo pipefail

repo_root="$(cd "$(dirname "${BASH_SOURCE[0]}")/../.." && pwd)"
reconcile="${repo_root}/infra/kubernetes/apps/vpn/enable-authentik-group-sync.sh"
audit="${repo_root}/infra/kubernetes/apps/vpn/audit-user-access.sh"
dns="${repo_root}/infra/kubernetes/apps/vpn/ensure-private-app-records.sh"

bash -n "${reconcile}" "${audit}" "${dns}" \
  "${repo_root}/infra/kubernetes/apps/vpn/ensure-private-dns-zone.sh"

grep -F 'PEER_LOGIN_EXPIRATION_SECONDS:-604800' "${reconcile}" >/dev/null
grep -F '.settings.peer_login_expiration_enabled = true' "${reconcile}" >/dev/null
grep -F '.settings.peer_login_expiration = $peer_login_expiration' "${reconcile}" >/dev/null
grep -F '.settings.groups_propagation_enabled = true' "${reconcile}" >/dev/null

if grep -Eq -- '--request[[:space:]]+(POST|PUT|PATCH|DELETE)' "${audit}"; then
  echo "VPN audit must remain read-only." >&2
  exit 1
fi

for private_host in \
  crm.gramly.tech git.gramly.tech tasks.gramly.tech docs.gramly.tech \
  grafana.gramly.tech cluster.gramly.tech hello-admin.gramly.tech; do
  grep -F "\"domain\":\"${private_host}\"" "${dns}" >/dev/null
done
grep -F 'enabled: false' "${dns}" >/dev/null
grep -F 'legacy_zone_domain="gramly.tech"' "${dns}" >/dev/null

echo "VPN reconciliation and read-only audit contracts are valid."
