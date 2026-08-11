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
service_user_name="gramly-k8s-operator"
expiration_days="${NETBIRD_OPERATOR_TOKEN_DAYS:-90}"
old_token="$(kubectl get secret netbird-mgmt-api-key \
  --namespace vpn \
  --output jsonpath='{.data.NB_API_KEY}' | base64 --decode)"

task_tmp="$(mktemp -d)"
trap 'rm -rf "${task_tmp}"; unset old_token new_token' EXIT

old_curl_config="${task_tmp}/old-curl.conf"
printf 'silent\nshow-error\nfail\nretry = 3\nretry-all-errors\nheader = "Authorization: Token %s"\nheader = "Accept: application/json"\n' \
  "${old_token}" >"${old_curl_config}"
chmod 600 "${old_curl_config}"

service_user_id="$(curl --config "${old_curl_config}" "${api_url}/users" | \
  jq -er --arg name "${service_user_name}" \
    '.[] | select(.is_service_user == true and .name == $name) | .id')"
curl --config "${old_curl_config}" \
  "${api_url}/users/${service_user_id}/tokens" >"${task_tmp}/old-tokens.json"

jq -n --arg name "kubernetes-operator-$(date -u +%Y-%m-%d)" \
  --argjson expires_in "${expiration_days}" \
  '{name: $name, expires_in: $expires_in}' >"${task_tmp}/create-token.json"
curl --config "${old_curl_config}" \
  --retry 0 \
  --header 'Content-Type: application/json' \
  --request POST \
  --data-binary "@${task_tmp}/create-token.json" \
  "${api_url}/users/${service_user_id}/tokens" >"${task_tmp}/new-token.json"

new_token="$(jq -er '.plain_token | select(type == "string" and length > 0)' \
  "${task_tmp}/new-token.json")"
new_token_id="$(jq -er '.personal_access_token.id' "${task_tmp}/new-token.json")"

new_curl_config="${task_tmp}/new-curl.conf"
printf 'silent\nshow-error\nfail\nretry = 3\nretry-all-errors\nheader = "Authorization: Token %s"\nheader = "Accept: application/json"\n' \
  "${new_token}" >"${new_curl_config}"
chmod 600 "${new_curl_config}"
curl --config "${new_curl_config}" "${api_url}/groups" >/dev/null

printf '%s' "${new_token}" | kubectl --namespace vpn create secret generic \
  netbird-mgmt-api-key \
  --from-file=NB_API_KEY=/dev/stdin \
  --dry-run=client \
  --output yaml | kubectl apply --filename - >/dev/null

kubectl rollout restart deployment/netbird-operator --namespace vpn >/dev/null
kubectl rollout status deployment/netbird-operator --namespace vpn --timeout=5m

while IFS= read -r token_id; do
  [[ -z "${token_id}" || "${token_id}" == "${new_token_id}" ]] && continue
  curl --config "${new_curl_config}" \
    --retry 0 \
    --request DELETE \
    "${api_url}/users/${service_user_id}/tokens/${token_id}" >/dev/null
done < <(jq -r '.[].id' "${task_tmp}/old-tokens.json")

echo "NetBird operator PAT rotated, stored in Kubernetes, and previous PATs revoked."
