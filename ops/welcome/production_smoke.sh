#!/usr/bin/env bash
set -euo pipefail

[[ -n "${KUBECONFIG:-}" ]] || { echo "KUBECONFIG is required." >&2; exit 1; }

namespace=gramly-welcome
kubectl -n "${namespace}" rollout status deployment/gramly-welcome-api --timeout=30m
kubectl -n "${namespace}" rollout status deployment/gramly-welcome-web --timeout=30m
for worker in owner events view delivery billing notifications; do
  kubectl -n "${namespace}" rollout status "deployment/gramly-welcome-worker-${worker}" --timeout=30m
done

kubectl -n "${namespace}" exec deployment/gramly-welcome-api -- python -c \
  "import httpx; response=httpx.get('http://127.0.0.1:8080/health/ready', timeout=10); response.raise_for_status()"

curl --fail --silent --show-error --location --max-time 60 \
  https://gramly.tech/ >/dev/null

app_html="$(curl --fail --silent --show-error --location --max-time 60 \
  https://hello.gramly.tech/app/)"
asset_paths="$(grep -Eo '/assets/[^\" ]+\.(js|css)' <<<"${app_html}" | sort -u)"
[[ -n "${asset_paths}" ]] || { echo "Mini App HTML does not reference built assets." >&2; exit 1; }
while IFS= read -r asset_path; do
  curl --fail --silent --show-error --location --max-time 60 \
    "https://hello.gramly.tech${asset_path}" >/dev/null
done <<<"${asset_paths}"

curl --fail --silent --show-error --location --max-time 60 \
  https://hello.gramly.tech/api/v1/public-config >/dev/null

session_status="$(curl --silent --show-error --output /dev/null --write-out '%{http_code}' \
  --max-time 60 --request POST --header 'Content-Type: application/json' \
  --data '{}' https://hello.gramly.tech/api/v1/session/telegram)"
[[ "${session_status}" == "422" ]] || {
  echo "Expected session validation HTTP 422, got ${session_status}." >&2
  exit 1
}

admin_status="$(curl --silent --show-error --output /dev/null --write-out '%{http_code}' \
  --max-time 60 https://hello-admin.gramly.tech/)"
case "${admin_status}" in
  302|303) ;;
  *) echo "Expected Authentik redirect from hello-admin, got HTTP ${admin_status}." >&2; exit 1 ;;
esac

kubectl -n "${namespace}" get pod \
  -l 'app.kubernetes.io/name in (gramly-welcome-api,gramly-welcome-web,gramly-welcome-worker-owner,gramly-welcome-worker-events,gramly-welcome-worker-view,gramly-welcome-worker-delivery,gramly-welcome-worker-billing,gramly-welcome-worker-notifications)' \
  --field-selector=status.phase!=Running --no-headers | \
  grep -q . && { echo "A GramlyHello production pod is not Running." >&2; exit 1; } || true

echo "GramlyHello production API, Mini App, SSO gate, and workers passed smoke checks."
