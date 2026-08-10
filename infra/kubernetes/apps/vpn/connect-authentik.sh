#!/usr/bin/env bash
set -euo pipefail

if [[ -z "${KUBECONFIG:-}" ]]; then
  echo "KUBECONFIG is not set; refusing to use an implicit cluster context." >&2
  exit 1
fi

for secret_ref in identity/netbird-authentik-oidc vpn/netbird-config; do
  namespace="${secret_ref%%/*}"
  secret_name="${secret_ref##*/}"
  kubectl get secret "${secret_name}" --namespace "${namespace}" >/dev/null
done

task_tmp="$(mktemp -d /tmp/gramly-netbird-auth.XXXXXX)"
trap 'rm -rf -- "${task_tmp}"' EXIT
umask 077

owner_email="avyaroslavskiy@miem.hse.ru"
owner_password="$(kubectl get secret netbird-config --namespace vpn --output jsonpath='{.data.bootstrap-owner-password}' | base64 --decode)"
client_id="$(kubectl get secret netbird-authentik-oidc --namespace identity --output jsonpath='{.data.client-id}' | base64 --decode)"
client_secret="$(kubectl get secret netbird-authentik-oidc --namespace identity --output jsonpath='{.data.client-secret}' | base64 --decode)"
issuer="$(kubectl get secret netbird-authentik-oidc --namespace identity --output jsonpath='{.data.issuer}' | base64 --decode)"

verifier="$(openssl rand -base64 48 | tr -d '=+/\n' | cut -c1-64)"
challenge="$(printf '%s' "${verifier}" | openssl dgst -sha256 -binary | base64 | tr '+/' '-_' | tr -d '=\n')"
auth_url="$(curl -sS -o /dev/null -w '%{url_effective}' --get 'https://vpn.gramly.tech/oauth2/auth' \
  --data-urlencode 'client_id=netbird-dashboard' \
  --data-urlencode 'redirect_uri=https://vpn.gramly.tech/nb-auth' \
  --data-urlencode 'response_type=code' \
  --data-urlencode 'scope=openid profile email groups' \
  --data-urlencode 'state=gramly-bootstrap' \
  --data-urlencode 'nonce=gramly-bootstrap' \
  --data-urlencode "code_challenge=${challenge}" \
  --data-urlencode 'code_challenge_method=S256')"

