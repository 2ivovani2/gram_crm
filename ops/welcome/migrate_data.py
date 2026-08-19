#!/usr/bin/env python3
from __future__ import annotations

import argparse
import asyncio
import os
from collections.abc import Iterable, Sequence

import asyncpg
from gramly_welcome.crypto import TokenKeyring, decrypt_legacy_django_token

TARGET_TABLES = (
    "event_log",
    "join_request",
    "greeting_delivery",
    "inbox_event",
    "contact",
    "welcome_draft_media",
    "welcome_draft",
    "welcome_media",
    "welcome_message_version",
    "channel",
    "managed_bot",
    "owner",
)


def asyncpg_dsn(value: str) -> str:
    """Convert SQLAlchemy's async driver URL into an asyncpg-compatible DSN."""
    for scheme in ("postgresql+asyncpg://", "postgres+asyncpg://"):
        if value.startswith(scheme):
            return "postgresql://" + value.removeprefix(scheme)
    return value


async def copy_query(
    source: asyncpg.Connection,
    target: asyncpg.Connection,
    *,
    source_query: str,
    target_table: str,
    target_columns: Sequence[str],
) -> int:
    rows = await source.fetch(source_query)
    if not rows:
        return 0
    placeholders = ", ".join(f"${index}" for index in range(1, len(target_columns) + 1))
    columns = ", ".join(target_columns)
    await target.executemany(
        f"INSERT INTO {target_table} ({columns}) VALUES ({placeholders})",  # nosec B608
        [tuple(row) for row in rows],
    )
    return len(rows)


async def reset_sequences(target: asyncpg.Connection, tables: Iterable[str]) -> None:
    for table in tables:
        await target.execute(
            "SELECT setval(pg_get_serial_sequence($1, 'id'), "
            f"COALESCE((SELECT max(id) FROM {table}), 1), "  # nosec B608
            f"EXISTS (SELECT 1 FROM {table}))",  # nosec B608
            table,
        )


