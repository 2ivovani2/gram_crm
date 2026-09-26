# GramlyView: verified scope and operating contract

GramlyView is a **separate Telegram bot** (`@gramly_view_bot`) sharing the
`owner` identity table and hello-admin with Gramly Hello. The stable identity
key is Telegram user ID; username is refreshed from each update. The View
webhook has its own secret and inbox source. Hello delivery and onboarding are
unchanged. View is free; its ten-channel request cap is a load limit, not a
subscription tier.

The user journey is link-first: a person sends a public URL, a private invite,
a post URL, or a list. They never log into MTProto or connect a personal account
to use GramlyView. Five or more separately authorized technical user accounts
are required before *new* analytics begins. Cached reports remain visible when
the pool is below threshold. The only product-level links to Hello are reciprocal
bot buttons and the shared admin/user identity.

## Trustat audit (personal walkthrough, Premium account)

The following rows are observations in the authenticated Telegram Web session
on 26 September 2026. Premium was active through 26 October. Trustat's
subscription screen says paid access is needed for all features and offers
Premium and Medium, so an independent free-tier comparison was **not** possible.
The flame icon on a menu item is not proof of a specific Premium entitlement.
No Trustat API or implementation code is used by GramlyView.

| Trustat path / observed response | GramlyView | Data source / implementation | Confirmation |
| --- | --- | --- | --- |
| `/start` → channel link or `@username` → short card with subscribers, 24/48/72 and ER24 | Public-channel card | Bot API member count; MTProto observations only after opt-in | Observed; card implemented with honest missing values |
| `/start` → multiple lines of links → deduplicated cards and aggregate/network graph controls | Up to ten deduplicated per-channel cards, partial errors | Bot API per channel, saved observations | Observed; cards implemented, aggregate graph pending |
| `Массовая аналитика` → `Сохранённые каналы` → `Изменить/добавить коллекции` → `Создать новую коллекцию` → step 1 asks name | Favorites and named collections up to ten channels | First-party DB | Wizard step 1 observed; later Trustat steps unverified |
| `Статистика картинкой` → channels → website link and month of daily subscriber changes | Historical series and image later | First-party periodic snapshots, never reconstructed | Text series observed; image/export details unverified |
| `Мониторинг упоминаний` → alerts bot → new tracking: name then keyword | Planned opt-in notifications | First-party MTProto search restricted to accessible channels | Two-step wizard observed; saved alert and delivery not tested |
| `Топ публикаций` → sort by views/forwards/comments/reactions → daily five-post list, pagination | Five measured posts ranked by views; other sorts pending | MTProto collector | List/sorts observed; cross-channel corpus unavailable initially |
| `Подписка` → Premium/Medium and expiry | No View paywall | Shared Gramly identity; View free | Personally observed Premium status |
| `Trustat API`, chat bot, website, `/help` | No copied API/chat product; View methodology help | Own endpoints only | Menu and support links observed; API internals unverified |
| Publication link sent directly | Identifies channel and returns channel card | Public link parser; post metadata requires MTProto | Personally observed, no post-level card in that response |
| Forwarded post, private invite, unsupported link, notifications, export options | Invite-backed private channel and sampled image/CSV reports; notifications still pending | Depends on Telegram permissions | Trustat behavior still unverified; do not call audit complete |

## Metrics and their limits

| Metric | Formula and window | Source / freshness | Missing-data rule |
| --- | --- | --- | --- |
| Subscribers | Public: `getChatMemberCount`; private: MTProto `channels.getFullChannel` | Bot API / assigned user session | API failure returns error, not zero |
| Views 24/48/72h | Sum of latest sampled view counters for posts **published** in the trailing 24/48/72 hours | Approved user MTProto session, sampled hourly | Requires complete 72h scan within two hours; otherwise `нет данных` |
| ER24 | Mean sampled views per post published in trailing 24h ÷ current subscribers × 100 | Same samples and Bot API subscribers | No posts, zero subscribers, stale/incomplete sample → `нет данных` |

