# Gramly Kubernetes platform

Production target: Vultr Kubernetes Engine, Kubernetes 1.36.

This directory contains the reproducible bootstrap configuration for the new
cluster. It deliberately contains no credentials, Telegram tokens, kubeconfig,
database passwords, or DNS API keys.

## Access model

There are two independent traffic planes:

- `public`: a Vultr Load Balancer serving identity/VPN, the Gramly landing and
  Telegram webhooks, and the signed-media S3 endpoint;
- `private`: separate ClusterIP-only business and collaboration gateways for
  CRM, Forgejo, Vikunja, and Outline. They are reachable through NetBird only.

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

After the administrator confirms a successful SSO login, retire local NetBird
authentication and its reusable bootstrap credential with:

```bash
infra/kubernetes/apps/vpn/harden-authentication.sh
```

Private application access uses the official NetBird Kubernetes Operator and
one `NetworkResource` per service. This keeps employee-facing and DevOps-only
resources in separate NetBird destination groups instead of granting both
through one shared ingress IP. Install the operator after storing its service
user PAT in the `vpn/netbird-mgmt-api-key` Secret:

```bash
infra/kubernetes/scripts/deploy-netbird-operator.sh
```

The operator is configured for the self-hosted management endpoint and is not
allowed to read arbitrary workload Secrets.

If an operator PAT is disclosed, rotate it without printing the replacement:

```bash
infra/kubernetes/apps/vpn/rotate-operator-token.sh
```

The replacement defaults to a 90-day lifetime, restarts the operator, verifies
the rollout, and revokes the previous service-user PATs.

Create the empty identity and destination groups before attaching users,
devices, resources, or policies:

```bash
infra/kubernetes/apps/vpn/ensure-access-groups.sh
```

The intended source roles are `gramly-employees`, `gramly-product`,
`gramly-engineering`, `gramly-devops`, and `gramly-owners`. Destination services
are split between `gramly-business-services`,
`gramly-collaboration-services`, and `gramly-devops-services`. Product and
repository permissions remain application-level roles; membership in a VPN
group only makes the corresponding endpoint reachable.

Reconcile the corresponding least-privilege HTTPS policies with:

```bash
infra/kubernetes/apps/vpn/ensure-access-policies.sh
```

Employees and application roles can reach business and collaboration services.
Only the `gramly-devops` and `gramly-owners` groups can reach infrastructure
administration services. Bootstrap devices in `gramly-admin-devices` can reach
all three access planes while SSO role synchronization is being introduced.
The script does not assign users or devices to roles.
It keeps the permissive bootstrap `Default` policy unless an approved
maintenance run explicitly sets `REMOVE_DEFAULT_POLICY=true`; even then, it
removes the policy only when it matches NetBird's exact All-to-All shape.

Create or reconcile the split-DNS zone used by private application resources:

```bash
infra/kubernetes/apps/vpn/ensure-private-dns-zone.sh
```

The zone reuses `gramly.tech`, but NetBird answers only for records explicitly
present in the zone. Public names such as `auth`, `vpn`, and `hello` continue to
fall through to authoritative public DNS.

Deploy the operator-managed, three-node private router after the zone exists:

```bash
infra/kubernetes/scripts/deploy-private-network.sh
```

Deploy separate business and collaboration gateways so a user who can reach
CRM cannot reuse the same ingress address to reach an infrastructure hostname:

```bash
infra/kubernetes/scripts/deploy-private-access-gateways.sh
```

Each access plane has its own Traefik controller, ClusterIP, NetBird
`NetworkResource`, and destination group. The existing private controller is
retained as the bootstrap/DevOps plane until its application routes are moved.

Deploy Forgejo 16.0.2 and its retained data volume with:

```bash
infra/kubernetes/scripts/deploy-forgejo.sh
```

The initial migration copied the old `/data` while its source container was
stopped and retained the old container as a rollback target.

After approving a brief Forgejo outage, rehearse a consistent data copy with:

```bash
CONFIRM_FORGEJO_DOWNTIME=true \
  infra/kubernetes/scripts/rehearse-forgejo-data-copy.sh
```

The script refuses a non-empty target, always restarts the source through an
exit trap, runs Forgejo's default doctor checks plus strict Git object checks
against the copied data, and leaves the target deployment stopped.

After application and HTTPS smoke tests pass, reconcile friendly NetBird DNS
records with:

```bash
infra/kubernetes/apps/vpn/ensure-private-app-records.sh
```

Vikunja provides the internal task and Kanban service because its open edition
supports Authentik OIDC and disabling local authentication. Deploy it with:

```bash
infra/kubernetes/scripts/deploy-vikunja.sh
```

Run the bootstrap script only after reviewing its rendered Helm output:

```bash
export KUBECONFIG="/absolute/path/to/downloaded-vke-kubeconfig.yaml"
infra/kubernetes/scripts/preflight.sh
infra/kubernetes/scripts/bootstrap-platform.sh
infra/kubernetes/scripts/bootstrap-identity.sh
infra/kubernetes/scripts/bootstrap-vpn.sh
```

The old VPS remains untouched throughout bootstrap. After cutover its CRM web,
worker, beat, and nginx containers stay stopped, while PostgreSQL and MinIO are
retained as a read-only rollback target for at least 14 days. The final source
dump is retained on the VPS as `/root/gramly-crm-final-20260812.dump`.

## Production cutover state

The CRM production cutover completed on 2026-08-12. The final PostgreSQL dump
was restored before migrations were applied, all 19 MinIO objects were mirrored
and verified, and only then were the Kubernetes web, worker, and beat workloads
started. The standalone `question_bot` on the old VPS is outside this migration
and remains running.

During authoritative DNS propagation, `gramly-cutover-bridge` on the old VPS
forwards TCP 80/443 to the VKE public Load Balancer. Keep it until recursive DNS
caches no longer return the old IP; removing it does not authorize deleting the
old database, MinIO volume, or final dump.

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

Publicly served records:

- `gramly.tech`
- `www.gramly.tech`
- `hello.gramly.tech`
- `media.gramly.tech`
- `auth.gramly.tech`
- `vpn.gramly.tech`

Private application names (`crm`, `git`, `tasks`, `docs`) also have public A
records pointing at the Load Balancer for DNS ownership and certificate flows,
but the public Gateway has no application routes for them. NetBird split DNS
overrides those records with the private business/collaboration Gateway IPs.
