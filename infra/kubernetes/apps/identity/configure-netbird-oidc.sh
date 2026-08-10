#!/usr/bin/env bash
set -euo pipefail

if [[ -z "${KUBECONFIG:-}" ]]; then
  echo "KUBECONFIG is not set; refusing to use an implicit cluster context." >&2
  exit 1
fi

namespace="identity"
secret_name="netbird-authentik-oidc"
issuer="https://auth.gramly.tech/application/o/netbird/"

if kubectl get secret "${secret_name}" --namespace "${namespace}" >/dev/null 2>&1; then
  client_id="$(kubectl get secret "${secret_name}" --namespace "${namespace}" --output jsonpath='{.data.client-id}' | base64 --decode)"
  client_secret="$(kubectl get secret "${secret_name}" --namespace "${namespace}" --output jsonpath='{.data.client-secret}' | base64 --decode)"
else
  client_id="$(openssl rand -hex 16)"
  client_secret="$(openssl rand -hex 32)"

  jq --null-input \
    --arg namespace "${namespace}" \
    --arg name "${secret_name}" \
    --arg client_id "${client_id}" \
    --arg client_secret "${client_secret}" \
    --arg issuer "${issuer}" \
    '{
      apiVersion: "v1",
      kind: "Secret",
      metadata: {namespace: $namespace, name: $name},
      type: "Opaque",
      stringData: {
        "client-id": $client_id,
        "client-secret": $client_secret,
        issuer: $issuer
      }
    }' | kubectl apply --filename - >/dev/null
fi

printf '%s\n%s\n' "${client_id}" "${client_secret}" | \
  kubectl exec --stdin --namespace "${namespace}" deployment/authentik-server \
    --container server -- ak shell -c \
    "import sys; from authentik.core.models import Application; from authentik.crypto.models import CertificateKeyPair; from authentik.flows.models import Flow; from authentik.providers.oauth2.models import OAuth2Provider, RedirectURI, RedirectURIMatchingMode, RedirectURIType, ScopeMapping; client_id=sys.stdin.readline().strip(); client_secret=sys.stdin.readline().strip(); provider, _=OAuth2Provider.objects.update_or_create(name='Gramly NetBird', defaults={'authentication_flow': Flow.objects.get(slug='default-authentication-flow'), 'authorization_flow': Flow.objects.get(slug='default-provider-authorization-implicit-consent'), 'invalidation_flow': Flow.objects.get(slug='default-provider-invalidation-flow'), 'client_type': 'confidential', 'client_id': client_id, 'client_secret': client_secret, 'grant_types': ['authorization_code', 'refresh_token'], 'include_claims_in_id_token': True, 'sub_mode': 'hashed_user_id', 'issuer_mode': 'per_provider', 'signing_key': CertificateKeyPair.objects.get(name='authentik Self-signed Certificate')}); provider.redirect_uris=[RedirectURI(RedirectURIMatchingMode('strict'),'https://vpn.gramly.tech/oauth2/callback',RedirectURIType('authorization')),RedirectURI(RedirectURIMatchingMode('strict'),'https://vpn.gramly.tech/oauth2/logout/callback',RedirectURIType('logout'))]; provider.save(update_fields=['_redirect_uris']); provider.property_mappings.set(ScopeMapping.objects.filter(scope_name__in=['openid','email','profile','entitlements'])); Application.objects.update_or_create(slug='netbird', defaults={'name':'NetBird VPN','provider':provider,'meta_description':'Secure access to Gramly internal services'}); print('GRAMLY_RESULT netbird_oidc_ready=true')" \
  2>&1 | grep 'GRAMLY_RESULT netbird_oidc_ready=true' >/dev/null

unset client_secret
echo "Authentik OIDC application for NetBird is ready; credentials are stored in Kubernetes only."
