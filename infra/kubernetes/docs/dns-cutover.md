# DNS cutover runbook

New VKE public gateway address: `45.77.149.91`.

Current public L4 edge address: `45.146.131.207`. Public DNS records use this
address so traffic from Russian networks reaches the VKE gateway through the
Moscow compatibility edge. The edge does not terminate TLS or contain
application data. NetBird split-DNS routes public/bootstrap names through the
Moscow edge and private service names directly to their private ingress
addresses.

The former production VPS at `192.248.148.140` has been retired. It is not a
valid rollback target. All public records listed below use the Moscow edge.

## Phase 1: identity and VPN bootstrap

These records are new and can be created immediately with TTL 300:

| Type | Host | Value |
| --- | --- | --- |
| A | `auth` | `45.146.131.207` |
| A | `vpn` | `45.146.131.207` |

Do not change `@`, `www`, `crm`, or `git` in this phase. Do not create the
`hello` record until its landing page and Telegram webhook routes are running in
VKE.

## Phase 2: private development contour

After NetBird is working, the following names are managed through NetBird split
DNS and resolve only for enrolled devices:

- `crm.gramly.tech`
- `git.gramly.tech`
- `tasks.gramly.tech`
- `docs.gramly.tech`
- `argocd.gramly.tech`
- `grafana.gramly.tech`
- `cluster.gramly.tech`

Enrolled devices resolve these names through NetBird split DNS. Business apps
use `10.99.132.83`; collaboration apps use `10.99.132.84`; infrastructure apps
use `10.99.132.82`. Each address is distributed through a dedicated NetBird
Network resource.

Public `A` records point at `45.146.131.207` so cert-manager can renew HTTP-01
certificates and non-enrolled users receive the VPN access gate. NetBird split
DNS overrides the public record for enrolled devices and sends application
traffic to the private ingress.

## Phase 3: Hello public cutover

Only after the new Hello deployment, database restore rehearsal, TLS, and bot
webhook tests pass:

| Type | Host | Value |
| --- | --- | --- |
| A | `hello` | `45.146.131.207` |

Telegram `setWebhook` is changed after DNS and TLS are healthy. A bot can have
only one active webhook URL, so this is the final cutover step.

## Phase 4: apex website

`@` and `www` point at `45.146.131.207`. The Moscow edge forwards their traffic
to the public Gramly landing in VKE.

## Rollback

If the Moscow edge fails, temporarily point public records directly at the VKE
LoadBalancer `45.77.149.91`. Application workloads and data do not move during
this rollback; only the network path changes.
