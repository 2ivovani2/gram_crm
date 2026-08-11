#!/usr/bin/env bash
set -euo pipefail

if [[ -z "${KUBECONFIG:-}" ]]; then
  echo "KUBECONFIG is not set; refusing to use an implicit cluster context." >&2
  exit 1
fi

for command_name in kubectl curl jq; do
  command -v "${command_name}" >/dev/null || exit 1
done

api_url="https://vpn.gramly.tech/api"
api_token="$(kubectl get secret netbird-mgmt-api-key --namespace vpn \
  --output jsonpath='{.data.NB_API_KEY}' | base64 --decode)"
task_tmp="$(mktemp -d)"
trap 'rm -rf "${task_tmp}"; unset api_token' EXIT

curl_config="${task_tmp}/curl.conf"
printf 'silent\nshow-error\nfail\nconnect-timeout = 10\nmax-time = 30\nheader = "Authorization: Token %s"\nheader = "Accept: application/json"\n' \
  "${api_token}" >"${curl_config}"
chmod 600 "${curl_config}"

zone_id="$(curl --config "${curl_config}" "${api_url}/dns/zones" | \
  jq -r '.[] | select(.domain == "gramly.tech") | .id')"
[[ -n "${zone_id}" ]] || { echo "NetBird zone gramly.tech is missing." >&2; exit 1; }

desired_records='[
  {"name":"crm.gramly.tech","type":"A","content":"10.99.132.83","ttl":300},
  {"name":"git.gramly.tech","type":"A","content":"10.99.132.84","ttl":300},
  {"name":"tasks.gramly.tech","type":"A","content":"10.99.132.84","ttl":300},
  {"name":"docs.gramly.tech","type":"A","content":"10.99.132.84","ttl":300}
]'
curl --config "${curl_config}" "${api_url}/dns/zones/${zone_id}/records" \
  >"${task_tmp}/records.json"

while IFS= read -r record; do
  name="$(jq -r '.name' <<<"${record}")"
  record_id="$(jq -r --arg name "${name}" \
    '.[] | select(.name == $name) | .id' "${task_tmp}/records.json" | head -n 1)"
  printf '%s\n' "${record}" >"${task_tmp}/record.json"
  if [[ -n "${record_id}" ]]; then
    method=PUT
    endpoint="${api_url}/dns/zones/${zone_id}/records/${record_id}"
  else
    method=POST
    endpoint="${api_url}/dns/zones/${zone_id}/records"
  fi
  curl --config "${curl_config}" --header 'Content-Type: application/json' \
    --request "${method}" --data-binary "@${task_tmp}/record.json" \
    "${endpoint}" >/dev/null
  echo "Reconciled private DNS record ${name}."
done < <(jq -c '.[]' <<<"${desired_records}")
