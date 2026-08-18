#!/usr/bin/env bash
set -euo pipefail
export LC_ALL=C

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
      end_wal="$(kubectl -n "${namespace}" get backup "${backup_name}" \
        -o jsonpath='{.status.endWal}')"
      [[ "${end_wal}" =~ ^[0-9A-F]{24}$ ]] || {
        echo "Backup completed without a valid end WAL." >&2
        exit 1
      }
      primary_pod="$(kubectl -n "${namespace}" get cluster "${cluster}" \
        -o jsonpath='{.status.currentPrimary}')"
      [[ -n "${primary_pod}" ]] || {
        echo "Primary pod was not reported after backup completion." >&2
        exit 1
      }

      # A completed online base backup is not restorable until its final WAL
      # segment reaches object storage. Force a normal WAL switch and verify
      # pg_stat_archiver before declaring the operational backup complete.
      kubectl -n "${namespace}" exec "${primary_pod}" -c postgres -- \
        psql -d postgres -v ON_ERROR_STOP=1 -Atc 'SELECT pg_switch_wal();' >/dev/null
      archive_deadline=$((SECONDS + 300))
      while ((SECONDS < archive_deadline)); do
        archived_wal="$(kubectl -n "${namespace}" exec "${primary_pod}" -c postgres -- \
          psql -d postgres -v ON_ERROR_STOP=1 -Atc \
          'SELECT last_archived_wal FROM pg_stat_archiver;' 2>/dev/null || true)"
        if [[ "${archived_wal}" =~ ^[0-9A-F]{24}$ ]] \
          && [[ "${archived_wal}" == "${end_wal}" || "${archived_wal}" > "${end_wal}" ]]; then
          echo "Backup WAL is archived through ${archived_wal}."
          exit 0
        fi
        sleep 5
      done
      echo "Backup completed, but WAL ${end_wal} was not archived within 5 minutes." >&2
      exit 1
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
