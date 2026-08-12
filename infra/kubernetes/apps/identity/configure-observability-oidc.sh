#!/usr/bin/env bash
set -euo pipefail

[[ -n "${KUBECONFIG:-}" ]] || { echo "KUBECONFIG is required." >&2; exit 1; }

if kubectl -n observability get secret observability-oidc >/dev/null 2>&1; then
  client_id="$(kubectl -n observability get secret observability-oidc -o jsonpath='{.data.client-id}' | base64 --decode)"
  client_secret="$(kubectl -n observability get secret observability-oidc -o jsonpath='{.data.client-secret}' | base64 --decode)"
else
  client_id="gramly-observability-$(openssl rand -hex 8)"
  client_secret="$(openssl rand -hex 32)"
fi

printf '%s\n%s\n' "${client_id}" "${client_secret}" | kubectl exec --stdin -n identity deployment/authentik-server -c server -- ak shell -c \
  "import sys; from authentik.core.models import Application; from authentik.crypto.models import CertificateKeyPair; from authentik.flows.models import Flow; from authentik.providers.oauth2.models import OAuth2Provider, RedirectURI, RedirectURIMatchingMode, RedirectURIType, ScopeMapping; client_id=sys.stdin.readline().strip(); client_secret=sys.stdin.readline().strip(); provider,_=OAuth2Provider.objects.update_or_create(name='Gramly Observability',defaults={'authentication_flow':Flow.objects.get(slug='default-authentication-flow'),'authorization_flow':Flow.objects.get(slug='default-provider-authorization-implicit-consent'),'invalidation_flow':Flow.objects.get(slug='default-provider-invalidation-flow'),'client_type':'confidential','client_id':client_id,'client_secret':client_secret,'grant_types':['authorization_code','refresh_token'],'include_claims_in_id_token':True,'sub_mode':'user_username','issuer_mode':'per_provider','signing_key':CertificateKeyPair.objects.get(name='authentik Self-signed Certificate')}); provider.redirect_uris=[RedirectURI(RedirectURIMatchingMode('strict'),'https://grafana.gramly.tech/login/generic_oauth',RedirectURIType('authorization')),RedirectURI(RedirectURIMatchingMode('strict'),'https://cluster.gramly.tech/oauth2/callback',RedirectURIType('authorization'))]; provider.save(update_fields=['_redirect_uris']); provider.property_mappings.set(ScopeMapping.objects.filter(scope_name__in=['openid','email','profile','entitlements'])); Application.objects.update_or_create(slug='observability',defaults={'name':'Gramly Observability','provider':provider,'meta_description':'Private cluster monitoring for DevOps administrators'}); print('GRAMLY_RESULT observability_oidc_ready=true')" \
  2>&1 | grep 'GRAMLY_RESULT observability_oidc_ready=true' >/dev/null

kubectl -n observability create secret generic observability-oidc \
  --from-literal=client-id="${client_id}" --from-literal=client-secret="${client_secret}" \
  --dry-run=client -o yaml | kubectl apply -f - >/dev/null
kubectl -n observability create secret generic grafana-oidc \
  --from-literal=GRAFANA_OIDC_CLIENT_ID="${client_id}" \
  --from-literal=GRAFANA_OIDC_CLIENT_SECRET="${client_secret}" \
  --dry-run=client -o yaml | kubectl apply -f - >/dev/null

if kubectl -n observability get secret observability-oauth2-proxy >/dev/null 2>&1; then
  cookie_secret="$(kubectl -n observability get secret observability-oauth2-proxy -o jsonpath='{.data.cookie-secret}' | base64 --decode)"
else
  cookie_secret="$(openssl rand -base64 32 | tr -d '\n' | head -c 32)"
fi
kubectl -n observability create secret generic observability-oauth2-proxy \
  --from-literal=client-id="${client_id}" --from-literal=client-secret="${client_secret}" \
  --from-literal=cookie-secret="${cookie_secret}" \
  --dry-run=client -o yaml | kubectl apply -f - >/dev/null

unset client_id client_secret cookie_secret
