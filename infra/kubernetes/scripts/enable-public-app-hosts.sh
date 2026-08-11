#!/usr/bin/env bash
set -euo pipefail
root_dir="$(cd "$(dirname "${BASH_SOURCE[0]}")/../../.." && pwd)"
infra_dir="${root_dir}/infra/kubernetes"
[[ -n "${KUBECONFIG:-}" ]] || { echo "KUBECONFIG is required." >&2; exit 1; }

expected_ip="${PUBLIC_LB_IP:-45.77.149.91}"
for host in hello.gramly.tech media.gramly.tech; do
  resolved="$(dig +short A "${host}" | tail -1)"
  if [[ "${resolved}" != "${expected_ip}" ]]; then
    echo "${host} must resolve to ${expected_ip}; got ${resolved:-no A record}." >&2
    exit 1
  fi
done

kubectl apply -f "${infra_dir}/platform/gateway/public-gateway.yaml"
kubectl apply -f "${infra_dir}/apps/crm/public-media.yaml"
kubectl wait certificate/hello-gramly-tech-tls -n traefik-public --for=condition=Ready --timeout=10m
kubectl wait certificate/media-gramly-tech-tls -n traefik-public --for=condition=Ready --timeout=10m
kubectl wait gateway/gramly-public -n traefik-public --for=condition=Programmed --timeout=5m
echo "Hello and private signed-media endpoints are TLS-ready. CRM runtime is unchanged."
