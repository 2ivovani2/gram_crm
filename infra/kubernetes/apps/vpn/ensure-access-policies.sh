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
api_token="$(kubectl get secret netbird-mgmt-api-key \
  --namespace vpn \
  --output jsonpath='{.data.NB_API_KEY}' | base64 --decode)"

task_tmp="$(mktemp -d)"
trap 'rm -rf "${task_tmp}"; unset api_token' EXIT

curl_config="${task_tmp}/curl.conf"
printf 'silent\nshow-error\nfail\nconnect-timeout = 10\nmax-time = 30\nretry = 3\nretry-all-errors\nheader = "Authorization: Token %s"\nheader = "Accept: application/json"\n' \
  "${api_token}" >"${curl_config}"
chmod 600 "${curl_config}"

curl --config "${curl_config}" "${api_url}/groups" >"${task_tmp}/groups.json"

group_id() {
  local group_name="$1"
  local id

  id="$(jq -r --arg name "${group_name}" \
    '.[] | select(.name == $name) | .id' "${task_tmp}/groups.json" | head -n 1)"
  if [[ -z "${id}" ]]; then
    echo "Missing required NetBird group: ${group_name}." >&2
    exit 1
  fi
  printf '%s' "${id}"
}

source_names='[
  "gramly-admin-devices",
  "gramly-employees",
  "gramly-product",
  "gramly-engineering",
  "gramly-devops",
  "gramly-owners"
]'
source_ids='[]'
while IFS= read -r source_name; do
  source_ids="$(jq -c --arg id "$(group_id "${source_name}")" '. + [$id]' \
    <<<"${source_ids}")"
done < <(jq -r '.[]' <<<"${source_names}")

privileged_source_ids="$(jq -cn \
  --arg admins "$(group_id gramly-admin-devices)" \
  --arg devops "$(group_id gramly-devops)" \
  --arg owners "$(group_id gramly-owners)" \
  '[$admins, $devops, $owners]')"

ensure_policy() {
  local policy_name="$1"
  local description="$2"
  local sources_json="$3"
  local destination_name="$4"
  local destination_id policy_id method endpoint

  destination_id="$(group_id "${destination_name}")"
  curl --config "${curl_config}" "${api_url}/policies" >"${task_tmp}/policies.json"
  policy_id="$(jq -r --arg name "${policy_name}" \
    '.[] | select(.name == $name) | .id' "${task_tmp}/policies.json" | head -n 1)"

  jq -n \
    --arg name "${policy_name}" \
    --arg description "${description}" \
    --argjson sources "${sources_json}" \
    --arg destination "${destination_id}" \
    '{name: $name, description: $description, enabled: true,
      source_posture_checks: [], rules: [{name: $name,
      description: $description, enabled: true, action: "accept",
      bidirectional: false, protocol: "tcp", ports: ["80", "443"],
      sources: $sources, destinations: [$destination]}]}' \
    >"${task_tmp}/policy.json"

  if [[ -n "${policy_id}" ]]; then
    method=PUT
    endpoint="${api_url}/policies/${policy_id}"
  else
    method=POST
    endpoint="${api_url}/policies"
  fi

  curl --config "${curl_config}" \
    --retry 0 \
    --header 'Content-Type: application/json' \
    --request "${method}" \
    --data-binary "@${task_tmp}/policy.json" \
    "${endpoint}" >/dev/null
  echo "Reconciled NetBird policy ${policy_name}."
}

ensure_policy \
  gramly-workforce-business \
  "Employees may reach CRM and other business services over HTTPS." \
  "${source_ids}" \
  gramly-business-services

ensure_policy \
  gramly-workforce-collaboration \
  "Employees may reach source control, tasks, and documentation over HTTPS." \
  "${source_ids}" \
  gramly-collaboration-services

ensure_policy \
  gramly-devops-infrastructure \
  "DevOps and owners may reach infrastructure administration services." \
  "${privileged_source_ids}" \
  gramly-devops-services

if [[ "${REMOVE_DEFAULT_POLICY:-false}" == "true" ]]; then
  curl --config "${curl_config}" "${api_url}/policies" >"${task_tmp}/policies.json"
  default_policy_id="$(jq -r '
    .[]
    | select(.name == "Default")
    | select(.rules | length == 1)
    | select(.rules[0].protocol == "all")
    | select(.rules[0].sources[0].name == "All")
    | select(.rules[0].destinations[0].name == "All")
    | .id
  ' "${task_tmp}/policies.json" | head -n 1)"
  if [[ -n "${default_policy_id}" ]]; then
    curl --config "${curl_config}" \
      --retry 0 \
      --request DELETE \
      "${api_url}/policies/${default_policy_id}" >/dev/null
    echo "Removed the permissive NetBird Default All-to-All policy."
  fi
else
  if jq -e '.[] | select(.name == "Default")' "${task_tmp}/policies.json" >/dev/null; then
    echo "Kept the NetBird Default policy; set REMOVE_DEFAULT_POLICY=true only in an approved maintenance window."
  else
    echo "The NetBird Default policy is already absent."
  fi
fi

echo "Role-based NetBird policies are present; group membership was not changed."
