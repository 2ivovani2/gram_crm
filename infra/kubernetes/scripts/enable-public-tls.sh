#!/usr/bin/env bash
set -euo pipefail

infra_dir="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"

: "${KUBECONFIG:?Set KUBECONFIG to the downloaded VKE kubeconfig}"

# shellcheck source=/dev/null
source "$infra_dir/bootstrap/versions.env"

for host in auth.gramly.tech vpn.gramly.tech; do
  resolved="$(dig +short "$host" A @ns1.reg.ru | tail -n 1)"
  if [[ "$resolved" != "45.77.149.91" ]]; then
    printf 'Refusing to request certificates: %s resolves to %s, expected 45.77.149.91\n' \
      "$host" "${resolved:-nothing}" >&2
    exit 1
  fi
done

kubectl apply -f "$infra_dir/platform/cert-manager/cluster-issuers.yaml"

helm upgrade --install traefik-public traefik/traefik \
  --version "$TRAEFIK_CHART_VERSION" \
  --namespace traefik-public \
  --values "$infra_dir/platform/gateway/traefik-public-values.yaml" \
  --wait --timeout 10m

kubectl apply -f "$infra_dir/platform/gateway/public-gateway.yaml"
kubectl apply -f "$infra_dir/platform/gateway/public-http-redirect.yaml"
kubectl apply -f "$infra_dir/apps/identity/http-route.yaml"
kubectl --namespace vpn delete udproute netbird-stun --ignore-not-found
kubectl apply -f "$infra_dir/apps/vpn/routes.yaml"

kubectl wait --for=condition=Ready certificate/auth-gramly-tech-tls \
  --namespace traefik-public --timeout=300s
kubectl wait --for=condition=Ready certificate/vpn-gramly-tech-tls \
  --namespace traefik-public --timeout=300s
kubectl wait --for=condition=Programmed gateway/gramly-public \
  --namespace traefik-public --timeout=180s

curl --fail --silent --show-error --location --max-time 20 \
  --resolve auth.gramly.tech:443:45.77.149.91 \
  https://auth.gramly.tech/-/health/ready/
