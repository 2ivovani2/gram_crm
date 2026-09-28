# GramlyView: operating contract

GramlyView is the separate `@gramly_view_bot` service. It shares the owner identity and hello-admin with Gramly Hello, but uses a separate webhook, inbox source and one bounded View worker. Hello delivery remains the resource priority. Analysis is free; there is no Trustat API, paywall or copied visual identity.

## Data and user flow

- Users send public `@username`/post links, private invites or a mixed list of up to **20** links. Text and Telegram `text_link` entities are parsed. Invalid channels do not discard valid ones; overflow is explicit.
- One channel gets a compact card. Two or more get one network summary followed immediately by one PNG. Buttons open per-channel details and switch the network image metric. Attribution stays in both text and image when forwarded.
- Public subscribers come from Bot API. Authorized Telethon accounts collect available posts, view counters and public-channel subscriber snapshots. Private reports require an invite and a still-authorized account; other users cannot retrieve them.
- **Views 24/48/72h** means the average *current* view count of posts published in the preceding 24/48/72 hours, from the latest sufficiently fresh scan. It is not a count of views gained in that period or Trustat's private formula. ER24 is the 24h-post average divided by the current subscriber count. The report gives post and channel coverage. Exact view counts at post age 24/48/72h remain separate measurements and cannot be backfilled.
- Monthly subscriber dynamics start with GramlyView's first snapshot. Top posts cover measured posts in the user's saved or just-analyzed channels. Mention alerts are opt-in and search **only already indexed public channels**, with no saved message text. Advertising price is a user-entered CPM × measured average views / 1,000; it is an arithmetic estimate, not a market quote.

## Availability and resources

At least **three** enabled accounts with recent successful checks and no cooldown are required for new analysis. Health checks run every two minutes. Sessions are leased and connected per operation, then disconnected and encrypted again; a transient failure is retried after cooldown, while a revoked session must be re-uploaded by an administrator. The collector runs every 30 minutes, selecting up to 20 least recently collected channels. A failed scan records an error code and never turns missing measurements into zero.

View has one worker pod, concurrency four, a two-connection DB pool and a one-CPU limit. Its inbox is excluded from Hello KEDA and Hello queue health metrics. View queue age, collection failures and worker absence have dedicated alerts. Do not raise View parallelism or Hello KEDA maxima based only on queue depth: confirm node memory, DB/PgBouncer connection headroom and Hello delivery SLO under simultaneous load first.

## Release check

Apply additive migrations before the new code. Test a new public channel, a permitted private invite and a 20-link mixed request. Confirm the network text and image appear together; check post counts, scan time, partial coverage, report callbacks, CSV, top, CPM and mention opt-in. Run load and failure tests with a fake Telegram server before increasing View throughput. A production canary must show that Hello delivery latency and expired greeting counts do not regress.
