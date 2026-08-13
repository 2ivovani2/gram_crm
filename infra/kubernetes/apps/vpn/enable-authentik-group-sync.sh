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
readonly auto_approve_authentik_users="${AUTO_APPROVE_AUTHENTIK_USERS:-false}"
readonly jwt_groups=(
  Business
  gramly-employees
  gramly-product
  gramly-engineering
  gramly-devops
  gramly-owners
)

task_tmp="$(mktemp -d)"
trap 'rm -rf "${task_tmp}"; unset api_token' EXIT
umask 077

api_token="$(kubectl get secret netbird-mgmt-api-key \
  --namespace vpn \
  --output jsonpath='{.data.NB_API_KEY}' | base64 --decode)"

curl_config="${task_tmp}/curl.conf"
printf 'silent\nshow-error\nfail\nconnect-timeout = 10\nmax-time = 45\nretry = 2\nretry-all-errors\nheader = "Authorization: Token %s"\nheader = "Accept: application/json"\n' \
  "${api_token}" >"${curl_config}"
chmod 600 "${curl_config}"

if [[ "${auto_approve_authentik_users}" != "true" && \
      "${auto_approve_authentik_users}" != "false" ]]; then
  echo "AUTO_APPROVE_AUTHENTIK_USERS must be true or false." >&2
  exit 1
fi

curl --config "${curl_config}" "${api_url}/groups" >"${task_tmp}/groups.json"
for group_name in "${jwt_groups[@]}"; do
  jq -e --arg name "${group_name}" \
    '.[] | select(.name == $name)' "${task_tmp}/groups.json" >/dev/null || {
      echo "Missing NetBird group ${group_name}; run ensure-access-groups.sh first." >&2
      exit 1
    }
done

# NetBird intentionally refuses to attach JWT membership to an API-issued group
# with the same name. Promote only the identity source groups in-place so
# their IDs (and every policy reference to those IDs) remain unchanged.
sql="UPDATE groups SET issued = 'jwt' WHERE name IN ('Business','gramly-employees','gramly-product','gramly-engineering','gramly-devops','gramly-owners') AND issued = 'api';"
postgres_primary="$(kubectl get pods --namespace identity \
  --selector cnpg.io/cluster=identity-postgres,role=primary \
  --output jsonpath='{.items[0].metadata.name}')"
if [[ -z "${postgres_primary}" ]]; then
  echo "The primary identity-postgres pod was not found." >&2
  exit 1
fi
kubectl exec --namespace identity "${postgres_primary}" --container postgres -- \
  psql --username postgres --dbname netbird --set ON_ERROR_STOP=1 \
  --command "${sql}" >/dev/null

curl --config "${curl_config}" "${api_url}/accounts" >"${task_tmp}/accounts.json"
account_id="$(jq -er '.[0].id' "${task_tmp}/accounts.json")"

jq --argjson auto_approve "${auto_approve_authentik_users}" \
  --argjson allow_groups '[
      "Business",
      "gramly-employees",
      "gramly-product",
      "gramly-engineering",
      "gramly-devops",
      "gramly-owners"
    ]' \
  '.[0]
   | {settings: .settings, onboarding: .onboarding}
   | .settings.jwt_groups_enabled = true
   | .settings.jwt_groups_claim_name = "groups"
   | .settings.jwt_allow_groups = $allow_groups
   | .settings.groups_propagation_enabled = true
   | if $auto_approve then
       .settings.extra.user_approval_required = false
     else . end' \
  "${task_tmp}/accounts.json" >"${task_tmp}/account-update.json"

curl --config "${curl_config}" \
  --header 'Content-Type: application/json' \
  --request PUT \
  --data-binary "@${task_tmp}/account-update.json" \
  "${api_url}/accounts/${account_id}" >"${task_tmp}/updated-account.json"

jq -e '
  .settings.jwt_groups_enabled == true and
  .settings.jwt_groups_claim_name == "groups" and
  .settings.groups_propagation_enabled == true and
  (.settings.jwt_allow_groups | sort) == ([
    "Business",
    "gramly-devops",
    "gramly-employees",
    "gramly-engineering",
    "gramly-owners",
    "gramly-product"
  ] | sort)
' "${task_tmp}/updated-account.json" >/dev/null

if [[ "${auto_approve_authentik_users}" == "true" ]]; then
  jq -e '.settings.extra.user_approval_required == false' \
    "${task_tmp}/updated-account.json" >/dev/null
fi

if [[ "${auto_approve_authentik_users}" == "true" ]]; then
  echo "NetBird accepts allowlisted Authentik users automatically and propagates groups to every device."
else
  echo "NetBird propagates Authentik groups to every device; one-time user approval remains enabled."
fi
