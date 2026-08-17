#!/usr/bin/env bash
set -euo pipefail

root_dir="$(cd "$(dirname "${BASH_SOURCE[0]}")/../../.." && pwd)"
infra_dir="${root_dir}/infra/kubernetes"
source "${infra_dir}/bootstrap/versions.env"
[[ -n "${KUBECONFIG:-}" ]] || { echo "KUBECONFIG is required." >&2; exit 1; }

helm repo add kedacore https://kedacore.github.io/charts --force-update >/dev/null
helm repo update kedacore >/dev/null
helm upgrade --install keda kedacore/keda \
  --version "${KEDA_CHART_VERSION}" --namespace keda --create-namespace \
  --values "${infra_dir}/platform/keda/values.yaml" --wait --timeout 10m
kubectl wait --for=condition=Established crd/scaledobjects.keda.sh --timeout=2m
kubectl -n keda rollout status deployment/keda-operator --timeout=5m
kubectl -n keda rollout status deployment/keda-operator-metrics-apiserver --timeout=5m
echo "KEDA ${KEDA_CHART_VERSION} is ready."
