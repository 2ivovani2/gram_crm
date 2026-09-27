"""Send one opt-in GramlyView digest per Moscow calendar day."""

from __future__ import annotations

import asyncio
import html
import logging
from datetime import UTC, datetime, time
from zoneinfo import ZoneInfo

from aiogram.exceptions import TelegramAPIError
from sqlalchemy import select

from .db import session_factory
from .models import Owner, ViewMonitor
from .required_channel import check_required_membership, required_channel_config
from .view_bot import _report_for, _send, view_bot
from .view_pool import analytics_available

logger = logging.getLogger(__name__)
MOSCOW = ZoneInfo("Europe/Moscow")


async def send_daily() -> None:
    if not await analytics_available():
        return
    today = datetime.now(MOSCOW).date()
    today_start = datetime.combine(today, time.min, tzinfo=MOSCOW).astimezone(UTC)
    last_id = 0
    while True:
        async with session_factory() as session:
            rows = (await session.scalars(
                select(ViewMonitor)
                .where(ViewMonitor.enabled.is_(True), ViewMonitor.id > last_id)
                .order_by(ViewMonitor.id).limit(100)
            )).all()
        if not rows:
            return
        for row in rows:
            last_id = row.id
            if row.last_sent_on == today:
                continue
            try:
                report = await _report_for(row.owner_id, row.channel_id)
            except TelegramAPIError as exc:
                logger.warning("GramlyView monitor lookup failed monitor_id=%s class=%s", row.id, type(exc).__name__)
                continue
            if report is None or report.observed_at < today_start:
                continue
            async with session_factory() as session:
                owner = await session.get(Owner, row.owner_id)
                if owner is None:
                    continue
                config = await required_channel_config(session)
                if not config.enabled or not config.channel_id:
                    continue
                membership = await check_required_membership(session, view_bot(), owner, force=True)
                if not membership.allowed or membership.temporarily_unavailable:
                    continue
            subscribers = f"{report.subscribers:,}".replace(",", " ") if report.subscribers is not None else "нет данных"
            views = f"{report.views_24h:,}".replace(",", " ") if report.views_24h is not None else "нет данных"
            growth = f"{report.growth_7d:+,}".replace(",", " ") if report.growth_7d is not None else "нет данных"
            try:
                await _send(
                    owner.telegram_id,
                    f"<b>Сводка · {html.escape(report.title)}</b>\n"
                    f"Подписчики · {subscribers}\n"
                    f"Рост за 7 дней · {growth}\n"
                    f"Средние просмотры поста через 24 ч · {views}\n"
                    f"<blockquote>По {report.views_samples_24h or 0} публикациям. "
                    f"Данные на {report.observed_at:%d.%m.%Y %H:%M} UTC.</blockquote>",
                )
            except TelegramAPIError as exc:
                logger.warning("GramlyView monitor delivery failed monitor_id=%s class=%s", row.id, type(exc).__name__)
                continue
            async with session_factory() as session, session.begin():
                monitor = await session.get(ViewMonitor, row.id, with_for_update=True)
                if monitor is not None and monitor.enabled:
                    monitor.last_sent_on = today
            await asyncio.sleep(0.05)


def run() -> None:
    logging.basicConfig(level=logging.INFO)
    asyncio.run(send_daily())
