#!/usr/bin/env bash
set -euo pipefail

[[ -n "${KUBECONFIG:-}" ]] || { echo "KUBECONFIG is required." >&2; exit 1; }

namespace=gramly-welcome
kubectl -n "${namespace}" rollout status deployment/gramly-welcome-api --timeout=10m
kubectl -n "${namespace}" rollout status deployment/gramly-welcome-web --timeout=10m
for worker in events delivery billing notifications; do
  kubectl -n "${namespace}" rollout status "deployment/gramly-welcome-worker-${worker}" --timeout=10m
done

kubectl -n "${namespace}" exec deployment/gramly-welcome-api -- python -c \
  "import httpx; response=httpx.get('http://127.0.0.1:8080/health/ready', timeout=10); response.raise_for_status()"

curl --fail --silent --show-error --location --max-time 20 \
  https://hello.gramly.tech/app/ >/dev/null

admin_status="$(curl --silent --show-error --output /dev/null --write-out '%{http_code}' \
  --max-time 20 https://hello-admin.gramly.tech/)"
case "${admin_status}" in
  302|303) ;;
  *) echo "Expected Authentik redirect from hello-admin, got HTTP ${admin_status}." >&2; exit 1 ;;
esac

kubectl -n "${namespace}" get pod \
  -l 'app.kubernetes.io/name in (gramly-welcome-api,gramly-welcome-web,gramly-welcome-worker-events,gramly-welcome-worker-delivery,gramly-welcome-worker-billing,gramly-welcome-worker-notifications)' \
  --field-selector=status.phase!=Running --no-headers | \
  grep -q . && { echo "A GramlyHello production pod is not Running." >&2; exit 1; } || true

echo "GramlyHello production API, Mini App, SSO gate, and workers passed smoke checks."
