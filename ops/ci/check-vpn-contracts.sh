#!/usr/bin/env bash
set -euo pipefail

repo_root="$(cd "$(dirname "${BASH_SOURCE[0]}")/../.." && pwd)"
reconcile="${repo_root}/infra/kubernetes/apps/vpn/enable-authentik-group-sync.sh"
audit="${repo_root}/infra/kubernetes/apps/vpn/audit-user-access.sh"
dns="${repo_root}/infra/kubernetes/apps/vpn/ensure-private-app-records.sh"
primary_dns="${repo_root}/infra/kubernetes/apps/vpn/ensure-primary-dns.sh"
hello_admin_audit="${repo_root}/infra/kubernetes/apps/identity/audit-welcome-admin-access.sh"

bash -n "${reconcile}" "${audit}" "${dns}" "${primary_dns}" "${hello_admin_audit}" \
  "${repo_root}/infra/kubernetes/apps/vpn/ensure-private-dns-zone.sh"

grep -F 'PEER_LOGIN_EXPIRATION_SECONDS:-604800' "${reconcile}" >/dev/null
grep -F '.settings.peer_login_expiration_enabled = true' "${reconcile}" >/dev/null
grep -F '.settings.peer_login_expiration = $peer_login_expiration' "${reconcile}" >/dev/null
grep -F '.settings.groups_propagation_enabled = true' "${reconcile}" >/dev/null

if grep -Eq -- '--request[[:space:]]+(POST|PUT|PATCH|DELETE)' "${audit}"; then
  echo "VPN audit must remain read-only." >&2
  exit 1
fi
if grep -Eq -- '--request[[:space:]]+(POST|PUT|PATCH|DELETE)' "${hello_admin_audit}"; then
  echo "Hello Admin audit must remain read-only." >&2
  exit 1
fi

for private_host in \
  crm.gramly.tech git.gramly.tech tasks.gramly.tech docs.gramly.tech \
  grafana.gramly.tech cluster.gramly.tech hello-admin.gramly.tech; do
  grep -F "\"domain\":\"${private_host}\"" "${dns}" >/dev/null
done
grep -F 'enabled: false' "${dns}" >/dev/null
grep -F 'legacy_zone_domain="gramly.tech"' "${dns}" >/dev/null

grep -F 'name: "Gramly primary DNS"' "${primary_dns}" >/dev/null
grep -F '{ip: "8.8.8.8", ns_type: "udp", port: 53}' "${primary_dns}" >/dev/null
grep -F '{ip: "8.8.4.4", ns_type: "udp", port: 53}' "${primary_dns}" >/dev/null
grep -F 'primary: true' "${primary_dns}" >/dev/null
grep -F 'groups: [$group_id]' "${primary_dns}" >/dev/null
grep -F 'Another enabled primary NetBird DNS group exists' "${primary_dns}" >/dev/null

echo "VPN reconciliation and read-only audit contracts are valid."
