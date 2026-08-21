#!/usr/bin/env bash
set -euo pipefail

if [[ -z "${KUBECONFIG:-}" ]]; then
  echo "KUBECONFIG is not set; refusing to use an implicit cluster context." >&2
  exit 1
fi

for command_name in kubectl curl jq base64; do
  command -v "${command_name}" >/dev/null || exit 1
done

readonly api_url="https://vpn.gramly.tech/api"
readonly legacy_zone_domain="gramly.tech"
readonly desired_zones='[
  {"domain":"crm.gramly.tech","content":"10.99.132.83"},
  {"domain":"git.gramly.tech","content":"10.99.132.84"},
  {"domain":"tasks.gramly.tech","content":"10.99.132.84"},
  {"domain":"docs.gramly.tech","content":"10.99.132.84"},
  {"domain":"grafana.gramly.tech","content":"10.99.132.82"},
  {"domain":"cluster.gramly.tech","content":"10.99.132.82"},
  {"domain":"hello-admin.gramly.tech","content":"10.99.132.82"}
]'

api_token="$(kubectl get secret netbird-mgmt-api-key --namespace vpn \
  --output jsonpath='{.data.NB_API_KEY}' | base64 --decode)"
task_tmp="$(mktemp -d)"
trap 'rm -rf "${task_tmp}"; unset api_token' EXIT
umask 077

curl_config="${task_tmp}/curl.conf"
printf 'silent\nshow-error\nfail\nconnect-timeout = 10\nmax-time = 30\nretry = 3\nretry-all-errors\nheader = "Authorization: Token %s"\nheader = "Accept: application/json"\n' \
  "${api_token}" >"${curl_config}"
chmod 600 "${curl_config}"

curl --config "${curl_config}" "${api_url}/groups" >"${task_tmp}/groups.json"
all_group_id="$(jq -r '.[] | select(.name == "All") | .id' "${task_tmp}/groups.json" | head -n 1)"
[[ -n "${all_group_id}" ]] || {
  echo "Required NetBird group All is missing." >&2
  exit 1
}

curl --config "${curl_config}" "${api_url}/dns/zones" >"${task_tmp}/zones.json"

while IFS= read -r desired; do
  domain="$(jq -r '.domain' <<<"${desired}")"
  content="$(jq -r '.content' <<<"${desired}")"
  zone_id="$(jq -r --arg domain "${domain}" \
    '.[] | select(.domain == $domain) | .id' "${task_tmp}/zones.json" | head -n 1)"

  jq -n \
    --arg domain "${domain}" \
    --arg group_id "${all_group_id}" \
    '{name: $domain, domain: $domain, enabled: true, enable_search_domain: false,
      distribution_groups: [$group_id]}' >"${task_tmp}/zone.json"

  if [[ -n "${zone_id}" ]]; then
    curl --config "${curl_config}" --retry 0 \
      --header 'Content-Type: application/json' \
      --request PUT --data-binary "@${task_tmp}/zone.json" \
      "${api_url}/dns/zones/${zone_id}" >"${task_tmp}/zone-response.json"
  else
    curl --config "${curl_config}" --retry 0 \
      --header 'Content-Type: application/json' \
      --request POST --data-binary "@${task_tmp}/zone.json" \
      "${api_url}/dns/zones" >"${task_tmp}/zone-response.json"
    zone_id="$(jq -er '.id' "${task_tmp}/zone-response.json")"
  fi

  curl --config "${curl_config}" \
    "${api_url}/dns/zones/${zone_id}/records" >"${task_tmp}/records.json"
  record_id="$(jq -r --arg domain "${domain}" \
    '.[] | select(.name == $domain and .type == "A") | .id' \
    "${task_tmp}/records.json" | head -n 1)"
  jq -n --arg name "${domain}" --arg content "${content}" \
    '{name: $name, type: "A", content: $content, ttl: 60}' \
    >"${task_tmp}/record.json"

  if [[ -n "${record_id}" ]]; then
    record_method=PUT
    record_endpoint="${api_url}/dns/zones/${zone_id}/records/${record_id}"
  else
    record_method=POST
    record_endpoint="${api_url}/dns/zones/${zone_id}/records"
  fi
  curl --config "${curl_config}" --retry 0 \
    --header 'Content-Type: application/json' \
    --request "${record_method}" --data-binary "@${task_tmp}/record.json" \
    "${record_endpoint}" >/dev/null
  echo "Reconciled exact private DNS zone ${domain} -> ${content}."
done < <(jq -c '.[]' <<<"${desired_zones}")

# The former parent zone intercepted every gramly.tech query. A second VPN can
# then win the resolver race and send private applications to the public edge.
# Keep the zone as a rollback artifact, but disable it only after all exact
# zones and records have been reconciled successfully.
legacy_zone="$(jq -c --arg domain "${legacy_zone_domain}" \
  '.[] | select(.domain == $domain)' "${task_tmp}/zones.json" | head -n 1)"
if [[ -n "${legacy_zone}" ]]; then
  legacy_zone_id="$(jq -r '.id' <<<"${legacy_zone}")"
  jq -n \
    --arg name "$(jq -r '.name' <<<"${legacy_zone}")" \
    --arg domain "${legacy_zone_domain}" \
    --argjson distribution_groups "$(jq -c '.distribution_groups // []' <<<"${legacy_zone}")" \
    '{name: $name, domain: $domain, enabled: false, enable_search_domain: false,
      distribution_groups: $distribution_groups}' >"${task_tmp}/legacy-zone.json"
  curl --config "${curl_config}" --retry 0 \
    --header 'Content-Type: application/json' \
    --request PUT --data-binary "@${task_tmp}/legacy-zone.json" \
    "${api_url}/dns/zones/${legacy_zone_id}" >/dev/null
  echo "Disabled legacy parent DNS zone ${legacy_zone_domain}; records were preserved for rollback."
fi

echo "Exact private application zones are distributed to All; Authentik remains the application authorization layer."
