# Welcome high-throughput runtime

## Service objectives

The capacity gate is 43 new five-step flows per second for 60 minutes and a
ten-minute burst at 86 flows per second. Webhook p95/p99 must stay below
100/250 ms. Deadline-bound delivery p95/p99 must stay below 5/15 seconds, with
no committed inbox loss and no new expired join-request windows.

## Delivery isolation

The delivery executable supports `WELCOME_DELIVERY_LANES`. Production runs
three isolated pools:

- `urgent,standard`: greeting chains, join requests and activations; 16 actions
  per pod, 4–40 pods;
- `interactive`: subscriber and owner-triggered messages; 8 actions per pod,
  2–20 pods;
- `bulk`: broadcasts and channel rotation; 8 actions per pod, 0–20 pods.

Each external action obtains and releases its own short database session. The
Telegram HTTP client pool is process-scoped, bounded and keyed by an opaque
token fingerprint. A hierarchical Valkey GCRA bucket enforces bot and chat
limits; bulk has a separate 20/s ceiling under the default 28/s bot ceiling so
urgent traffic always has headroom.

`delivery_job` is the durable migration boundary for the Streams dispatcher.
Database triggers dual-write all seven legacy queue types and keep their state
in sync. PostgreSQL remains authoritative. Valkey may only contain a job ID;
consumers must acquire the source lease in PostgreSQL before an external call.
The legacy claim path remains the rollback path until the Streams canary has
completed and drained.

## Dependency behaviour

- Telegram transient network/5xx failures feed a 30-second circuit. At least
  20 samples and 50% failures open it for 15 seconds; three probes close it.
- Telegram 429 establishes a distributed bot cooldown. 400/401/403 do not
  open the global circuit.
- Three consecutive Valkey errors open the local limiter circuit for five
  seconds. Work remains leased in PostgreSQL and is retried with jitter.
- PostgreSQL commits synchronously to one standby. A webhook is never
  acknowledged before commit.
- `delivery_job` terminal rows leave the hot table after seven days. Monthly
  history partitions are retained for approximately 180 days.

## Release gate

1. Apply the PgBouncer Pooler and synchronous-write setting while keeping the
   current two PostgreSQL instances and resource requests. Confirm both database
   instances plus three PgBouncer instances are healthy. Add a third PostgreSQL
   instance and larger node pool only after a measured capacity gate justifies
   the extra capacity and the cluster can schedule it.
2. Run migration `0032`; compare active rows in each legacy queue with
   `delivery_job` grouped by `source_kind`.
3. Deploy lane workers paused, verify rendered manifests and Prometheus targets,
   then enable urgent, interactive and bulk pools in that order.
4. Run `ops/welcome/load_test.py` for webhook ingestion. A five-step
   Telegram-compatible delivery load scenario at the rates above is still
   required before claiming the 155,000-flows-per-hour target.
5. Exercise Telegram 429/5xx, a 60-second Valkey outage, an S3 outage and a
   PostgreSQL switchover. Urgent work must recover without a retry storm.
6. Promote 5%, 10%, 50% and 100%. Stop promotion on SLO burn, an expired join
   window, dual-write drift or a circuit that remains open.

The initial release uses the legacy PostgreSQL consumer with isolated worker
pools. The Streams relay stays disabled; enabling a Streams consumer, the
third database replica and the target throughput are separate gated steps.

Rollback disables the new lane deployments and restores the original combined
worker configuration. Source rows and inbox events remain intact, so rollback
does not require reversing migration `0032`.
