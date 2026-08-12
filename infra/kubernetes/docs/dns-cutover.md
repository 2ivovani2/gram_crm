# DNS cutover runbook

New VKE public gateway address: `45.77.149.91`.

The production VPS remains at `192.248.148.140`. Existing records must stay on
that address until the corresponding application has passed its migration
rehearsal.

## Phase 1: identity and VPN bootstrap

These records are new and can be created immediately with TTL 300:

| Type | Host | Value |
| --- | --- | --- |
| A | `auth` | `45.77.149.91` |
| A | `vpn` | `45.77.149.91` |

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

Public `A` records may point at `45.77.149.91` solely so cert-manager can renew
HTTP-01 certificates. The public Gateway has no application route for these
hostnames and must return `404`; NetBird split DNS overrides the public record
for enrolled devices and sends application traffic to the private ingress.

## Phase 3: Hello public cutover

Only after the new Hello deployment, database restore rehearsal, TLS, and bot
webhook tests pass:

| Type | Host | Value |
| --- | --- | --- |
| A | `hello` | `45.77.149.91` |

Telegram `setWebhook` is changed after DNS and TLS are healthy. A bot can have
only one active webhook URL, so this is the final cutover step.

## Phase 4: apex website

`@` and `www` remain at `192.248.148.140` until the public Gramly landing is
separately deployed and accepted. Their cutover is independent of CRM and
internal tools.

## Rollback

During the 14-day rollback window, restore the previous public record to
`192.248.148.140` and restore the previous Telegram webhook URL. Do not delete
the old VPS, PostgreSQL volume, or object-storage data during this window.
