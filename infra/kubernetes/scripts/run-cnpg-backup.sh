#!/usr/bin/env bash
set -euo pipefail

[[ -n "${KUBECONFIG:-}" ]] || {
  echo "KUBECONFIG is required." >&2
  exit 1
}

namespace=gramly-crm
cluster=gramly-crm-postgres
backup_name="gramly-crm-manual-$(date -u +%Y%m%d%H%M%S)"

kubectl -n "${namespace}" create -f - >/dev/null <<YAML
apiVersion: postgresql.cnpg.io/v1
kind: Backup
metadata:
  name: ${backup_name}
  namespace: ${namespace}
  labels:
    app.kubernetes.io/name: gramly-cnpg-backup
spec:
  cluster:
    name: ${cluster}
  method: plugin
  pluginConfiguration:
    name: barman-cloud.cloudnative-pg.io
  target: prefer-standby
YAML

echo "Waiting for ${namespace}/${backup_name}..."
deadline=$((SECONDS + 1800))
while ((SECONDS < deadline)); do
  phase="$(kubectl -n "${namespace}" get backup "${backup_name}" \
    -o jsonpath='{.status.phase}' 2>/dev/null || true)"
  case "${phase}" in
    completed)
      kubectl -n "${namespace}" get backup "${backup_name}" \
        -o custom-columns='NAME:.metadata.name,PHASE:.status.phase,STARTED:.status.startedAt,STOPPED:.status.stoppedAt'
      exit 0
      ;;
    failed)
      kubectl -n "${namespace}" describe backup "${backup_name}" >&2
      exit 1
      ;;
  esac
  sleep 10
done

echo "Timed out waiting for ${namespace}/${backup_name}." >&2
exit 1
