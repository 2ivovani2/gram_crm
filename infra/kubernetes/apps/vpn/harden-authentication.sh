#!/usr/bin/env bash
set -euo pipefail

if [[ -z "${KUBECONFIG:-}" ]]; then
  echo "KUBECONFIG is not set; refusing to use an implicit cluster context." >&2
  exit 1
fi

namespace="vpn"
secret_name="netbird-config"

config="$(kubectl get secret "${secret_name}" --namespace "${namespace}" --output jsonpath='{.data.config\.yaml}' | base64 --decode)"

if ! curl -fsSL 'https://vpn.gramly.tech/oauth2/auth?client_id=netbird-dashboard&redirect_uri=https%3A%2F%2Fvpn.gramly.tech%2Fapi%2Freverse-proxy%2Fcallback&response_type=code&scope=openid%20profile%20email%20groups&state=gramly-hardening-check&nonce=gramly-hardening-check' \
  | grep -q 'Continue with Gramly SSO'; then
  echo "Gramly SSO is not visible on the NetBird login screen; refusing to disable local authentication." >&2
  exit 1
fi

has_local_auth_setting=false
if grep -q '^    localAuthDisabled:' <<<"${config}"; then
  has_local_auth_setting=true
fi

config="$({
  skip_owner=false
  while IFS= read -r line; do
    if [[ "${line}" == "    owner:" ]]; then
      skip_owner=true
      continue
    fi
    if [[ "${skip_owner}" == true ]]; then
      if [[ "${line}" == "      "* ]]; then
        continue
      fi
      skip_owner=false
    fi

    if [[ "${line}" == "    localAuthDisabled:"* ]]; then
      printf '%s\n' '    localAuthDisabled: true'
      continue
    fi

    printf '%s\n' "${line}"
    if [[ "${has_local_auth_setting}" == false && "${line}" == "    issuer:"* ]]; then
      printf '%s\n' '    localAuthDisabled: true'
    fi
  done <<<"${config}"
})"

encoded_config="$(printf '%s' "${config}" | base64 | tr -d '\n')"
jq --null-input --arg value "${encoded_config}" '{data:{"config.yaml":$value}}' | \
  kubectl patch secret "${secret_name}" --namespace "${namespace}" --type merge --patch-file /dev/stdin >/dev/null

if kubectl get secret "${secret_name}" --namespace "${namespace}" \
  --output go-template='{{if index .data "bootstrap-owner-password"}}present{{end}}' | grep -q '^present$'; then
  kubectl patch secret "${secret_name}" --namespace "${namespace}" --type json \
    --patch='[{"op":"remove","path":"/data/bootstrap-owner-password"}]' >/dev/null
fi

unset config encoded_config
kubectl rollout restart deployment/netbird-server --namespace "${namespace}" >/dev/null
kubectl rollout status deployment/netbird-server --namespace "${namespace}" --timeout=5m

echo "NetBird local authentication is disabled and its bootstrap owner credential is removed."
