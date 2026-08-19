#!/usr/bin/env bash
set -euo pipefail

repo_root="$(cd "$(dirname "${BASH_SOURCE[0]}")/../.." && pwd)"
infra_dir="${repo_root}/infra/kubernetes"
: "${KUBECONFIG:?Set KUBECONFIG to the production VKE kubeconfig}"
: "${WELCOME_IMAGE:?Set WELCOME_IMAGE to an immutable image@sha256 digest}"
: "${WELCOME_WEB_IMAGE:?Set WELCOME_WEB_IMAGE to an immutable image@sha256 digest}"

digest_pattern='^.+@sha256:[a-f0-9]{64}$'
[[ "${WELCOME_IMAGE}" =~ ${digest_pattern} ]] || { echo "WELCOME_IMAGE must use an immutable sha256 digest." >&2; exit 1; }
[[ "${WELCOME_WEB_IMAGE}" =~ ${digest_pattern} ]] || { echo "WELCOME_WEB_IMAGE must use an immutable sha256 digest." >&2; exit 1; }

current_context="$(kubectl config current-context)"
[[ "${current_context}" == "admin@vke-79dd127e-498c-4250-a75a-eb8f85778d0b" ]] || {
  echo "Refusing unexpected Kubernetes context: ${current_context}." >&2
  exit 1
}
kubectl get cluster gramly-crm-postgres -n gramly-crm \
  -o jsonpath='{.status.phase}' | grep -qx 'Cluster in healthy state'
kubectl -n gramly-welcome get secret gramly-welcome-runtime gramly-welcome-registry >/dev/null

public_edge_ip="${PUBLIC_EDGE_IP:-45.146.131.207}"
authoritative_ns="$(dig +short NS gramly.tech | head -n 1)"
[[ -n "${authoritative_ns}" ]] || { echo "No authoritative DNS server found for gramly.tech." >&2; exit 1; }
resolved_admin="$(dig +short "@${authoritative_ns}" A hello-admin.gramly.tech | tail -n 1)"
[[ "${resolved_admin}" == "${public_edge_ip}" ]] || {
  echo "hello-admin.gramly.tech must resolve publicly to ${public_edge_ip}; got ${resolved_admin:-no A record}." >&2
  exit 1
}

"${infra_dir}/scripts/run-cnpg-backup.sh"

kubectl apply -f "${infra_dir}/platform/gateway/public-gateway.yaml"
current_crm_image="$(kubectl -n gramly-hello get deployment gramly-public-web -o jsonpath='{.spec.template.spec.containers[0].image}')"
public_allowed_hosts="gramly.tech,www.gramly.tech,hello.gramly.tech,hello-admin.gramly.tech,crm.gramly.tech"
kubectl -n gramly-hello patch secret gramly-public-runtime --type=merge \
  -p "{\"stringData\":{\"ALLOWED_HOSTS\":\"${public_allowed_hosts}\"}}" >/dev/null
kubectl kustomize "${infra_dir}/base/public-web" | \
  sed "s|CRM_IMAGE_PLACEHOLDER|${current_crm_image}|g" | kubectl apply -f -

kubectl apply -f "${infra_dir}/base/welcome-web/hello-admin-tls.yaml"
kubectl wait certificate/hello-admin-gramly-tech -n traefik-public \
  --for=condition=Ready --timeout=10m
"${infra_dir}/scripts/deploy-private-admin-gateway.sh"
"${infra_dir}/apps/identity/configure-welcome-admin-oidc.sh"

kubectl -n gramly-welcome delete job gramly-welcome-migrate --ignore-not-found=true
kubectl kustomize "${infra_dir}/overlays/production/welcome-migrations" | \
  sed "s|WELCOME_IMAGE_PLACEHOLDER|${WELCOME_IMAGE}|g" | kubectl apply -f -
kubectl -n gramly-welcome wait job/gramly-welcome-migrate \
  --for=condition=Complete --timeout=10m

kubectl kustomize "${infra_dir}/overlays/production/welcome-cutover" | \
  sed -e "s|WELCOME_IMAGE_PLACEHOLDER|${WELCOME_IMAGE}|g" \
      -e "s|WELCOME_WEB_IMAGE_PLACEHOLDER|${WELCOME_WEB_IMAGE}|g" | kubectl apply -f -
kubectl apply -f "${infra_dir}/apps/observability/gramly-welcome-dashboard.yaml"
kubectl apply -f "${infra_dir}/apps/observability/gramly-welcome-alerts.yaml"

kubectl -n gramly-welcome rollout status deployment/gramly-welcome-api --timeout=10m
kubectl -n gramly-welcome exec deployment/gramly-welcome-api -- welcome-reconcile-interface-webhook
"${infra_dir}/apps/vpn/ensure-private-app-records.sh"
"${repo_root}/ops/welcome/production_smoke.sh"

echo "GramlyHello production release completed with immutable images."
