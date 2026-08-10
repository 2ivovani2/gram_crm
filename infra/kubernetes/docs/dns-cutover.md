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

They resolve to the pinned private ingress address `10.99.132.82`, distributed
through the NetBird `gramly-cluster` Network as a `/32` resource. Do not point
these names to the public VKE Load Balancer.

Public `A` records for private services are removed only after VPN access has
been tested from at least two administrator devices. They are never pointed at
the public VKE load balancer.

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