These are GramlyView definitions, **not** a claim that Trustat uses the same
formula. The 24/48/72 values are not unique viewers or view increments within
the window. Sampling begins when the approved collector runs; past 24-hour
post-age view snapshots cannot be reconstructed. An old post link can identify
its channel, but its historical reach cannot be invented. Telegram's Bot API
does not expose post view counters or arbitrary channel history. MTProto's
[`messages.getMessagesViews`](https://core.telegram.org/method/messages.getMessagesViews)
is user-only, and channel statistics require appropriate administrator access.

## Access and data flow

1. `/welcome/view/webhook/` authenticates the distinct secret, validates body
   size and commits a deduplicated `InboxEvent(source_key='view')` before 200.
2. A dedicated View worker claims only `view` inbox events. Hello event workers
   exclude them, and owner workers claim only `interface`; a long MTProto scan
   cannot occupy greeting delivery capacity or enter Hello's owner FSM. One
   View pod processes four events concurrently while preserving each person's
   order within a batch; its bounded lease is renewed during long scans.
3. `owner_from_telegram(..., service='view')` upserts the same `Owner` by
   Telegram ID and writes per-service first/last activity. Migration backfills
   existing owners as Hello without sending any message or triggering delivery.
4. The single `required_news_channel` admin flag is reused. View denies access
   if disabled/unconfigured, verifies actual membership for every analytical
   action, and denies on Telegram lookup error even when a cached positive
   state exists. Telegram's membership lookup for other users requires the
   View bot to be an administrator of that channel.
5. Public names, `t.me` URLs, post URLs and private invitations are deduplicated.
   A private invitation is checked with MTProto; one technical account joins the
   broadcast channel if no approval or payment is required. That account remains
   assigned to the channel, and only users who supplied an invite can see private
   reports. A forwarded private post is accepted after that assignment exists.
6. Cards are sent one by one. A central renderer appends a visible HTML link
   to `@gramly_view_bot` in the text of every View response; forwarding retains
   it even if Telegram removes inline buttons. Values and titles are escaped.

`gramly-view-mtproto` contains only the application API ID/hash. Administrators
upload Telethon `.session` files through the restricted hello-admin section.
The files are verified against Telegram, encrypted with the application keyring,
and persisted in the protected database; raw sessions are never downloadable.
Workers materialize one private temporary copy per leased account. The health
CronJob probes every two minutes. At fewer than five healthy authorized accounts,
all new analyses stop and a verified View-bot recipient receives a threshold
alert. The collector CronJob remains suspended until five accounts are loaded
and the end-to-end smoke check passes. Incomplete scans are marked unusable.
Credentials, invite hashes, message texts and tokens must not appear in logs.

## Rollout

1. Apply migration `0035_gramly_view_identity` before deploying code. Rollback
   of application code is safe while the additive tables remain; dropping them
   deletes View observations and service history and is not a normal rollback.
2. Mount `WELCOME_VIEW_BOT_TOKEN`, `WELCOME_VIEW_BOT_USERNAME`, and a random
   `WELCOME_VIEW_WEBHOOK_SECRET` in `gramly-welcome-runtime`. The username must
   match Telegram `getMe`. Never pass token in a URL or commit it.
3. Add `@gramly_view_bot` as admin of the hello-admin configured news channel
   with the least rights sufficient for membership checks. Run
   `welcome-reconcile-view-webhook`; confirm `getWebhookInfo` points to
   `https://gramly.tech/welcome/view/webhook/` and delivery is healthy.
4. Test a nonmember, member, departed member, Bot API error, public list,
   invalid link, forwarded private channel, and shared identity in admin.
5. Configure `WELCOME_VIEW_MTPROTO_API_ID` and `WELCOME_VIEW_MTPROTO_API_HASH`
   in the separate `gramly-view-mtproto` Secret. Generate each technical
   account session locally with `welcome-view-authorize /secure/path/account.session`
   or an equivalent approved Telethon login. Enter codes/passwords only in the
   terminal, then upload **five distinct** `.session` files in hello-admin.
   In hello-admin set the alert recipient to the Telegram username of an
   account that has opened `@gramly_view_bot` with `/start`. The bot sends a
   test message before the recipient is saved. Verify the confirmation and
   healthy pool count.
6. Run a public and a private invite canary, including an invite that needs
   approval, export an image and CSV, then unsuspend the hourly collector.
   Keep the 5-account gate active throughout rollout. No real credentials or
   invite hashes enter Git, logs or command-line arguments.

Costs scale with sampled channels: up to 20 × 500 post observations per
hour could be 240,000 rows/day at the hard cap. Completed samples older than
seven days are pruned in bounded batches; at full cap this is still around
1.7 million live rows. Real volume must be measured before expanding the
canary, then use partitioning or aggregation. The bot itself uses
Telegram Bot API traffic and database rows but has no Trustat subscription
dependency.

## Design contract

View uses Gramly's ink, graphite, lime and cyan tokens (see `DESIGN.md`), with
short mobile cards rather than copied Trustat layouts. Example:

> **Канал**
> Подписчики · 12 345
> Просмотры 24 / 48 / 72 ч · нет данных
> ER24 · нет данных
> Обновлено · 26.09.2026 01:00 UTC
> Аналитика подготовлена в GramlyView · Открыть бот

The footer is a real clickable text link, not only an inline keyboard. The
View profile uses the separate `gramlyview-avatar.png`: the familiar Cubigator
head with a simple cyan analytics lens. `welcome-reconcile-view-webhook`
updates it through Telegram `setMyProfilePhoto`; inspect Telegram's circular
crop at 40–80 px after activation.
