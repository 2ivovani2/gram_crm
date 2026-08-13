#!/usr/bin/env bash
set -euo pipefail

if [[ -z "${KUBECONFIG:-}" ]]; then
  echo "KUBECONFIG is not set; refusing to use an implicit cluster context." >&2
  exit 1
fi

for command_name in kubectl curl jq; do
  command -v "${command_name}" >/dev/null || {
    echo "${command_name} is required." >&2
    exit 1
  }
done

api_url="https://vpn.gramly.tech/api"
zone_domain="gramly.tech"
zone_name="${zone_domain}"
api_token="$(kubectl get secret netbird-mgmt-api-key \
  --namespace vpn \
  --output jsonpath='{.data.NB_API_KEY}' | base64 --decode)"

task_tmp="$(mktemp -d)"
trap 'rm -rf "${task_tmp}"; unset api_token' EXIT

curl_config="${task_tmp}/curl.conf"
printf 'silent\nshow-error\nfail\nconnect-timeout = 10\nmax-time = 30\nretry = 3\nretry-all-errors\nheader = "Authorization: Token %s"\nheader = "Accept: application/json"\n' \
  "${api_token}" >"${curl_config}"
chmod 600 "${curl_config}"

distribution_group_names='[
  "All"
]'

curl --config "${curl_config}" "${api_url}/groups" >"${task_tmp}/groups.json"
missing_groups="$(jq -r --argjson names "${distribution_group_names}" '
  ([.[].name]) as $existing
  | $names[] as $wanted
  | select(($existing | index($wanted)) == null)
  | $wanted
' "${task_tmp}/groups.json")"
if [[ -n "${missing_groups}" ]]; then
  echo "Missing required NetBird groups: ${missing_groups//$'\n'/, }." >&2
  exit 1
fi

distribution_groups="$(jq -cer --argjson names "${distribution_group_names}" '
  [.[] | . as $group | select($names | index($group.name)) | $group.id] | unique
' "${task_tmp}/groups.json")"

curl --config "${curl_config}" "${api_url}/dns/zones" >"${task_tmp}/zones.json"
zone_id="$(jq -r --arg domain "${zone_domain}" \
  '.[] | select(.domain == $domain) | .id' "${task_tmp}/zones.json" | head -n 1)"

jq -n \
  --arg name "${zone_name}" \
  --arg domain "${zone_domain}" \
  --argjson distribution_groups "${distribution_groups}" \
  '{name: $name, domain: $domain, enabled: true, enable_search_domain: false,
    distribution_groups: $distribution_groups}' >"${task_tmp}/zone.json"

if [[ -n "${zone_id}" ]]; then
  curl --config "${curl_config}" \
    --retry 0 \
    --header 'Content-Type: application/json' \
    --request PUT \
    --data-binary "@${task_tmp}/zone.json" \
    "${api_url}/dns/zones/${zone_id}" >/dev/null
  echo "Updated NetBird private DNS zone ${zone_domain}."
else
  curl --config "${curl_config}" \
    --retry 0 \
    --header 'Content-Type: application/json' \
    --request POST \
    --data-binary "@${task_tmp}/zone.json" \
    "${api_url}/dns/zones" >/dev/null
  echo "Created NetBird private DNS zone ${zone_domain}."
fi

echo "The private zone is distributed to approved peers; record contents are reconciled separately."
