#!/usr/bin/env bash
set -euo pipefail

if [[ -z "${KUBECONFIG:-}" ]]; then
  echo "KUBECONFIG is not set; refusing to use an implicit cluster context." >&2
  exit 1
fi

kubectl exec -n identity deployment/authentik-server -- ak shell -c \
  "import os; from authentik.core.models import User, Group; u,created=User.objects.get_or_create(username='i_vovani',defaults={'name':'Alexander Yaroslavskiy','email':'avyaroslavskiy@miem.hse.ru','is_active':True,'type':'internal','path':'users'}); u.name='Alexander Yaroslavskiy'; u.email='avyaroslavskiy@miem.hse.ru'; u.is_active=True; u.set_password(os.environ['GRAMLY_INITIAL_ADMIN_PASSWORD']) if created else None; u.save(); u.groups.add(Group.objects.get(name='authentik Admins'))"

echo "Authentik administrator i_vovani is present. Password was not printed."
