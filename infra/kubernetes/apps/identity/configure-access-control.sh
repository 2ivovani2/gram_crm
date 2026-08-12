#!/usr/bin/env bash
set -euo pipefail

if [[ -z "${KUBECONFIG:-}" ]]; then
  echo "KUBECONFIG is not set; refusing to use an implicit cluster context." >&2
  exit 1
fi

kubectl rollout status deployment/authentik-server \
  --namespace identity --timeout=5m >/dev/null

kubectl exec --namespace identity deployment/authentik-server \
  --container server -- ak shell -c \
  "from django.db import transaction
from authentik.core.models import Application, Group, User
from authentik.policies.models import PolicyBinding

group_names = (
    'gramly-employees',
    'gramly-product',
    'gramly-engineering',
    'gramly-devops',
    'gramly-owners',
)

application_groups = {
    'netbird': group_names,
    'crm': ('gramly-employees', 'gramly-product', 'gramly-devops', 'gramly-owners'),
    'forgejo': ('gramly-product', 'gramly-engineering', 'gramly-devops', 'gramly-owners'),
    'vikunja': group_names,
    'outline': group_names,
    'observability': ('gramly-devops', 'gramly-owners'),
}

with transaction.atomic():
    groups = {
        name: Group.objects.get_or_create(name=name, defaults={'is_superuser': False})[0]
        for name in group_names
    }

    owner = User.objects.filter(username='i_vovani', is_active=True).first()
    if owner is None:
        owner = User.objects.filter(email__iexact='avyaroslavskiy@miem.hse.ru', is_active=True).first()
    if owner is None:
        raise RuntimeError('Gramly owner account was not found in Authentik')
    owner.groups.add(groups['gramly-owners'])

    for slug, allowed_names in application_groups.items():
        application = Application.objects.filter(slug=slug).first()
        if application is None:
            raise RuntimeError(f'Authentik application {slug!r} does not exist')

        application.policy_engine_mode = 'any'
        application.save(update_fields=['policy_engine_mode'])

        allowed_groups = [groups[name] for name in allowed_names]
        PolicyBinding.objects.filter(
            target=application,
            group__in=groups.values(),
        ).exclude(group__in=allowed_groups).delete()

        for order, group in enumerate(allowed_groups):
            binding, _ = PolicyBinding.objects.get_or_create(
                target=application,
                group=group,
                defaults={'order': order, 'enabled': True},
            )
            changed = []
            if not binding.enabled:
                binding.enabled = True
                changed.append('enabled')
            if binding.order != order:
                binding.order = order
                changed.append('order')
            if changed:
                binding.save(update_fields=changed)

print('GRAMLY_RESULT access_control_ready=true')" \
  2>&1 | grep 'GRAMLY_RESULT access_control_ready=true' >/dev/null

echo "Authentik groups and application access bindings are reconciled."
