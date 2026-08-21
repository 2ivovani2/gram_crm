#!/usr/bin/env bash
set -euo pipefail
root_dir="$(cd "$(dirname "${BASH_SOURCE[0]}")/../../.." && pwd)"
infra_dir="${root_dir}/infra/kubernetes"
[[ -n "${KUBECONFIG:-}" ]] || { echo "KUBECONFIG is required." >&2; exit 1; }

expected_ip="${PUBLIC_LB_IP:-45.77.149.91}"
authoritative_ns="$(dig +short NS gramly.tech | head -1)"
[[ -n "${authoritative_ns}" ]] || { echo "No authoritative DNS server found for gramly.tech." >&2; exit 1; }
for host in hello.gramly.tech media.gramly.tech; do
  # Query the authoritative server directly. Recursive resolvers can retain an
  # NXDOMAIN cache for several minutes immediately after a record is created.
  resolved="$(dig +short "@${authoritative_ns}" A "${host}" | tail -1)"
  if [[ "${resolved}" != "${expected_ip}" ]]; then
    echo "${host} must resolve to ${expected_ip}; got ${resolved:-no A record}." >&2
    exit 1
  fi
done

allowed_hosts="gramly.tech,www.gramly.tech,hello.gramly.tech,hello-admin.gramly.tech,crm.gramly.tech,grafana.gramly.tech,cluster.gramly.tech"
kubectl -n gramly-crm patch secret gramly-crm-runtime --type=merge \
  -p "{\"stringData\":{\"ALLOWED_HOSTS\":\"${allowed_hosts}\"}}" >/dev/null
"${infra_dir}/scripts/prepare-public-web-secrets.sh"

kubectl apply -f "${infra_dir}/platform/gateway/public-gateway.yaml"
kubectl apply -f "${infra_dir}/apps/crm/public-media.yaml"
kubectl delete referencegrant allow-public-media-route -n gramly-crm --ignore-not-found=true >/dev/null
kubectl rollout status deployment/gramly-media-proxy -n gramly-hello --timeout=10m
current_image="$(kubectl -n gramly-hello get deployment gramly-public-web -o jsonpath='{.spec.template.spec.containers[0].image}')"
[[ -n "${current_image}" ]] || { echo "Public web deployment image could not be resolved." >&2; exit 1; }
kubectl kustomize "${infra_dir}/base/public-web" \
  | sed "s|CRM_IMAGE_PLACEHOLDER|${current_image}|g" \
  | kubectl apply -f -
kubectl rollout restart deployment/gramly-public-web -n gramly-hello
kubectl rollout status deployment/gramly-public-web -n gramly-hello --timeout=10m
kubectl wait certificate/hello-gramly-tech-tls -n traefik-public --for=condition=Ready --timeout=10m
kubectl wait certificate/media-gramly-tech-tls -n traefik-public --for=condition=Ready --timeout=10m
kubectl wait gateway/gramly-public -n traefik-public --for=condition=Programmed --timeout=5m
echo "Hello and private signed-media endpoints are TLS-ready. CRM runtime is unchanged."
