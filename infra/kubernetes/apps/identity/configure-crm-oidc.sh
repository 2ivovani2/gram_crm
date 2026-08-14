#!/usr/bin/env bash
set -euo pipefail
[[ -n "${KUBECONFIG:-}" ]] || { echo "KUBECONFIG is required." >&2; exit 1; }

if kubectl -n gramly-crm get secret crm-oidc >/dev/null 2>&1; then
  client_id="$(kubectl -n gramly-crm get secret crm-oidc -o jsonpath='{.data.CRM_OIDC_CLIENT_ID}' | base64 --decode)"
  client_secret="$(kubectl -n gramly-crm get secret crm-oidc -o jsonpath='{.data.CRM_OIDC_CLIENT_SECRET}' | base64 --decode)"
else
  client_id="$(openssl rand -hex 16)"
  client_secret="$(openssl rand -hex 32)"
  kubectl -n gramly-crm create secret generic crm-oidc \
    --from-literal=CRM_OIDC_CLIENT_ID="${client_id}" \
    --from-literal=CRM_OIDC_CLIENT_SECRET="${client_secret}" >/dev/null
fi

python_code="$(cat <<'PY'
import sys

from authentik.core.models import Application
from authentik.crypto.models import CertificateKeyPair
from authentik.flows.models import Flow
from authentik.providers.oauth2.models import (
    OAuth2Provider,
    RedirectURI,
    RedirectURIMatchingMode,
    RedirectURIType,
    ScopeMapping,
)

client_id = sys.stdin.readline().strip()
client_secret = sys.stdin.readline().strip()

provider, _ = OAuth2Provider.objects.update_or_create(
    name="Gramly CRM",
    defaults={
        "authentication_flow": Flow.objects.get(slug="default-authentication-flow"),
        "authorization_flow": Flow.objects.get(slug="default-provider-authorization-implicit-consent"),
        "invalidation_flow": Flow.objects.get(slug="default-provider-invalidation-flow"),
        "client_type": "confidential",
        "client_id": client_id,
        "client_secret": client_secret,
        "grant_types": ["authorization_code", "refresh_token"],
        "include_claims_in_id_token": True,
        "sub_mode": "hashed_user_id",
        "issuer_mode": "per_provider",
        "signing_key": CertificateKeyPair.objects.get(name="authentik Self-signed Certificate"),
    },
)
provider.redirect_uris = [
    RedirectURI(
        RedirectURIMatchingMode("strict"),
        "https://crm.gramly.tech/oidc/callback/",
        RedirectURIType("authorization"),
    )
]
provider.save(update_fields=["_redirect_uris"])

crm_identity_mapping, _ = ScopeMapping.objects.update_or_create(
    name="Gramly CRM Telegram ID",
    defaults={
        "scope_name": "profile",
        "description": "Stable Telegram user ID used only for initial CRM identity binding.",
        "expression": """raw = request.user.attributes.get(\"gramly_crm_telegram_id\")
if not raw:
    return {}
telegram_id = str(raw).strip()
if not telegram_id.isascii() or not telegram_id.isdecimal():
    return {}
return {\"gramly_crm_telegram_id\": telegram_id}""",
    },
)
mappings = list(ScopeMapping.objects.filter(scope_name__in=["openid", "email", "profile"]))
if crm_identity_mapping not in mappings:
    mappings.append(crm_identity_mapping)
provider.property_mappings.set(mappings)

Application.objects.update_or_create(
    slug="crm",
    defaults={
        "name": "Gramly CRM",
        "provider": provider,
        "meta_description": "Private business workspace",
    },
)
print("GRAMLY_RESULT crm_oidc_ready=true telegram_id_claim=true")
PY
)"

printf '%s\n%s\n' "${client_id}" "${client_secret}" | \
  kubectl exec --stdin -n identity deployment/authentik-server -c server -- \
  ak shell -c "${python_code}" 2>&1 | \
  grep 'GRAMLY_RESULT crm_oidc_ready=true telegram_id_claim=true' >/dev/null

unset client_secret
echo "Authentik OIDC application for CRM and explicit Telegram ID claim are ready."
