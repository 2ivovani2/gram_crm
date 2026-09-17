# Template and chain editor release

The Mini App now installs an unassigned independent copy, opens the full draft
editor and publishes with channel assignments only after the owner's explicit
launch. Admin templates use the same message composer in a three-step wizard.
Existing bot editing and API endpoints remain available.

## Deployment order

1. Merge the reviewed PR with passing `quality` checks. Build the API and web
   images from that commit and record immutable digests and the current images.
2. Take and verify the usual PostgreSQL backup. Run `alembic upgrade head` from
   the new API image **before** any new API or worker pod starts. Migration 0026
   adds `content_flow_version.edit_revision`, `editor_media`, and revision
   triggers; it does not rewrite messages or publish anything.
3. Roll out the API and Welcome workers/maintenance with the same API digest.
   Check readiness and errors, then roll out the web digest. Old web clients
   continue to work against the new API.
4. On a designated test bot/channel, create an admin template with a photo,
   formatted `{name}`, URL button and custom emoji from an addemoji link. Save
   the hidden version, publish it, install two owner copies, edit and launch one.
   Verify the template and the other copy remain unchanged. Test saving with no
   selected channels and a stale-editor conflict.
5. Verify actual Telegram delivery separately: check the caption, personalized
   UTF-16 entity offsets, keyboard, timing and custom emoji ID in the delivery.
   Browser previews alone do not prove that a particular bot may send custom
   emojis. A test bot/channel and recipient must be designated before this check.

The new application cannot run on schema 0025. To roll back an application
release, restore the previous web and API/worker image digests and **keep 0026**:
its additions are backwards compatible. Do not downgrade 0026 in production
while registered editor uploads are referenced. Local downgrade/upgrade is
covered in the release validation; dropping `editor_media` would discard the
upload ownership registry.

## Contracts and compatibility

- `GET /api/v1/editor/flows/{id}` opens/reads the draft under locks and returns
  keyboards, authenticated media previews, channels and `edit_revision`.
- `PUT /api/v1/editor/drafts/{id}` requires `expected_revision` and an
  `Idempotency-Key`. Full content, ordering and delays commit together. Media
  must be an existing attachment of this draft or a registered upload owned by
  the caller; browser-supplied object keys cannot grant access.
- `POST /api/v1/editor/drafts/{id}/launch` requires the same concurrency and
  retry controls and at least one owned, active channel. Content validation,
  activation, publication and assignments commit together with the retry receipt.
- Revision triggers cover messages, files, keyboards, versions, flow metadata
  and channel assignments, including writes through the owner bot. A `409`
  keeps the local editor content and requires an explicit reload.
- Complex legacy sequences, per-file captions and special keyboards are kept
  intact and labeled in the editor. Unsupported rich text entities are read-only.
  Editing copies never deletes shared catalog blobs or published message rows.
- Owner media and both emoji APIs require authentication. The emoji service only
  parses Telegram addemoji links and uses Bot API methods. Binary previews and
  bounded TGS data pass through the server without exposing bot tokens. External
  animation assets are rejected; the frontend uses the expression-free Lottie
  light player compatible with the existing CSP.
- Limits remain five messages, five files in total, 20 MiB per file and 180 days
  per delay. Large stickers and bot-message import are not part of this editor.

## Validation

`python -m pytest services/welcome/tests ops/welcome/tests -q` runs the backend
suite. PostgreSQL cases require a freshly migrated disposable database via
`WELCOME_DATABASE_URL` and `WELCOME_TEST_DATABASE_URL`; an older finance fixture
uses fixed IDs and expects a clean database on repeat runs.

Run `npx tsc --noEmit -p frontend/welcome/tsconfig.json` and `npm run build:welcome`.
For browser tests start `npm run dev:welcome -- --host 127.0.0.1 --port 5187`, then:

```sh
WELCOME_CATALOG_TEST_URL=http://127.0.0.1:5187 npx playwright test \
  frontend/tests/welcome-catalog.spec.js \
  frontend/tests/welcome-editor.spec.js \
  frontend/tests/welcome-admin-catalog.spec.js
```

The suite covers Chromium, Firefox and WebKit; 390/430/1440 px; Mini App light
and dark themes; explicit save, retained local conflict edits, idempotent retry,
media ordering, emoji insertion, keyboard navigation, no-channel state and
admin publication retry. CI runs the Chromium cases on every PR. Real Telegram
credentials are never used in these automated tests.
