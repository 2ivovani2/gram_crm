#!/usr/bin/env bash
set -euo pipefail

if [[ -z "${KUBECONFIG:-}" ]]; then
  echo "KUBECONFIG is not set; refusing to use an implicit cluster context." >&2
  exit 1
fi

username="${1:-}"
if [[ -z "${username}" ]]; then
  echo "Usage: $0 <username>" >&2
  exit 1
fi

printf '%s\n' "${username}" | kubectl exec -i -n identity \
  deployment/authentik-server -c server -- ak shell -c \
  "import sys; from authentik.core.models import User; username=sys.stdin.readline().strip(); user=User.objects.get(username=username); attrs=dict(user.attributes); attrs['reset_password']=True; user.attributes=attrs; user.save(update_fields=['attributes']); print('GRAMLY_RESULT temporary_password_marked=true')" \
  2>&1 | grep 'GRAMLY_RESULT temporary_password_marked=true' >/dev/null

echo "User ${username} must change the temporary password on next login."
