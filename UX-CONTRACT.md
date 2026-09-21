# Gramly UX contract

## Navigation

- Mini App primary navigation is **Главная, Приветствие, База, Рассылки, Ещё**.
- Главная shows one next step: connect a bot, connect a channel, create a greeting or review the running result.
- Secondary destinations live under Ещё. Deep links may open the relevant secondary destination directly.
- Owner bot is a compact command centre: open Gramly, bots, video learning, subscription, support and the public site.

## State and feedback

- Every asynchronous region has loading, empty, error and retry states. Existing content remains usable when optional analytics fails.
- Draft editors always expose saved, saving, unsaved and conflict states. A `409` preserves local edits and offers a reload of the server revision.
- Destructive or ownership-changing actions show their concrete impact before confirmation and create a security audit event.
- User messages sent to owner-bot input states are deleted after success and after recoverable errors when the original message is not needed for retry.

## Compatibility

- Existing Telegram entities, media, keyboards and unknown payload fields survive read-edit-save cycles.
- Legacy help, tips and Telegraph onboarding are hidden and no longer scheduled. Their stored rows remain untouched during the compatibility release.
- Video learning and support remain available in owner bot, Mini App, admin and the public landing.
