#!/usr/bin/env bash
set -euo pipefail

root_dir="$(cd "$(dirname "${BASH_SOURCE[0]}")/../../.." && pwd)"
template="${root_dir}/infra/kubernetes/platform/welcome/managed-valkey-egress.yaml.tpl"
environment="${1:?Usage: apply-welcome-valkey-egress.sh staging|production CIDR PORT}"
valkey_cidr="${2:?Valkey CIDR is required}"
valkey_port="${3:?Valkey TCP port is required}"
[[ -n "${KUBECONFIG:-}" ]] || { echo "KUBECONFIG is required." >&2; exit 1; }

case "${environment}" in
  staging) namespace=gramly-staging ;;
  production) namespace=gramly-welcome ;;
  *) echo "Environment must be staging or production." >&2; exit 1 ;;
esac
python3 -c 'import ipaddress,sys; ipaddress.ip_network(sys.argv[1], strict=False)' "${valkey_cidr}"
[[ "${valkey_port}" =~ ^[0-9]+$ ]] && (( valkey_port > 0 && valkey_port < 65536 )) \
  || { echo "Valkey port must be between 1 and 65535." >&2; exit 1; }

sed \
  -e "s|__WELCOME_NAMESPACE__|${namespace}|g" \
  -e "s|__WELCOME_VALKEY_CIDR__|${valkey_cidr}|g" \
  -e "s|__WELCOME_VALKEY_PORT__|${valkey_port}|g" \
  "${template}" | kubectl apply -f -
