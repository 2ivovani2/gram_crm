#!/usr/bin/env bash
set -euo pipefail

[[ -n "${KUBECONFIG:-}" ]] || { echo "KUBECONFIG is required." >&2; exit 1; }

secret_name="forgejo-oidc"
auth_name="Gramly"
callback_url="https://git.gramly.tech/user/oauth2/${auth_name}/callback"
discovery_url="https://auth.gramly.tech/application/o/forgejo/.well-known/openid-configuration"

if kubectl -n devtools get secret "${secret_name}" >/dev/null 2>&1; then
  client_id="$(kubectl -n devtools get secret "${secret_name}" -o jsonpath='{.data.client-id}' | base64 --decode)"
  client_secret="$(kubectl -n devtools get secret "${secret_name}" -o jsonpath='{.data.client-secret}' | base64 --decode)"
else
  client_id="$(openssl rand -hex 16)"
  client_secret="$(openssl rand -hex 32)"
  kubectl -n devtools create secret generic "${secret_name}" \
    --from-literal=client-id="${client_id}" \
    --from-literal=client-secret="${client_secret}" >/dev/null
fi

printf '%s\n%s\n%s\n' "${client_id}" "${client_secret}" "${callback_url}" | \
  kubectl exec --stdin -n identity deployment/authentik-server -c server -- ak shell -c \
  "import sys; from authentik.core.models import Application; from authentik.crypto.models import CertificateKeyPair; from authentik.flows.models import Flow; from authentik.providers.oauth2.models import OAuth2Provider, RedirectURI, RedirectURIMatchingMode, RedirectURIType, ScopeMapping; client_id=sys.stdin.readline().strip(); client_secret=sys.stdin.readline().strip(); callback=sys.stdin.readline().strip(); provider,_=OAuth2Provider.objects.update_or_create(name='Gramly Forgejo', defaults={'authentication_flow':Flow.objects.get(slug='default-authentication-flow'),'authorization_flow':Flow.objects.get(slug='default-provider-authorization-implicit-consent'),'invalidation_flow':Flow.objects.get(slug='default-provider-invalidation-flow'),'client_type':'confidential','client_id':client_id,'client_secret':client_secret,'grant_types':['authorization_code','refresh_token'],'include_claims_in_id_token':True,'sub_mode':'user_username','issuer_mode':'per_provider','signing_key':CertificateKeyPair.objects.get(name='authentik Self-signed Certificate')}); provider.redirect_uris=[RedirectURI(RedirectURIMatchingMode('strict'),callback,RedirectURIType('authorization'))]; provider.save(update_fields=['_redirect_uris']); provider.property_mappings.set(ScopeMapping.objects.filter(scope_name__in=['openid','email','profile'])); Application.objects.update_or_create(slug='forgejo',defaults={'name':'Gramly Git','provider':provider,'meta_description':'Internal source control'}); print('GRAMLY_RESULT forgejo_oidc_ready=true')" \
  2>&1 | grep 'GRAMLY_RESULT forgejo_oidc_ready=true' >/dev/null

auth_id="$(kubectl -n devtools exec deployment/forgejo -- \
  su git -c 'forgejo admin auth list --config /data/gitea/conf/app.ini' | \
  awk -v name="${auth_name}" '$2 == name {print $1; exit}')"

forgejo_auth_args=(
  --config /data/gitea/conf/app.ini
  --name "${auth_name}"
  --provider openidConnect
  --key "${client_id}"
  --secret "${client_secret}"
  --auto-discover-url "${discovery_url}"
  --scopes openid
  --scopes profile
  --scopes email
  --skip-local-2fa
  --group-claim-name groups
  --admin-group "authentik Admins"
)

if [[ -n "${auth_id}" ]]; then
  kubectl -n devtools exec deployment/forgejo -- su git -c \
    "forgejo admin auth update-oauth --id ${auth_id} $(printf '%q ' "${forgejo_auth_args[@]}")" >/dev/null
else
  kubectl -n devtools exec deployment/forgejo -- su git -c \
    "forgejo admin auth add-oauth $(printf '%q ' "${forgejo_auth_args[@]}")" >/dev/null
fi

unset client_secret
echo "Forgejo OIDC authentication source is ready."
