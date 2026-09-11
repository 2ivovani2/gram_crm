"""Owner-visible incident state. No changes to bot/channel permissions."""
from __future__ import annotations

import uuid
from datetime import UTC, datetime

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from .models import EventLog, ManagedBot, OwnerNotification


def diagnostics_text(bot: ManagedBot) -> str:
    issues = bot.delivery_issues or {}
    lines = []
    if "invalid_bot_token" in issues:
        lines.append("Токен бота недействителен. Получите новый токен в BotFather и нажмите «Обновить токен». "
                     "Каналы и цепочки сохранятся.")
    if any(key.startswith("external_approval:") for key in issues):
        lines.append("Зафиксировано принятие заявок другим ботом раньше GramlyHello. Отключите его приветствия и автоприём "
                     "в затронутом канале. После принятия Telegram закрывает временный адрес; "
                     "без ранее выданного разрешения на сообщения приветствие недоступно. "
                     "Автоприём Gramly мы не изменяли.")
    return "\n\n".join(lines)


async def record_incident(session: AsyncSession, bot_id: int, kind: str, *, channel_id: int | None = None) -> None:
    if kind not in {"invalid_bot_token", "external_approval"}:
        raise ValueError("Unsupported bot incident")
    bot = await session.scalar(select(ManagedBot).where(ManagedBot.id == bot_id)
                               .with_for_update().execution_options(populate_existing=True))
    if bot is None:
        return
    key = f"{kind}:{channel_id}" if kind == "external_approval" else kind
    issues = dict(bot.delivery_issues or {})
    if key in issues:
        return
    incident_id = uuid.uuid4().hex
    issues[key] = {"id": incident_id, "opened_at": datetime.now(UTC).isoformat(), "channel_id": channel_id}
    bot.delivery_issues = issues
    body = f"Бот @{bot.username or bot.display_name}\n\n{diagnostics_text(bot)}"
    session.add(OwnerNotification(owner_id=bot.owner_id, kind="delivery_incident",
        dedupe_key=f"delivery-incident:{incident_id}", sequence=0, due_at=datetime.now(UTC),
        payload={"title": "Требуется внимание к доставке", "body": body}))
    session.add(EventLog(bot_id=bot.id, owner_id=bot.owner_id, event_type="delivery_incident_opened",
                         level="warning", message=kind, context={"incident_id": incident_id, "channel_id": channel_id}))
    await session.flush()


async def resolve_incident(session: AsyncSession, bot_id: int, key: str) -> None:
    bot = await session.scalar(select(ManagedBot).where(ManagedBot.id == bot_id)
                               .with_for_update().execution_options(populate_existing=True))
    if bot is None or key not in (bot.delivery_issues or {}):
        return
    issues = dict(bot.delivery_issues)
    incident = issues.pop(key)
    bot.delivery_issues = issues
    session.add(EventLog(bot_id=bot.id, owner_id=bot.owner_id, event_type="delivery_incident_resolved",
                         message=key.split(":")[0], context={"incident_id": incident["id"]}))
    await session.flush()