async def migrate(args: argparse.Namespace) -> None:
    source = await asyncpg.connect(asyncpg_dsn(args.source_database_url))
    target = await asyncpg.connect(asyncpg_dsn(args.target_database_url))
    keyring = TokenKeyring.parse(args.token_encryption_keys)
    try:
        await source.execute("SET SESSION CHARACTERISTICS AS TRANSACTION READ ONLY")
        target_name = str(await target.fetchval("SELECT current_database()"))
        if target_name != args.confirm_target_database:
            raise RuntimeError(
                f"Refusing target database {target_name!r}; expected "
                f"{args.confirm_target_database!r}"
            )
        async with target.transaction():
            await target.execute("SELECT pg_advisory_xact_lock(hashtext('gramly-welcome-migration'))")
            await target.execute(
                "TRUNCATE " + ", ".join(TARGET_TABLES) + " RESTART IDENTITY CASCADE"
            )
            counts: dict[str, int] = {}
            counts["owner"] = await copy_query(
                source,
                target,
                source_query=(
                    "SELECT id, telegram_id, username, first_name, last_name, "
                    "guide_completed, guide_step, created_at, last_seen_at "
                    "FROM welcome_bots_owner ORDER BY id"
                ),
                target_table="owner",
                target_columns=(
                    "id",
                    "telegram_id",
                    "username",
                    "first_name",
                    "last_name",
                    "guide_completed",
                    "guide_step",
                    "created_at",
                    "last_seen_at",
                ),
            )
            bots = await source.fetch(
                "SELECT id, owner_id, public_id, telegram_id, username, display_name, "
                "token_ciphertext, webhook_secret, path_secret, is_active, webhook_configured, "
                "welcome_delay_seconds, approval_delay_seconds, auto_approve, created_at, updated_at "
                "FROM welcome_bots_managedbot ORDER BY id"
            )
            bot_values = []
            for row in bots:
                values = tuple(row)
                token = decrypt_legacy_django_token(values[6], args.legacy_django_secret_key)
                bot_values.append(
                    (*values[:6], keyring.encrypt(token), keyring.current_version, *values[7:])
                )
            await target.executemany(
                "INSERT INTO managed_bot "
                "(id, owner_id, public_id, telegram_id, username, display_name, token_ciphertext, "
                "key_version, webhook_secret, path_secret, is_active, webhook_configured, "
                "welcome_delay_seconds, approval_delay_seconds, auto_approve, created_at, updated_at) "
                "VALUES ($1,$2,$3,$4,$5,$6,$7,$8,$9,$10,$11,$12,$13,$14,$15,$16,$17)",
                bot_values,
            )
            counts["managed_bot"] = len(bot_values)
            counts["welcome_draft"] = await copy_query(
                source,
                target,
                source_query=(
                    "SELECT id, bot_id, owner_id, media_group_id, payload, "
                    "updated_at + INTERVAL '2 seconds', NULL, created_at, updated_at "
                    "FROM welcome_bots_welcomedraft ORDER BY id"
                ),
                target_table="welcome_draft",
                target_columns=(
                    "id",
                    "bot_id",
                    "owner_id",
                    "media_group_id",
                    "payload",
                    "finalize_at",
                    "finalized_version_id",
                    "created_at",
                    "updated_at",
                ),
            )
            counts["welcome_draft_media"] = await copy_query(
                source,
                target,
                source_query=(
                    "SELECT id, draft_id, telegram_message_id, media_type, storage_key, "
                    "original_name, mime_type, size, created_at "
                    "FROM welcome_bots_welcomedraftmedia ORDER BY id"
                ),
                target_table="welcome_draft_media",
                target_columns=(
                    "id",
                    "draft_id",
                    "telegram_message_id",
                    "media_type",
                    "storage_key",
                    "original_name",
                    "mime_type",
                    "size",
                    "created_at",
                ),
            )
            counts["channel"] = await copy_query(
                source,
                target,
                source_query=(
                    "SELECT id, bot_id, telegram_id, title, username, is_active, can_invite_users, "
                    "connected_at, disconnected_at FROM welcome_bots_channel ORDER BY id"
                ),
                target_table="channel",
                target_columns=(
                    "id",
                    "bot_id",
                    "telegram_id",
                    "title",
                    "username",
                    "is_active",
                    "can_invite_users",
                    "connected_at",
                    "disconnected_at",
                ),
            )
            counts["welcome_message_version"] = await copy_query(
                source,
                target,
                source_query=(
                    "SELECT v.id, m.bot_id, v.version, v.author_telegram_id, v.payload, "
                    "(m.active_version_id = v.id), v.created_at "
                    "FROM welcome_bots_welcomemessageversion v "
                    "JOIN welcome_bots_welcomemessage m ON m.id = v.message_id ORDER BY v.id"
                ),
                target_table="welcome_message_version",
                target_columns=(
                    "id",
                    "bot_id",
                    "version",
                    "author_telegram_id",
                    "payload",
                    "is_active",
                    "created_at",
                ),
            )
            counts["welcome_media"] = await copy_query(
                source,
                target,
                source_query=(
                    "SELECT id, version_id, position, media_type, storage_key, original_name, "
                    "mime_type, size FROM welcome_bots_welcomemedia ORDER BY id"
                ),
                target_table="welcome_media",
                target_columns=(
                    "id",
                    "version_id",
                    "position",
                    "media_type",
                    "storage_key",
                    "original_name",
                    "mime_type",
                    "size",
                ),
            )
            counts["contact"] = await copy_query(
                source,
                target,
                source_query=(
                    "SELECT id, bot_id, telegram_id, username, first_name, last_name, language_code, "
                    "gender, delivery_status, bot_started, first_seen_at, last_seen_at, "
                    "last_delivery_at, last_error FROM welcome_bots_contact ORDER BY id"
                ),
                target_table="contact",
                target_columns=(
                    "id",
                    "bot_id",
                    "telegram_id",
                    "username",
                    "first_name",
                    "last_name",
                    "language_code",
                    "gender",
                    "delivery_status",
                    "bot_started",
                    "first_seen_at",
                    "last_seen_at",
                    "last_delivery_at",
                    "last_error",
                ),
            )
            counts["join_request"] = await copy_query(
                source,
                target,
                source_query=(
                    "SELECT id, bot_id, channel_id, contact_id, telegram_update_id, status, "
                    "delay_snapshot_seconds, due_at, 0, NULL, NULL, error, created_at, processed_at "
                    "FROM welcome_bots_joinrequest ORDER BY id"
                ),
                target_table="join_request",
                target_columns=(
                    "id",
                    "bot_id",
                    "channel_id",
                    "contact_id",
                    "telegram_update_id",
                    "status",
                    "delay_snapshot_seconds",
                    "due_at",
                    "attempts",
                    "lease_owner",
                    "lease_expires_at",
                    "error",
                    "created_at",
                    "processed_at",
                ),
            )
            counts["greeting_delivery"] = await copy_query(
                source,
                target,
                source_query=(
                    "SELECT id, bot_id, channel_id, contact_id, version_id, event_key, status, "
                    "delay_snapshot_seconds, 0, due_at, NULL, NULL, sent_at, error, created_at "
                    "FROM welcome_bots_greetingdelivery ORDER BY id"
                ),
                target_table="greeting_delivery",
                target_columns=(
                    "id",
                    "bot_id",
                    "channel_id",
                    "contact_id",
                    "version_id",
                    "event_key",
                    "status",
                    "delay_snapshot_seconds",
                    "attempts",
                    "due_at",
                    "lease_owner",
                    "lease_expires_at",
                    "sent_at",
                    "error",
                    "created_at",
                ),
            )
            counts["event_log"] = await copy_query(
                source,
                target,
                source_query=(
                    "SELECT id, bot_id, owner_id, event_type, level, message, context, created_at "
                    "FROM welcome_bots_eventlog ORDER BY id"
                ),
                target_table="event_log",
                target_columns=(
                    "id",
                    "bot_id",
                    "owner_id",
                    "event_type",
                    "level",
                    "message",
                    "context",
                    "created_at",
                ),
            )
            processed = await source.fetch(
                "SELECT p.id, b.public_id, p.bot_id, p.update_id, p.created_at "
                "FROM welcome_bots_processedupdate p "
                "JOIN welcome_bots_managedbot b ON b.id = p.bot_id ORDER BY p.id"
            )
            await target.executemany(
                "INSERT INTO inbox_event "
                "(source_key, bot_id, update_id, payload, status, attempts, available_at, "
                "received_at, processed_at) VALUES ($1,$2,$3,'{}'::jsonb,'completed',1,$4,$4,$4)",
                [(f"bot:{row[1]}", row[2], row[3], row[4]) for row in processed],
            )
            counts["processed_update"] = len(processed)
            await reset_sequences(
                target,
                (
                    "owner",
                    "managed_bot",
                    "channel",
                    "welcome_message_version",
                    "welcome_media",
                    "welcome_draft",
                    "welcome_draft_media",
                    "contact",
                    "join_request",
                    "greeting_delivery",
                    "event_log",
                    "inbox_event",
                ),
            )
        print(" ".join(f"{table}={count}" for table, count in counts.items()))
    finally:
        await source.close()
        await target.close()


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Replace a staging Welcome DB from Django data")
    parser.add_argument("--source-database-url", default=os.environ.get("SOURCE_DATABASE_URL", ""))
    parser.add_argument("--target-database-url", default=os.environ.get("TARGET_DATABASE_URL", ""))
    parser.add_argument("--confirm-target-database", required=True)
    parser.add_argument(
        "--legacy-django-secret-key",
        default=os.environ.get("LEGACY_DJANGO_SECRET_KEY", ""),
        help=argparse.SUPPRESS,
    )
    parser.add_argument(
        "--token-encryption-keys",
        default=os.environ.get("WELCOME_TOKEN_ENCRYPTION_KEYS", ""),
        help=argparse.SUPPRESS,
    )
    args = parser.parse_args()
    required = (
        args.source_database_url,
        args.target_database_url,
        args.legacy_django_secret_key,
        args.token_encryption_keys,
    )
    if not all(required):
        parser.error("source, target and both encryption settings are required")
    return args


if __name__ == "__main__":
    asyncio.run(migrate(parse_args()))
