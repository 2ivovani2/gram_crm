#!/usr/bin/env bash
set -euo pipefail

root_dir="$(cd "$(dirname "${BASH_SOURCE[0]}")/../../.." && pwd)"
infra_dir="${root_dir}/infra/kubernetes"

# shellcheck source=/dev/null
source "${infra_dir}/bootstrap/versions.env"

if [[ -z "${KUBECONFIG:-}" ]]; then
  echo "KUBECONFIG is not set; refusing to use an implicit cluster context." >&2
  exit 1
fi

namespace=devtools
connection_secret=forgejo-runner-connection

kubectl --namespace "${namespace}" rollout status deployment/forgejo --timeout=5m

if ! kubectl --namespace "${namespace}" get secret "${connection_secret}" >/dev/null 2>&1; then
  runner_secret="$(openssl rand -hex 20)"
  runner_uuid="$(printf '%s' "${runner_secret}" | \
    kubectl --namespace "${namespace}" exec --stdin deployment/forgejo -- \
      su git -c 'forgejo forgejo-cli actions register --name gramly-ci --scope gramly/gram_crm --labels ubuntu-latest --secret-stdin=-' | \
    tr -d '\r\n')"

  if [[ ! "${runner_uuid}" =~ ^[0-9a-fA-F-]{36}$ ]]; then
    echo "Forgejo did not return a valid runner UUID; refusing to create the connection secret." >&2
    exit 1
  fi

  config_file="$(mktemp)"
  trap 'rm -f "${config_file}"' EXIT
  cat >"${config_file}" <<EOF
log:
  level: info
  job_level: info
runner:
  capacity: 1
  timeout: 3h
  shutdown_timeout: 3h
  insecure: false
  fetch_timeout: 30s
  fetch_interval: 2s
  report_interval: 1s
  envs:
    DOCKER_HOST: tcp://172.17.0.1:2375
cache:
  enabled: false
container:
  network: bridge
  privileged: false
  valid_volumes: []
  docker_host: tcp://172.17.0.1:2375
  force_pull: true
server:
  connections:
    gramly:
      url: http://forgejo.devtools.svc.cluster.local:3000/
      uuid: ${runner_uuid}
      token: ${runner_secret}
      labels:
        ubuntu-latest:
          backend: docker
          backend-options:
            image: ${FORGEJO_JOB_IMAGE}
EOF

  kubectl --namespace "${namespace}" create secret generic "${connection_secret}" \
    --from-file=config.yml="${config_file}"
fi

sed \
  -e "s|FORGEJO_RUNNER_IMAGE_PLACEHOLDER|${FORGEJO_RUNNER_IMAGE}|" \
  -e "s|FORGEJO_DIND_IMAGE_PLACEHOLDER|${FORGEJO_DIND_IMAGE}|" \
  "${infra_dir}/apps/devtools/forgejo-runner.yaml" | kubectl apply --filename -

kubectl --namespace "${namespace}" rollout status deployment/forgejo-runner --timeout=10m
kubectl --namespace "${namespace}" get pods \
  --selector app.kubernetes.io/name=forgejo-runner --output wide

echo "Repository-scoped Forgejo runner is online (capacity=1, isolated DinD)."
