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

kubectl apply -f "${infra_dir}/apps/observability/tls.yaml"
kubectl wait certificate/grafana-gramly-tech -n traefik-public --for=condition=Ready --timeout=10m
kubectl wait certificate/cluster-gramly-tech -n traefik-public --for=condition=Ready --timeout=10m
for secret_name in grafana-gramly-tech-tls cluster-gramly-tech-tls; do
  kubectl -n traefik-public get secret "${secret_name}" -o json \
    | jq '{apiVersion:"v1", kind:"Secret", metadata:{name:.metadata.name, namespace:"traefik-private"}, type:.type, data:.data}' \
    | kubectl apply -f - >/dev/null
done
bootstrap_job="observability-tls-sync-$(date +%s)"
kubectl -n traefik-private create job "${bootstrap_job}" \
  --from=cronjob/observability-tls-sync >/dev/null
kubectl -n traefik-private wait "job/${bootstrap_job}" \
  --for=condition=Complete --timeout=2m
kubectl apply -f "${infra_dir}/platform/gateway/private-access-gateways.yaml"
kubectl apply -f "${infra_dir}/platform/gateway/private-ingress-network-resource.yaml"
kubectl apply -f "${infra_dir}/apps/observability/routes.yaml"
kubectl wait gateway/gramly-infrastructure -n traefik-private --for=condition=Programmed --timeout=5m
"${infra_dir}/apps/vpn/ensure-private-app-records.sh"

echo "Observability is ready on the DevOps-only private access plane."
