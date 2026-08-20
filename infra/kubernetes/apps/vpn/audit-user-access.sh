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

readonly authentik_username="$1"
readonly api_url="https://vpn.gramly.tech/api"
if [[ ! "${authentik_username}" =~ ^[A-Za-z0-9_.@-]+$ ]]; then
  echo "Username contains unsupported characters." >&2
  exit 1
fi

for command_name in kubectl curl jq base64; do
  command -v "${command_name}" >/dev/null || {
    echo "${command_name} is required." >&2
    exit 1
  }
done

task_tmp="$(mktemp -d)"
trap 'rm -rf "${task_tmp}"; unset api_token' EXIT
umask 077

authentik_output="$(kubectl exec --namespace identity deployment/authentik-server \
  --container server -- env GRAMLY_AUDIT_USERNAME="${authentik_username}" \
  ak shell -c '
import json, os
from authentik.core.models import User
username = os.environ["GRAMLY_AUDIT_USERNAME"]
user = User.objects.filter(username=username).first()
print("GRAMLY_AUDIT=" + json.dumps(None if user is None else {
    "uid": str(user.pk),
    "username": user.username,
    "name": user.name,
    "email": user.email,
    "is_active": user.is_active,
    "groups": sorted(user.groups.values_list("name", flat=True)),
}, ensure_ascii=False))
' 2>&1)"
authentik_json="$(sed -n 's/^GRAMLY_AUDIT=//p' <<<"${authentik_output}" | tail -n 1)"

if [[ -z "${authentik_json}" ]] || [[ "${authentik_json}" == "null" ]]; then
  if [[ -z "${authentik_json}" ]]; then
    tail -n 8 <<<"${authentik_output}" >&2
  fi
  echo "Authentik user ${authentik_username} was not found." >&2
  exit 2
fi

api_token="$(kubectl get secret netbird-mgmt-api-key \
  --namespace vpn --output jsonpath='{.data.NB_API_KEY}' | base64 --decode)"
curl_config="${task_tmp}/curl.conf"
printf 'silent\nshow-error\nfail\nconnect-timeout = 10\nmax-time = 45\nretry = 2\nretry-all-errors\nheader = "Authorization: Token %s"\nheader = "Accept: application/json"\n' \
  "${api_token}" >"${curl_config}"
chmod 600 "${curl_config}"

for endpoint in accounts users peers groups policies dns/zones; do
  filename="${endpoint//\//-}.json"
  curl --config "${curl_config}" "${api_url}/${endpoint}" >"${task_tmp}/${filename}"
done

authentik_uid="$(jq -r '.uid' <<<"${authentik_json}")"
authentik_email="$(jq -r '.email // "" | ascii_downcase' <<<"${authentik_json}")"
authentik_name="$(jq -r '.name // ""' <<<"${authentik_json}")"

netbird_user="$(jq -c \
  --arg uid "${authentik_uid}" \
  --arg email "${authentik_email}" \
  --arg name "${authentik_name}" \
  --slurpfile peers "${task_tmp}/peers.json" '
    [.[] | select(
        (.idp_id // "") == $uid or
        (($email | length) > 0 and ((.email // "") | ascii_downcase) == $email) or
        (($name | length) > 0 and (.name // "") == $name)
      )
      | . as $user
      | . + {audit_peer_count: ([$peers[0][] | select(.user_id == $user.id)] | length)}
    ]
    | sort_by(.audit_peer_count)
    | reverse
    | first // null
  ' "${task_tmp}/users.json")"

netbird_user_id="$(jq -r '.id // ""' <<<"${netbird_user}")"
peers='[]'
if [[ -n "${netbird_user_id}" ]]; then
  peers="$(jq -c --arg user_id "${netbird_user_id}" \
    '[.[] | select((.user_id // "") == $user_id)]' "${task_tmp}/peers.json")"
fi

jq -n \
  --argjson authentik "${authentik_json}" \
  --argjson netbird_user "${netbird_user}" \
  --argjson peers "${peers}" \
  --slurpfile accounts "${task_tmp}/accounts.json" \
  --slurpfile groups "${task_tmp}/groups.json" \
  --slurpfile policies "${task_tmp}/policies.json" \
  --slurpfile zones "${task_tmp}/dns-zones.json" '
  ($groups[0] // []) as $all_groups |
  {
    authentik: $authentik,
    netbird: {
      identity: (if $netbird_user == null then null else {
        id: $netbird_user.id,
        name: $netbird_user.name,
        email: $netbird_user.email,
        status: $netbird_user.status,
        is_blocked: $netbird_user.is_blocked,
        is_current: $netbird_user.is_current,
        auto_groups: [$all_groups[] as $group | select(($netbird_user.auto_groups // []) | index($group.id)) | $group.name],
        peer_count: $netbird_user.audit_peer_count
      } end),
      account: {
        peer_login_expiration_enabled: $accounts[0][0].settings.peer_login_expiration_enabled,
        peer_login_expiration_seconds: $accounts[0][0].settings.peer_login_expiration,
        groups_propagation_enabled: $accounts[0][0].settings.groups_propagation_enabled,
        jwt_groups_claim_name: $accounts[0][0].settings.jwt_groups_claim_name
      },
      peers: [$peers[] | . as $peer | {
        id, name, ip, os, version, connected, login_expired, approval_required,
        last_seen,
        groups: [$all_groups[] | select([(.peers // [])[] | if type == "object" then .id else . end] | index($peer.id)) | .name]
      }],
      private_dns: [$zones[0][] | select(.domain == "gramly.tech") | {
        domain, enabled, distribution_groups
      }],
      transport_policies: [$policies[0][] | select(
        .name == "gramly-workforce-business" or
        .name == "gramly-workforce-collaboration" or
        .name == "gramly-devops-infrastructure"
      ) | {name, enabled, rules}],
      private_resources: [$all_groups[] | select(
        .name == "gramly-business-services" or
        .name == "gramly-collaboration-services" or
        .name == "gramly-devops-services"
      ) | {group: .name, resources}]
    },
    checks: {
      identity_found: ($netbird_user != null),
      has_peer: (($peers | length) > 0),
      every_peer_connected: (($peers | length) > 0 and all($peers[]; .connected == true)),
      no_expired_peer: all($peers[]; .login_expired != true),
      every_peer_in_all: all($peers[]; . as $peer | any($all_groups[]; .name == "All" and ([((.peers // [])[]) | if type == "object" then .id else . end] | index($peer.id)) != null)),
      expiration_is_seven_days: ($accounts[0][0].settings.peer_login_expiration == 604800),
      group_propagation_enabled: ($accounts[0][0].settings.groups_propagation_enabled == true),
      private_dns_enabled: any($zones[0][]; .domain == "gramly.tech" and .enabled == true)
    }
  }' | jq .
