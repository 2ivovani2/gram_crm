# NetBird connectivity and Hello Admin runbook

## Request path

```text
employee browser
  -> system DNS -> NetBird local managed resolver
     -> private custom zone -> 10.99.132.82/83/84
  -> WireGuard peer -> gramly-private routing peer
  -> private Traefik access plane
  -> Authentik/OIDC (application authorization)
  -> application
```

The private addresses are stable `/32` NetBird Network Resources:

- `10.99.132.82` — infrastructure and `hello-admin`;
- `10.99.132.83` — CRM/business;
- `10.99.132.84` — Git, Tasks and Docs.

Public DNS deliberately resolves closed hosts to the public access gate. A
healthy VPN connection must therefore use NetBird's custom-zone answer. The
`Gramly primary DNS` group assigned to `All` makes the NetBird local resolver
the deterministic system resolver from the first connection. The exact custom
zones are resolved locally and take precedence over its public upstreams.

## Server-side reconciliation

```bash
export KUBECONFIG=/absolute/path/to/vke.yaml
infra/kubernetes/apps/vpn/ensure-primary-dns.sh
infra/kubernetes/apps/vpn/ensure-private-dns-zone.sh
infra/kubernetes/apps/vpn/ensure-access-policies.sh
infra/kubernetes/apps/vpn/enable-authentik-group-sync.sh
```

The primary-DNS script refuses to overwrite a differently named enabled
primary resolver. It also ensures that DNS management is not disabled for the
`All` group. It never changes application permissions.

## Client acceptance test

Disable every non-work VPN before the test. Browser Secure DNS/DoH must use the
operating-system provider; an explicitly forced third-party DoH resolver
bypasses split DNS by design.

### macOS

```bash
netbird status --detail
dscacheutil -q host -a name crm.gramly.tech
route -n get 10.99.132.83
curl -sS -o /dev/null -w '%{http_code} %{remote_ip}\n' https://crm.gramly.tech/
```

Expected: `Connected`, no expired-login warning, DNS `10.99.132.83`, route via
the NetBird `utun` interface, and an Authentik/application response rather than
the public VPN gate.

### Windows PowerShell

```powershell
netbird status --detail
Resolve-DnsName crm.gramly.tech
Get-NetRoute -DestinationPrefix 10.99.132.83/32
curl.exe -sS -o NUL -w "%{http_code} %{remote_ip}\n" https://crm.gramly.tech/
```

### Linux

```bash
netbird status --detail
getent ahostsv4 crm.gramly.tech
ip route get 10.99.132.83
curl -sS -o /dev/null -w '%{http_code} %{remote_ip}\n' https://crm.gramly.tech/
```

Do not use a bare `dig` result as the only acceptance test: it can query a
resolver directly and bypass the resolver path used by applications.

## Hello Admin

Hello Admin is intentionally stricter than network reachability:

```text
NetBird transport
  -> Authentik application policy
     -> oauth2-proxy allowed group
        -> Welcome API owner-group check
```

`gramly-owners`, `authentik Admins`, `Business`, and `Product` are valid. A user
who can reach the private IP but does not belong to any of these groups must be
denied. Authentik application bindings, the provider-scoped verified-email
claim, oauth2-proxy, and the Welcome admin API must keep this exact allow-list
in sync. Diagnose a user without changing permissions:

```bash
infra/kubernetes/apps/identity/audit-welcome-admin-access.sh <username>
kubectl -n gramly-welcome logs deployment/gramly-welcome-admin-auth --since=30m
```

The canonical issuer is
`https://auth.gramly.tech/application/o/welcome-admin/`; the only callback is
`https://hello-admin.gramly.tech/oauth2/callback`. Clear only the
`_gramly_welcome_admin` cookie when testing a stale session.

## Rollback

If the managed primary resolver affects public DNS, disable the `Gramly primary
DNS` nameserver group in NetBird. Do not delete custom zones, routes or users.
Peers then return to their original system resolver behavior while the previous
private configuration remains intact for investigation.
