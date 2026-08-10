#!/usr/bin/env bash
set -euo pipefail

ROOT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")/../../.." && pwd)"

: "${KUBECONFIG:?Set KUBECONFIG to the downloaded VKE kubeconfig}"

for host in auth.gramly.tech vpn.gramly.tech; do
  resolved="$(dig +short "$host" A @ns1.reg.ru | tail -n 1)"
  if [[ "$resolved" != "45.77.149.91" ]]; then
    printf 'Refusing to request certificates: %s resolves to %s, expected 45.77.149.91\n' \
      "$host" "${resolved:-nothing}" >&2
    exit 1
  fi
done

kubectl apply -f "$ROOT_DIR/platform/cert-manager/cluster-issuers.yaml"
kubectl apply -f "$ROOT_DIR/platform/gateway/public-gateway.yaml"
kubectl apply -f "$ROOT_DIR/platform/gateway/public-http-redirect.yaml"
kubectl apply -f "$ROOT_DIR/apps/identity/http-route.yaml"

kubectl wait --for=condition=Programmed gateway/gramly-public \
  --namespace traefik-public --timeout=180s
kubectl wait --for=condition=Ready certificate/auth-gramly-tech-tls \
  --namespace traefik-public --timeout=300s

curl --fail --silent --show-error --location --max-time 20 \
  --resolve auth.gramly.tech:443:45.77.149.91 \
  https://auth.gramly.tech/-/health/ready/