login_html="$(curl -fsSL --cookie-jar "${task_tmp}/cookies" --cookie "${task_tmp}/cookies" "${auth_url}")"
action="$(printf '%s' "${login_html}" | sed -n 's/.*<form method="post" action="\([^"]*\)".*/\1/p' | head -1 | sed 's/&amp;/\&/g')"
if [[ -z "${action}" ]]; then
  local_path="$(printf '%s' "${login_html}" | sed -n 's/.*href="\(\/oauth2\/auth\/local?[^\"]*\)".*/\1/p' | head -1 | sed -e 's/&amp;/\&/g' -e 's/&#43;/+/g')"
  if [[ -n "${local_path}" ]]; then
    login_html="$(curl -fsSL --cookie-jar "${task_tmp}/cookies" --cookie "${task_tmp}/cookies" "https://vpn.gramly.tech${local_path}")"
    action="$(printf '%s' "${login_html}" | sed -n 's/.*<form method="post" action="\([^"]*\)".*/\1/p' | head -1 | sed 's/&amp;/\&/g')"
  fi
fi
if [[ -z "${action}" ]]; then
  echo "Could not locate the local NetBird login form." >&2
  exit 1
fi

final_url="$(curl -sSL --cookie-jar "${task_tmp}/cookies" --cookie "${task_tmp}/cookies" \
  --output /dev/null --write-out '%{url_effective}' \
  --data-urlencode "login=${owner_email}" \
  --data-urlencode "password=${owner_password}" \
  "https://vpn.gramly.tech${action}")"
code="$(printf '%s' "${final_url}" | sed -nE 's/.*[?&]code=([^&]+).*/\1/p')"
if [[ -z "${code}" ]]; then
  echo "NetBird did not return an authorization code." >&2
  exit 1
fi

token_response="$(curl -fsSL --request POST 'https://vpn.gramly.tech/oauth2/token' \
  --data-urlencode 'grant_type=authorization_code' \
  --data-urlencode "code=${code}" \
  --data-urlencode 'redirect_uri=https://vpn.gramly.tech/nb-auth' \
  --data-urlencode 'client_id=netbird-dashboard' \
  --data-urlencode "code_verifier=${verifier}")"
access_token="$(printf '%s' "${token_response}" | jq -er '.access_token')"

printf 'header = "Authorization: Bearer %s"\nheader = "Content-Type: application/json"\n' \
  "${access_token}" >"${task_tmp}/curl.conf"
http_status="$(curl -sS --config "${task_tmp}/curl.conf" --output "${task_tmp}/providers.json" \
  --write-out '%{http_code}' 'https://vpn.gramly.tech/api/identity-providers')"
if [[ "${http_status}" != "200" ]]; then
  echo "Unable to list NetBird identity providers (HTTP ${http_status})." >&2
  exit 1
fi

if ! jq -e '.[] | select(.issuer == "https://auth.gramly.tech/application/o/netbird/")' \
  "${task_tmp}/providers.json" >/dev/null; then
  jq --null-input \
    --arg type authentik \
    --arg name 'Gramly SSO' \
    --arg issuer "${issuer}" \
    --arg client_id "${client_id}" \
    --arg client_secret "${client_secret}" \
    '{type:$type,name:$name,issuer:$issuer,client_id:$client_id,client_secret:$client_secret}' \
    >"${task_tmp}/provider.json"

  http_status="$(curl -sS --config "${task_tmp}/curl.conf" --output "${task_tmp}/response.json" \
    --write-out '%{http_code}' --request POST --data-binary "@${task_tmp}/provider.json" \
    'https://vpn.gramly.tech/api/identity-providers')"
  if [[ "${http_status}" != "200" ]]; then
    echo "Unable to create NetBird identity provider (HTTP ${http_status})." >&2
    exit 1
  fi
fi

http_status="$(curl -sS --config "${task_tmp}/curl.conf" --output "${task_tmp}/users.json" \
  --write-out '%{http_code}' 'https://vpn.gramly.tech/api/users')"
if [[ "${http_status}" != "200" ]]; then
  echo "Unable to list NetBird users (HTTP ${http_status})." >&2
  exit 1
fi

pending_user_id="$(jq -r --arg email "${owner_email}" \
  '.[] | select(.email == $email and .pending_approval == true) | .id' \
  "${task_tmp}/users.json" | head -1)"
if [[ -n "${pending_user_id}" ]]; then
  http_status="$(curl -sS --config "${task_tmp}/curl.conf" --output "${task_tmp}/approved-user.json" \
    --write-out '%{http_code}' --request POST \
    "https://vpn.gramly.tech/api/users/${pending_user_id}/approve")"
  if [[ "${http_status}" != "200" ]]; then
    echo "Unable to approve the NetBird SSO owner (HTTP ${http_status})." >&2
    exit 1
  fi

  jq '{role:"admin",auto_groups:(.auto_groups // []),is_blocked:false}' \
    "${task_tmp}/approved-user.json" >"${task_tmp}/owner-role.json"
  http_status="$(curl -sS --config "${task_tmp}/curl.conf" --output "${task_tmp}/admin-user.json" \
    --write-out '%{http_code}' --request PUT --data-binary "@${task_tmp}/owner-role.json" \
    "https://vpn.gramly.tech/api/users/${pending_user_id}")"
  if [[ "${http_status}" != "200" ]]; then
    echo "The NetBird SSO owner was approved but could not be promoted to admin (HTTP ${http_status})." >&2
    exit 1
  fi
fi

http_status="$(curl -sS --config "${task_tmp}/curl.conf" --output "${task_tmp}/verified-users.json" \
  --write-out '%{http_code}' 'https://vpn.gramly.tech/api/users')"
if [[ "${http_status}" != "200" ]] || \
  ! jq -e --arg email "${owner_email}" \
    '.[] | select(.email == $email and .pending_approval == false and .role == "admin" and .idp_id != null)' \
    "${task_tmp}/verified-users.json" >/dev/null; then
  echo "The NetBird SSO owner did not pass the final approval and admin-role check." >&2
  exit 1
fi

unset owner_password client_secret access_token token_response code verifier pending_user_id
echo "Authentik is connected to NetBird; the SSO owner is approved and has the admin role."
