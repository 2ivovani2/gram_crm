#!/usr/bin/env bash
set -euo pipefail

root_dir="$(cd "$(dirname "${BASH_SOURCE[0]}")/../../.." && pwd)"
infra_dir="${root_dir}/infra/kubernetes"
source "${infra_dir}/bootstrap/versions.env"
[[ -n "${KUBECONFIG:-}" ]] || { echo "KUBECONFIG is required." >&2; exit 1; }

kubectl apply -f "${infra_dir}/namespaces/namespaces.yaml"
kubectl apply -f "${infra_dir}/apps/observability/rbac.yaml"
if ! kubectl -n observability get secret grafana-admin >/dev/null 2>&1; then
  kubectl -n observability create secret generic grafana-admin \
    --from-literal=admin-user=breakglass-admin \
    --from-literal=admin-password="$(openssl rand -hex 32)" >/dev/null
fi
"${infra_dir}/apps/identity/configure-observability-oidc.sh"
# Grafana's dashboard sidecar deliberately has runtime reload disabled. Make
# the provisioned dashboard available before the Grafana pod starts/restarts.
kubectl apply -f "${infra_dir}/apps/observability/gramly-platform-dashboard.yaml"
kubectl apply -f "${infra_dir}/apps/observability/gramly-welcome-dashboard.yaml"

helm repo add prometheus-community https://prometheus-community.github.io/helm-charts --force-update >/dev/null
helm repo add headlamp https://kubernetes-sigs.github.io/headlamp/ --force-update >/dev/null
helm repo add oauth2-proxy https://oauth2-proxy.github.io/manifests --force-update >/dev/null
helm repo update prometheus-community headlamp oauth2-proxy >/dev/null

helm upgrade --install kube-prometheus-stack prometheus-community/kube-prometheus-stack \
  --version "${KUBE_PROMETHEUS_STACK_CHART_VERSION}" --namespace observability \
  --values "${infra_dir}/apps/observability/kube-prometheus-stack-values.yaml" --wait --timeout 15m
helm upgrade --install headlamp headlamp/headlamp \
  --version "${HEADLAMP_CHART_VERSION}" --namespace observability \
  --values "${infra_dir}/apps/observability/headlamp-values.yaml" --wait --timeout 10m
helm upgrade --install observability-oauth2-proxy oauth2-proxy/oauth2-proxy \
  --version "${OAUTH2_PROXY_CHART_VERSION}" --namespace observability \
  --values "${infra_dir}/apps/observability/oauth2-proxy-values.yaml" --wait --timeout 10m

kubectl apply -f "${infra_dir}/apps/observability/gramly-platform-alerts.yaml"
kubectl apply -f "${infra_dir}/apps/observability/gramly-welcome-alerts.yaml"

kubectl apply -f "${infra_dir}/apps/observability/tls.yaml"
kubectl wait certificate/grafana-gramly-tech -n traefik-public --for=condition=Ready --timeout=10m
kubectl wait certificate/cluster-gramly-tech -n traefik-public --for=condition=Ready --timeout=10m
kubectl apply -f "${infra_dir}/platform/gateway/public-gateway.yaml"
kubectl apply -f "${infra_dir}/apps/observability/routes.yaml"
kubectl apply -f "${infra_dir}/apps/observability/headlamp-network-policy.yaml"
kubectl wait gateway/gramly-public-access -n traefik-public --for=condition=Programmed --timeout=5m

echo "Observability is ready through public HTTPS and its existing SSO restrictions."
