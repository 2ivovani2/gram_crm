#!/usr/bin/env bash
set -euo pipefail

[[ -n "${KUBECONFIG:-}" ]] || {
  echo "KUBECONFIG is required." >&2
  exit 1
}

namespace=gramly-restore-check
cluster=gramly-crm-restore-check
source_namespace=gramly-crm
backup_secret=gramly-cnpg-backup-s3

if [[ "${1:-}" == "cleanup" ]]; then
  kubectl get namespace "${namespace}" >/dev/null 2>&1 || {
    echo "Restore-check namespace is already absent."
    exit 0
  }
  kubectl delete namespace "${namespace}"
  echo "Deleted isolated restore-check namespace ${namespace}."
  exit 0
fi

if kubectl -n "${namespace}" get cluster "${cluster}" >/dev/null 2>&1; then
  echo "${namespace}/${cluster} already exists; inspect it or run '$0 cleanup'." >&2
  exit 1
fi

kubectl create namespace "${namespace}" --dry-run=client -o yaml | kubectl apply -f - >/dev/null
kubectl label namespace "${namespace}" \
  gramly.tech/environment=restore-check \
  gramly.tech/contour=isolated \
  --overwrite >/dev/null

kubectl -n "${source_namespace}" get secret "${backup_secret}" -o json \
  | jq --arg namespace "${namespace}" \
      '.metadata = {name:"gramly-cnpg-backup-s3", namespace:$namespace} |
       del(.metadata.creationTimestamp, .metadata.resourceVersion, .metadata.uid, .metadata.managedFields)' \
  | kubectl apply -f - >/dev/null

kubectl apply -f - >/dev/null <<YAML
apiVersion: barmancloud.cnpg.io/v1
kind: ObjectStore
metadata:
  name: gramly-crm-backups
  namespace: ${namespace}
spec:
  configuration:
    destinationPath: s3://gramly-backups/cnpg/gramly-crm
    endpointURL: https://ewr1.vultrobjects.com
    s3Credentials:
      accessKeyId: {name: gramly-cnpg-backup-s3, key: AWS_ACCESS_KEY_ID}
      secretAccessKey: {name: gramly-cnpg-backup-s3, key: AWS_SECRET_ACCESS_KEY}
      region: {name: gramly-cnpg-backup-s3, key: AWS_REGION}
    wal:
      compression: gzip
      maxParallel: 4
---
apiVersion: postgresql.cnpg.io/v1
kind: Cluster
metadata:
  name: ${cluster}
  namespace: ${namespace}
  labels:
    app.kubernetes.io/name: gramly-cnpg-restore-check
spec:
  instances: 1
  imageName: ghcr.io/cloudnative-pg/postgresql:17.10-standard-trixie@sha256:78c4fdf165e8ffb1b5b7a7fc6b22b3cf37890a338b4c1c8c4d913896129a86da
  bootstrap:
    recovery:
      source: gramly-crm-backup
  externalClusters:
    - name: gramly-crm-backup
      plugin:
        name: barman-cloud.cloudnative-pg.io
        parameters:
          barmanObjectName: gramly-crm-backups
          serverName: gramly-crm-postgres
  storage:
    storageClass: vultr-block-storage
    size: 10Gi
  resources:
    requests: {cpu: 100m, memory: 256Mi}
    limits: {cpu: "1", memory: 1Gi}
  enableSuperuserAccess: true
YAML

echo "Waiting for isolated restore ${namespace}/${cluster}..."
kubectl -n "${namespace}" wait cluster "${cluster}" \
  --for=condition=Ready --timeout=30m

primary_pod="$(kubectl -n "${namespace}" get pods \
  -l "cnpg.io/cluster=${cluster},role=primary" \
  -o jsonpath='{.items[0].metadata.name}')"
[[ -n "${primary_pod}" ]] || {
  echo "Restored primary pod was not found." >&2
  exit 1
}

kubectl -n "${namespace}" exec "${primary_pod}" -c postgres -- \
  psql -d gramly -v ON_ERROR_STOP=1 -Atc \
  "SELECT 'django_migrations=' || count(*) FROM django_migrations;
   SELECT 'welcome_bots=' || count(*) FROM welcome_bots_managedbot;"

echo "Restore drill passed. Inspect the isolated cluster, then run: $0 cleanup"
