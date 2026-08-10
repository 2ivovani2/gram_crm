#!/usr/bin/env bash
set -euo pipefail

if [[ -z "${KUBECONFIG:-}" ]]; then
  echo "KUBECONFIG is not set; refusing to use an implicit cluster context." >&2
  exit 1
fi

kubectl exec -n identity deployment/authentik-server -c server -- ak shell -c \
  "from authentik.core.models import User; from authentik.stages.authenticator_totp.models import TOTPDevice; from authentik.stages.authenticator_validate.models import AuthenticatorValidateStage; u=User.objects.get(username='i_vovani'); assert u.is_active and u.has_usable_password(), 'i_vovani must have an active account and a permanent password'; assert TOTPDevice.objects.filter(user=u,confirmed=True).exists(), 'i_vovani must enroll confirmed TOTP before MFA enforcement'; s=AuthenticatorValidateStage.objects.get(name='default-authentication-mfa-validation'); s.not_configured_action='deny'; s.save(update_fields=['not_configured_action']); bootstrap=User.objects.filter(username='akadmin').first(); setattr(bootstrap,'is_active',False) if bootstrap else None; bootstrap.save(update_fields=['is_active']) if bootstrap else None" \
  >/dev/null

kubectl patch secret authentik-runtime --namespace identity --type=merge \
  --patch='{"data":{"AUTHENTIK_BOOTSTRAP_PASSWORD":null,"AUTHENTIK_BOOTSTRAP_TOKEN":null,"GRAMLY_INITIAL_ADMIN_PASSWORD":null}}' \
  >/dev/null

echo "MFA is mandatory. The bootstrap administrator and reusable bootstrap credentials are disabled."
