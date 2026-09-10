#!/usr/bin/env bash
set -euo pipefail

# Routes only: preserves the deployed application images, Secrets and data.
root_dir="$(cd "$(dirname "${BASH_SOURCE[0]}")/../../.." && pwd)"
infra_dir="${root_dir}/infra/kubernetes"
: "${KUBECONFIG:?Set the production VKE kubeconfig}"
[[ "$(kubectl config current-context)" == "admin@vke-79dd127e-498c-4250-a75a-eb8f85778d0b" ]] || exit 1
source "${infra_dir}/bootstrap/versions.env"

# Public Git must not retain the old VPN-protected anonymous browsing setting.
kubectl -n devtools set env deployment/forgejo \
  FORGEJO__service__REQUIRE_SIGNIN_VIEW=true FORGEJO__service__DISABLE_REGISTRATION=true
kubectl -n devtools rollout status deployment/forgejo --timeout=5m

helm upgrade traefik-public traefik/traefik \
  --version "${TRAEFIK_CHART_VERSION}" --namespace traefik-public \
  --values "${infra_dir}/platform/gateway/traefik-public-values.yaml" \
  --wait --timeout 10m
kubectl apply -f "${infra_dir}/platform/gateway/public-gateway.yaml"
kubectl apply -f "${infra_dir}/platform/gateway/public-http-redirect.yaml"
kubectl apply -f "${infra_dir}/base/welcome-web/network-policy.yaml"
kubectl apply -f "${infra_dir}/apps/observability/headlamp-network-policy.yaml"

while read -r namespace route listener; do
  # Keep the old private parent during the DNS/cache transition. Retirement
  # removes it only after clients stop receiving private DNS/routes.
  parents="$(kubectl -n "${namespace}" get httproute "${route}" -o json | \
    jq -c --arg listener "${listener}" \
      '((.spec.parentRefs // []) | map(select(.name != "gramly-public-access"))) +
       [{name:"gramly-public-access", namespace:"traefik-public", sectionName:$listener}]')"
  kubectl -n "${namespace}" patch httproute "${route}" --type=merge \
    -p "{\"spec\":{\"parentRefs\":${parents}}}"
done <<'ROUTES'
gramly-crm gramly-crm-private crm-https
devtools forgejo git-https
devtools vikunja tasks-https
devtools outline docs-https
observability grafana grafana-https
observability headlamp cluster-https
gramly-welcome gramly-welcome-admin hello-admin-https
ROUTES

kubectl -n gramly-hello delete httproute gramly-private-access-gate --ignore-not-found
kubectl -n traefik-public wait gateway/gramly-public-access --for=condition=Programmed --timeout=5m
echo "Public service routes applied. Verify SSO and application permissions before retiring NetBird."
