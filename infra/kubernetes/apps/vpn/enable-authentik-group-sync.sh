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
readonly peer_login_expiration_seconds="${PEER_LOGIN_EXPIRATION_SECONDS:-604800}"

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

if [[ ! "${peer_login_expiration_seconds}" =~ ^[0-9]+$ ]] ||
   (( peer_login_expiration_seconds < 86400 )); then
  echo "PEER_LOGIN_EXPIRATION_SECONDS must be an integer of at least 86400." >&2
  exit 1
fi

curl --config "${curl_config}" "${api_url}/accounts" >"${task_tmp}/accounts.json"
account_id="$(jq -er '.[0].id' "${task_tmp}/accounts.json")"

jq --argjson auto_approve "${auto_approve_authentik_users}" \
  --argjson peer_login_expiration "${peer_login_expiration_seconds}" \
  --argjson allow_groups '[]' \
  '.[0]
   | {settings: .settings, onboarding: .onboarding}
   | .settings.jwt_groups_enabled = true
   | .settings.jwt_groups_claim_name = "groups"
   | .settings.jwt_allow_groups = $allow_groups
   | .settings.groups_propagation_enabled = true
   | .settings.peer_login_expiration_enabled = true
   | .settings.peer_login_expiration = $peer_login_expiration
   | if $auto_approve then
       .settings.extra.user_approval_required = false
     else . end' \
  "${task_tmp}/accounts.json" >"${task_tmp}/account-update.json"

curl --config "${curl_config}" \
  --header 'Content-Type: application/json' \
  --request PUT \
  --data-binary "@${task_tmp}/account-update.json" \
  "${api_url}/accounts/${account_id}" >"${task_tmp}/updated-account.json"

jq -e --argjson peer_login_expiration "${peer_login_expiration_seconds}" '
  .settings.jwt_groups_enabled == true and
  .settings.jwt_groups_claim_name == "groups" and
  .settings.groups_propagation_enabled == true and
  .settings.jwt_allow_groups == [] and
  .settings.peer_login_expiration_enabled == true and
  .settings.peer_login_expiration == $peer_login_expiration
' "${task_tmp}/updated-account.json" >/dev/null

if [[ "${auto_approve_authentik_users}" == "true" ]]; then
  jq -e '.settings.extra.user_approval_required == false' \
    "${task_tmp}/updated-account.json" >/dev/null
fi

if [[ "${auto_approve_authentik_users}" == "true" ]]; then
  echo "NetBird accepts Authentik users automatically, propagates identity groups and requires SSO again after ${peer_login_expiration_seconds} seconds."
else
  echo "NetBird admits Authentik users after one-time approval, propagates identity groups and requires SSO again after ${peer_login_expiration_seconds} seconds."
fi
