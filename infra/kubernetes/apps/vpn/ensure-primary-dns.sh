#!/usr/bin/env bash
set -euo pipefail

if [[ -z "${KUBECONFIG:-}" ]]; then
  echo "KUBECONFIG is not set; refusing to use an implicit cluster context." >&2
  exit 1
fi

for command_name in kubectl curl jq base64; do
  command -v "${command_name}" >/dev/null || {
    echo "${command_name} is required." >&2
    exit 1
  }
done

readonly api_url="https://vpn.gramly.tech/api"
readonly group_name="Gramly primary DNS"

task_tmp="$(mktemp -d)"
trap 'rm -rf "${task_tmp}"; unset api_token' EXIT
umask 077

api_token="$(kubectl get secret netbird-mgmt-api-key --namespace vpn \
  --output jsonpath='{.data.NB_API_KEY}' | base64 --decode)"
curl_config="${task_tmp}/curl.conf"
printf 'silent\nshow-error\nfail\nconnect-timeout = 10\nmax-time = 30\nretry = 3\nretry-all-errors\nheader = "Authorization: Token %s"\nheader = "Accept: application/json"\n' \
  "${api_token}" >"${curl_config}"
chmod 600 "${curl_config}"

curl --config "${curl_config}" "${api_url}/groups" >"${task_tmp}/groups.json"
all_group_id="$(jq -r '.[] | select(.name == "All") | .id' \
  "${task_tmp}/groups.json" | head -n 1)"
[[ -n "${all_group_id}" ]] || {
  echo "Required NetBird group All is missing." >&2
  exit 1
}

curl --config "${curl_config}" "${api_url}/dns/nameservers" \
  >"${task_tmp}/nameservers.json"
group_id="$(jq -r --arg name "${group_name}" \
  '.[] | select(.name == $name) | .id' "${task_tmp}/nameservers.json" | head -n 1)"

# NetBird supports one primary resolver per peer. Refuse to silently replace an
# independently managed primary group: that could redirect all employee DNS.
conflicting_primary="$(jq -r --arg name "${group_name}" \
  '.[] | select(.enabled == true and .primary == true and .name != $name) | .name' \
  "${task_tmp}/nameservers.json" | head -n 1)"
if [[ -n "${conflicting_primary}" ]]; then
  echo "Another enabled primary NetBird DNS group exists: ${conflicting_primary}." >&2
  exit 1
fi

jq -n --arg group_id "${all_group_id}" '{
  name: "Gramly primary DNS",
  description: "Managed public resolvers; NetBird custom zones remain authoritative for private Gramly services.",
  nameservers: [
    {ip: "8.8.8.8", ns_type: "udp", port: 53},
    {ip: "8.8.4.4", ns_type: "udp", port: 53}
  ],
  enabled: true,
  groups: [$group_id],
  primary: true,
  domains: [],
  search_domains_enabled: false
}' >"${task_tmp}/desired.json"

if [[ -n "${group_id}" ]]; then
  method=PUT
  endpoint="${api_url}/dns/nameservers/${group_id}"
else
  method=POST
  endpoint="${api_url}/dns/nameservers"
fi

curl --config "${curl_config}" --retry 0 \
  --header 'Content-Type: application/json' \
  --request "${method}" --data-binary "@${task_tmp}/desired.json" \
  "${endpoint}" >"${task_tmp}/updated.json"

jq -e --arg group_id "${all_group_id}" '
  .name == "Gramly primary DNS" and
  .enabled == true and
  .primary == true and
  .domains == [] and
  .search_domains_enabled == false and
  (.groups | index($group_id) != null) and
  (.nameservers | map(.ip) | sort == ["8.8.4.4", "8.8.8.8"])
' "${task_tmp}/updated.json" >/dev/null

curl --config "${curl_config}" "${api_url}/dns/settings" \
  >"${task_tmp}/dns-settings.json"
if jq -e --arg group_id "${all_group_id}" \
  '(.disabled_management_groups // []) | index($group_id) != null' \
  "${task_tmp}/dns-settings.json" >/dev/null; then
  jq --arg group_id "${all_group_id}" \
    '{disabled_management_groups: ((.disabled_management_groups // []) - [$group_id])}' \
    "${task_tmp}/dns-settings.json" >"${task_tmp}/dns-settings-update.json"
  curl --config "${curl_config}" --retry 0 \
    --header 'Content-Type: application/json' --request PUT \
    --data-binary "@${task_tmp}/dns-settings-update.json" \
    "${api_url}/dns/settings" >/dev/null
fi

echo "NetBird primary DNS is enabled for All; private custom zones remain authoritative."
