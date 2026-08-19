#!/usr/bin/env bash
set -euo pipefail

[[ -n "${KUBECONFIG:-}" ]] || { echo "KUBECONFIG is required." >&2; exit 1; }

namespace=gramly-welcome
secret_name=gramly-welcome-admin-oidc
kubectl get namespace "${namespace}" >/dev/null
kubectl rollout status deployment/authentik-server -n identity --timeout=5m >/dev/null

if kubectl -n "${namespace}" get secret "${secret_name}" >/dev/null 2>&1; then
  client_id="$(kubectl -n "${namespace}" get secret "${secret_name}" -o jsonpath='{.data.client-id}' | base64 --decode)"
  client_secret="$(kubectl -n "${namespace}" get secret "${secret_name}" -o jsonpath='{.data.client-secret}' | base64 --decode)"
  cookie_secret="$(kubectl -n "${namespace}" get secret "${secret_name}" -o jsonpath='{.data.cookie-secret}' | base64 --decode)"
else
  client_id="gramly-welcome-admin-$(openssl rand -hex 8)"
  client_secret="$(openssl rand -hex 32)"
  cookie_secret="$(openssl rand -base64 32 | tr -d '\n' | head -c 32)"
fi

printf '%s\n%s\n' "${client_id}" "${client_secret}" | \
  kubectl exec --stdin -n identity deployment/authentik-server -c server -- ak shell -c \
  "import sys
from django.db import transaction
from authentik.core.models import Application, Group
from authentik.crypto.models import CertificateKeyPair
from authentik.flows.models import Flow
from authentik.policies.models import PolicyBinding
from authentik.providers.oauth2.models import OAuth2Provider, RedirectURI, RedirectURIMatchingMode, RedirectURIType, ScopeMapping

client_id = sys.stdin.readline().strip()
client_secret = sys.stdin.readline().strip()
with transaction.atomic():
    provider, _ = OAuth2Provider.objects.update_or_create(
        name='GramlyHello Control',
        defaults={
            'authentication_flow': Flow.objects.get(slug='default-authentication-flow'),
            'authorization_flow': Flow.objects.get(slug='default-provider-authorization-implicit-consent'),
            'invalidation_flow': Flow.objects.get(slug='default-provider-invalidation-flow'),
            'client_type': 'confidential',
            'client_id': client_id,
            'client_secret': client_secret,
            'grant_types': ['authorization_code', 'refresh_token'],
            'include_claims_in_id_token': True,
            'sub_mode': 'user_username',
            'issuer_mode': 'per_provider',
            'signing_key': CertificateKeyPair.objects.get(name='authentik Self-signed Certificate'),
        },
    )
    provider.redirect_uris = [RedirectURI(
        RedirectURIMatchingMode('strict'),
        'https://hello-admin.gramly.tech/oauth2/callback',
        RedirectURIType('authorization'),
    )]
    provider.save(update_fields=['_redirect_uris'])
    provider.property_mappings.set(ScopeMapping.objects.filter(
        scope_name__in=['openid', 'profile', 'email', 'entitlements'],
    ))
    application, _ = Application.objects.update_or_create(
        slug='welcome-admin',
        defaults={
            'name': 'GramlyHello Control',
            'provider': provider,
            'meta_description': 'Private owner console for GramlyHello',
            'policy_engine_mode': 'any',
        },
    )
    PolicyBinding.objects.filter(target=application).delete()
    for order, group_name in enumerate(('gramly-owners', 'authentik Admins')):
        group, _ = Group.objects.get_or_create(name=group_name, defaults={'is_superuser': False})
        PolicyBinding.objects.create(target=application, group=group, order=order, enabled=True)
print('GRAMLY_RESULT welcome_admin_oidc_ready=true')" \
  2>&1 | grep 'GRAMLY_RESULT welcome_admin_oidc_ready=true' >/dev/null

config_file="$(mktemp)"
trap 'rm -f "${config_file}"; unset client_id client_secret cookie_secret' EXIT
cat >"${config_file}" <<EOF
provider = "oidc"
provider_display_name = "Gramly SSO"
http_address = "0.0.0.0:4180"
oidc_issuer_url = "https://auth.gramly.tech/application/o/welcome-admin/"
redirect_url = "https://hello-admin.gramly.tech/oauth2/callback"
upstreams = ["http://gramly-welcome-web.gramly-welcome.svc.cluster.local:8080/"]
client_id = "${client_id}"
client_secret = "${client_secret}"
cookie_secret = "${cookie_secret}"
email_domains = ["*"]
scope = "openid profile email entitlements"
allowed_groups = ["gramly-owners", "authentik Admins"]
pass_access_token = false
pass_authorization_header = false
pass_user_headers = true
set_xauthrequest = true
reverse_proxy = true
trusted_proxy_ips = ["10.244.0.0/16"]
cookie_secure = true
cookie_name = "_gramly_welcome_admin"
cookie_samesite = "strict"
cookie_expire = "8h"
cookie_refresh = "1h"
silence_ping_logging = true
code_challenge_method = "S256"
skip_provider_button = true
whitelist_domains = [".gramly.tech"]
EOF

kubectl -n "${namespace}" create secret generic "${secret_name}" \
  --from-literal=client-id="${client_id}" \
  --from-literal=client-secret="${client_secret}" \
  --from-literal=cookie-secret="${cookie_secret}" \
  --from-file=oauth2-proxy.cfg="${config_file}" \
  --dry-run=client -o yaml | kubectl apply -f - >/dev/null

echo "GramlyHello Control OIDC and owner-only access are ready."
