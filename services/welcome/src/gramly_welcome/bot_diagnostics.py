"""Owner-visible incident state. No changes to bot/channel permissions."""
from __future__ import annotations

import uuid
from datetime import UTC, datetime
from typing import Any

from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from .models import Channel, DeliveryOperation, EventLog, ManagedBot, OwnerNotification


def diagnostics_text(bot: ManagedBot) -> str:
    issues = bot.delivery_issues or {}
    lines = []
    if "invalid_bot_token" in issues:
        lines.append("Токен бота недействителен. Получите новый токен в BotFather и нажмите «Обновить токен». "
                     "Каналы и цепочки сохранятся.")
    for key, issue in issues.items():
        if not key.startswith("external_approval:"):
            continue
        lines.append(external_approval_text(issue, key))
    return "\n\n".join(lines)


def external_approval_text(issue: dict[str, Any], key: str) -> str:
    channel = str(issue.get("channel_title") or f"канал #{issue.get('channel_id') or key.split(':', 1)[1]}")
    approver = str(issue.get("approver_username") or issue.get("approver_id") or "другой бот")
    if issue.get("approver_username"):
        approver = f"@{approver}"
    sent, cancelled = issue.get("sent_steps"), issue.get("cancelled_steps")
    steps = f" Отправлено шагов: {sent}; отменено: {cancelled}." if isinstance(sent, int) and isinstance(cancelled, int) else ""
    return (f"{channel}: заявку принял {approver} раньше завершения приветствия.{steps} "
            "Отключите у него автоприём или право принимать заявки и нажмите «Проверить каналы». "
            "После принятия Telegram закрывает временный адрес для оставшихся сообщений.")


async def record_incident(
    session: AsyncSession, bot_id: int, kind: str, *, channel_id: int | None = None,
    approver_id: int | None = None, approver_username: str = "", delivery_id: int | None = None,
) -> None:
    if kind not in {"invalid_bot_token", "external_approval"}:
        raise ValueError("Unsupported bot incident")
    bot = await session.scalar(select(ManagedBot).where(ManagedBot.id == bot_id)
                               .with_for_update().execution_options(populate_existing=True))
    if bot is None:
        return
    key = f"{kind}:{channel_id}" if kind == "external_approval" else kind
    issues = dict(bot.delivery_issues or {})
    if key in issues and kind != "external_approval":
        return
    is_new = key not in issues
    incident_id = uuid.uuid4().hex if is_new else issues[key]["id"]
    channel = await session.get(Channel, channel_id) if channel_id is not None else None
    sent = cancelled = None
    if delivery_id is not None:
        counts = await session.execute(
            select(DeliveryOperation.status, func.count(DeliveryOperation.id))
            .where(DeliveryOperation.flow_delivery_id == delivery_id)
            .group_by(DeliveryOperation.status)
        )
        by_status: dict[str, int] = {row[0]: row[1] for row in counts.all()}
        sent, cancelled = int(by_status.get("sent", 0)), int(by_status.get("cancelled", 0))
    issue = {
        "id": incident_id, "opened_at": issues.get(key, {}).get("opened_at") or datetime.now(UTC).isoformat(),
        "last_seen_at": datetime.now(UTC).isoformat(), "channel_id": channel_id,
        "channel_title": channel.title[:100] if channel else "",
        "approver_id": approver_id, "approver_username": approver_username[:64],
        "sent_steps": sent, "cancelled_steps": cancelled,
    }
    issues[key] = issue
    bot.delivery_issues = issues
    if is_new:
        body = f"Бот @{bot.username or bot.display_name}\n\n{external_approval_text(issue, key) if kind == 'external_approval' else diagnostics_text(bot)}"
        session.add(OwnerNotification(owner_id=bot.owner_id, kind="delivery_incident",
            dedupe_key=f"delivery-incident:{incident_id}", sequence=0, due_at=datetime.now(UTC),
            payload={"title": "Требуется внимание к доставке", "body": body}))
    session.add(EventLog(bot_id=bot.id, owner_id=bot.owner_id,
                         event_type="delivery_incident_opened" if is_new else "delivery_incident_observed",
                         level="warning", message=kind, context={
                             "incident_id": incident_id, "channel_id": channel_id,
                             "approver_id": approver_id, "approver_username": approver_username[:64],
                             "sent_steps": sent, "cancelled_steps": cancelled,
                         }))
    await session.flush()


async def resolve_incident(
    session: AsyncSession, bot_id: int, key: str, *, expected_id: str | None = None,
) -> None:
    bot = await session.scalar(select(ManagedBot).where(ManagedBot.id == bot_id)
                               .with_for_update().execution_options(populate_existing=True))
    if bot is None or key not in (bot.delivery_issues or {}):
        return
    if expected_id is not None and bot.delivery_issues[key].get("id") != expected_id:
        return
    issues = dict(bot.delivery_issues)
    incident = issues.pop(key)
    bot.delivery_issues = issues
    session.add(EventLog(bot_id=bot.id, owner_id=bot.owner_id, event_type="delivery_incident_resolved",
                         message=key.split(":")[0], context={"incident_id": incident["id"]}))
    await session.flush()
