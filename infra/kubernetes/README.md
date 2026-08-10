# Gramly Kubernetes platform

Production target: Vultr Kubernetes Engine, Kubernetes 1.36.

This directory contains the reproducible bootstrap configuration for the new
cluster. It deliberately contains no credentials, Telegram tokens, kubeconfig,
database passwords, or DNS API keys.

## Access model

There are two independent traffic planes:

- `public`: a Vultr Load Balancer serving only `hello.gramly.tech`,
  `auth.gramly.tech`, and `vpn.gramly.tech`;
- `private`: a ClusterIP-only gateway for CRM, Forgejo, Plane, Outline, Argo CD,
  and observability. It is reachable through NetBird only.

Authentik is the shared OIDC identity provider. NetBird provides the WireGuard
VPN and uses the same Authentik identities. Application authorization remains
role-based: a successful SSO login does not automatically grant administrator
permissions.

## Pinned platform versions

Versions are declared in `bootstrap/versions.env`. Updates must be performed in
a separate pull request after reading upstream release notes.

## Bootstrap order

1. Export the downloaded VKE kubeconfig path as `KUBECONFIG`.
2. Run `scripts/preflight.sh` and confirm the cluster identity.
3. Install Gateway API CRDs.
4. Install metrics-server and verify `kubectl top nodes`.
5. Install cert-manager with Gateway API integration.
6. Install the public and private Traefik controllers.
7. Install CloudNativePG and the dedicated Authentik database.
8. Record the public LoadBalancer address, but do not change production DNS yet.
9. Deploy Authentik and NetBird and verify VPN-only access.
10. Deploy application staging routes and rehearse all migrations.
11. Lower DNS TTL and perform the production cutover in a maintenance window.

The first Authentik administrator is `i_vovani`. Bootstrap scripts never print
its generated password. Once public TLS is healthy, issue a one-time recovery
link, let the user choose a password, enroll MFA, disable the `akadmin`
break-glass account, and remove bootstrap credentials from the runtime Secret.

After the administrator has confirmed password and TOTP enrollment, enforce MFA
and retire bootstrap access with:

```bash
infra/kubernetes/apps/identity/harden-authentication.sh
```

This deliberately denies login to users who have not enrolled MFA. Provision
new employees with a short-lived recovery or invitation flow so they can enroll
before their first regular login.

Create the confidential Authentik OIDC application used by NetBird with:

```bash
infra/kubernetes/apps/identity/configure-netbird-oidc.sh
```

The script is idempotent and stores generated client credentials only in the
`identity/netbird-authentik-oidc` Kubernetes Secret. Connect that provider to
NetBird through its supported management API with:

```bash
infra/kubernetes/apps/vpn/connect-authentik.sh
```

The external callback is pinned to `https://vpn.gramly.tech/oauth2/callback`.
The connection script also approves and grants the NetBird admin role only to
the configured bootstrap owner's matching SSO identity. Other new employees
remain subject to explicit administrator approval.
Local NetBird authentication deliberately remains available until an
administrator has completed a real SSO login; disable it only after that test.

Run the bootstrap script only after reviewing its rendered Helm output:

```bash
export KUBECONFIG="/absolute/path/to/downloaded-vke-kubeconfig.yaml"
infra/kubernetes/scripts/preflight.sh
infra/kubernetes/scripts/bootstrap-platform.sh
infra/kubernetes/scripts/bootstrap-identity.sh
infra/kubernetes/scripts/bootstrap-vpn.sh
```

The old VPS and its production database remain untouched throughout bootstrap.
They are retained as the rollback target for at least 14 days after cutover.

Once the authoritative `auth` and `vpn` records both point to `45.77.149.91`,
enable the public Gateway and certificates:

```bash
infra/kubernetes/scripts/enable-public-tls.sh
```

The script refuses to contact ACME while authoritative DNS is missing or points
at another server. This avoids serving a certificate on the wrong endpoint and
unnecessary Let's Encrypt failures.

NetBird uses its own PostgreSQL database and role in the highly available
CloudNativePG cluster. Its dashboard has two replicas; the combined control
server uses a retained PVC for embedded-identity bootstrap state and PostgreSQL
for management data and activity events. TCP 80/443 and UDP 3478 share the
public Vultr Load Balancer. UDP is routed with Traefik's stable
`IngressRouteUDP`, while HTTP and HTTPS use Gateway API.

The embedded NetBird identity is bootstrap-only. After the first Authentik
administrator has set a private password and enrolled MFA, add Authentik as the
external NetBird IdP, verify a fresh login, and disable NetBird local auth.

## DNS policy

Public DNS records are changed only after the public gateway, TLS, Authentik,
NetBird, and Hello staging endpoint have passed smoke tests.

Public records:

- `hello.gramly.tech`
- `auth.gramly.tech`
- `vpn.gramly.tech`

Private names (`crm`, `git`, `tasks`, `docs`, `argocd`, `grafana`) are served by
NetBird split DNS and are not pointed at the public load balancer.
