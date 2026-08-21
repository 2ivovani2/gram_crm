#!/usr/bin/env bash
set -euo pipefail

if [[ $# -ne 1 ]]; then
  echo "Usage: $0 <authentik-username>" >&2
  exit 1
fi
if [[ -z "${KUBECONFIG:-}" ]]; then
  echo "KUBECONFIG is not set; refusing to use an implicit cluster context." >&2
  exit 1
fi

readonly username="$1"
if [[ ! "${username}" =~ ^[A-Za-z0-9_.@-]+$ ]]; then
  echo "Username contains unsupported characters." >&2
  exit 1
fi

audit_output="$(kubectl exec --namespace identity deployment/authentik-server --container server \
  -- env GRAMLY_AUDIT_USERNAME="${username}" ak shell -c '
import json
import os

from authentik.core.models import Application, User
from authentik.policies.models import PolicyBinding
from authentik.providers.oauth2.models import OAuth2Provider

username = os.environ["GRAMLY_AUDIT_USERNAME"]
user = User.objects.filter(username=username).first()
application = Application.objects.filter(slug="welcome-admin").first()
if user is None or application is None:
    print("GRAMLY_AUDIT=" + json.dumps({
        "user_found": user is not None,
        "application_found": application is not None,
        "authorized": False,
    }))
    raise SystemExit(2)

groups = set(user.groups.values_list("name", flat=True))
bindings = list(PolicyBinding.objects.filter(
    target=application,
    enabled=True,
    negate=False,
).select_related("group", "user"))
binding_groups = {binding.group.name for binding in bindings if binding.group_id}
direct_users = {binding.user.username for binding in bindings if binding.user_id}
authorized = bool(
    user.is_active
    and ((groups & binding_groups) or username in direct_users)
)
provider = OAuth2Provider.objects.get(pk=application.provider_id)
print("GRAMLY_AUDIT=" + json.dumps({
    "user_found": True,
    "username": user.username,
    "active": user.is_active,
    "groups": sorted(groups),
    "application": application.slug,
    "policy_engine_mode": application.policy_engine_mode,
    "binding_groups": sorted(binding_groups),
    "direct_binding": username in direct_users,
    "authorized": authorized,
    "issuer": f"https://auth.gramly.tech/application/o/{application.slug}/",
    "redirect_uris": [str(uri) for uri in provider.redirect_uris],
}, ensure_ascii=False))
' 2>&1)"
audit_json="$(sed -n 's/^GRAMLY_AUDIT=//p' <<<"${audit_output}" | tail -n 1)"
if [[ -z "${audit_json}" ]]; then
  tail -n 12 <<<"${audit_output}" >&2
  exit 1
fi
jq . <<<"${audit_json}"

kubectl get httproute gramly-welcome-admin --namespace gramly-welcome \
  --output jsonpath='{range .status.parents[*].conditions[*]}{.type}={.status}{" "}{end}{"\n"}'
kubectl get deployment gramly-welcome-admin-auth gramly-welcome-web \
  --namespace gramly-welcome \
  --output custom-columns=NAME:.metadata.name,READY:.status.readyReplicas,DESIRED:.spec.replicas
