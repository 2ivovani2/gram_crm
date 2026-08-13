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

ensure_group() {
  local group_name="$1"
  local existing

  existing="$(curl --config "${curl_config}" "${api_url}/groups")"
  if jq -e --arg name "${group_name}" '.[] | select(.name == $name)' \
    >/dev/null <<<"${existing}"; then
    echo "NetBird group ${group_name} already exists."
    return
  fi

  jq -n --arg name "${group_name}" '{name: $name, peers: [], resources: []}' \
    >"${task_tmp}/group.json"
  curl --config "${curl_config}" \
    --header 'Content-Type: application/json' \
    --request POST \
    --data-binary "@${task_tmp}/group.json" \
    "${api_url}/groups" >/dev/null
  echo "Created NetBird group ${group_name}."
}

groups=(
  Business
  gramly-employees
  gramly-product
  gramly-engineering
  gramly-devops
  gramly-owners
  gramly-business-services
  gramly-collaboration-services
  gramly-devops-services
)

for group_name in "${groups[@]}"; do
  ensure_group "${group_name}"
done

echo "Gramly NetBird access groups are present; no peer membership or policies were changed."
