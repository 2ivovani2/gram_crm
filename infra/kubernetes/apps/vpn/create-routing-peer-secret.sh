#!/usr/bin/env bash
set -euo pipefail

if [[ -z "${KUBECONFIG:-}" ]]; then
  echo "KUBECONFIG is not set; refusing to use an implicit cluster context." >&2
  exit 1
fi

if kubectl get secret netbird-routing-peer --namespace vpn >/dev/null 2>&1; then
  echo "Secret vpn/netbird-routing-peer already exists; refusing to rotate it implicitly." >&2
  exit 1
fi

read -r -s -p "NetBird routing-peer setup key: " setup_key
printf '\n'
if [[ -z "${setup_key}" ]]; then
  echo "Setup key cannot be empty." >&2
  exit 1
fi

encoded_key="$(printf '%s' "${setup_key}" | base64 | tr -d '\n')"
jq --null-input --arg value "${encoded_key}" \
  '{apiVersion:"v1",kind:"Secret",metadata:{name:"netbird-routing-peer",namespace:"vpn"},type:"Opaque",data:{"setup-key":$value}}' | \
  kubectl apply --filename - >/dev/null

unset setup_key encoded_key
echo "Routing-peer setup key stored in Kubernetes; its value was not printed or written to disk."
