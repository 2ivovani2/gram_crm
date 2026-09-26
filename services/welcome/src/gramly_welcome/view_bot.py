"""GramlyView: first-party channel observations with a fail-closed news gate.

Post views are intentionally absent until an authorized MTProto collector has
actually sampled them. Bot API member counts are never substituted for reach.
"""

from __future__ import annotations

import asyncio
import csv
import html
import io
import logging
import re
from datetime import UTC, datetime, timedelta
from typing import Any

from aiogram import Bot
from aiogram.enums import ChatType, ParseMode
from aiogram.exceptions import TelegramAPIError
from aiogram.types import (
    BufferedInputFile,
    InlineKeyboardButton,
    InlineKeyboardMarkup,
    LinkPreviewOptions,
    User,
)
from sqlalchemy import delete, func, select
from sqlalchemy.dialects.postgresql import insert

from .config import get_settings
from .crypto import TokenKeyring
from .db import session_factory
from .models import (
    OwnerServiceActivity,
    ViewChannelSnapshot,
    ViewCollection,
    ViewCollectionItem,
    ViewFavorite,
    ViewUserState,
)
from .owner_repository import owner_from_telegram
from .required_channel import check_required_membership, required_channel_config
from .view_metrics import ViewWindows, measured_windows
from .view_pool import analytics_available, lease_account
from .view_private import PrivateChannelError, connect_private, parse_invites
from .view_reports import ReportPoint, ViewReport, render_report_png

logger = logging.getLogger(__name__)
CHANNEL_PATTERN = re.compile(
    r"(?<![\w/])(?:@([A-Za-z][A-Za-z0-9_]{4,31})|(?:https?://)?(?:t\.me|telegram\.me)/([A-Za-z][A-Za-z0-9_]{4,31})(?:/\d+)?)",
    re.IGNORECASE,
)
POST_PATTERN = re.compile(r"(?:https?://)?(?:t\.me|telegram\.me)/([A-Za-z][A-Za-z0-9_]{4,31})/(\d+)", re.I)
PRIVATE_POST_PATTERN = re.compile(r"(?:https?://)?(?:t\.me|telegram\.me)/c/(\d{5,15})/(\d+)", re.I)
MAX_CHANNELS = 10
_bot: Bot | None = None
_lookup_slots = asyncio.Semaphore(8)


def view_bot() -> Bot:
    global _bot
    settings = get_settings()
    if not settings.view_bot_token:
        raise RuntimeError("WELCOME_VIEW_BOT_TOKEN is missing")
    if _bot is None:
        _bot = Bot(settings.view_bot_token)
    return _bot


def attribution(text: str) -> str:
    username = get_settings().view_bot_username.lstrip("@")
    if not re.fullmatch(r"[A-Za-z][A-Za-z0-9_]{4,31}", username):
        raise RuntimeError("WELCOME_VIEW_BOT_USERNAME is missing or invalid")
    return (
        f"{text.rstrip()}\n\n"
        f'Аналитика подготовлена в GramlyView · <a href="https://t.me/{username}">Открыть бот</a>'
    )


def parse_channels(text: str) -> list[str]:
    """Accept names, public links and post links; deduplicate case-insensitively."""
    result: list[str] = []
    seen: set[str] = set()
    for match in CHANNEL_PATTERN.finditer(text):
        username = match.group(1) or match.group(2)
        if username and username.lower() != "joinchat" and username.lower() not in seen:
            result.append(username)
            seen.add(username.lower())
        if len(result) >= MAX_CHANNELS:
            break
    return result


def _menu() -> InlineKeyboardMarkup:
    hello = get_settings().interface_bot_username.lstrip("@")
    rows = [
        [InlineKeyboardButton(text="Избранные каналы", callback_data="view:favorites")],
        [InlineKeyboardButton(text="Коллекции", callback_data="view:collections")],
        [InlineKeyboardButton(text="Как считаются показатели", callback_data="view:method")],
    ]
    if hello:
        rows.append([InlineKeyboardButton(text="Gramly Hello", url=f"https://t.me/{hello}?start=from_view")])
    return InlineKeyboardMarkup(inline_keyboard=rows)


async def _send(chat_id: int, text: str, *, keyboard: InlineKeyboardMarkup | None = None) -> None:
    # One exit point ensures even error and collection cards retain attribution
    # when forwarded or copied without their inline keyboard.
    await view_bot().send_message(
        chat_id,
        attribution(text),
        parse_mode=ParseMode.HTML,
        reply_markup=keyboard,
        link_preview_options=LinkPreviewOptions(is_disabled=True),
    )


async def _gate(chat_id: int, owner_id: int, query: str = "") -> bool:
    response: str | None = None
    keyboard: InlineKeyboardMarkup | None = None
    async with session_factory() as session:
        from .models import Owner

        owner = await session.get(Owner, owner_id)
        if owner is None:
            return False
        config = await required_channel_config(session)
        if not config.enabled or not config.channel_id or not config.url:
            logger.error("GramlyView gate unavailable: required_news_channel is not configured")
            response = "Проверка подписки пока не настроена. Мы уже сообщили администратору."
        else:
            result = await check_required_membership(session, view_bot(), owner, force=True)
            if result.temporarily_unavailable:
                logger.warning("GramlyView gate Telegram lookup unavailable channel_id=%s", config.channel_id)
                response = "Не удалось проверить подписку в Telegram. Попробуйте ещё раз через минуту."
            elif result.allowed:
                return True
            else:
                if query:
                    protected = (
                        "sealed:" + TokenKeyring.parse(get_settings().token_encryption_keys).encrypt(query)
                        if parse_invites(query) else query
                    )
                    await session.execute(
                        insert(ViewUserState)
                        .values(owner_id=owner_id, pending_query=protected, updated_at=datetime.now(UTC))
                        .on_conflict_do_update(
                            index_elements=[ViewUserState.owner_id],
                            set_={"pending_query": protected, "updated_at": datetime.now(UTC)},
                        )
                    )
                    await session.commit()
                keyboard = InlineKeyboardMarkup(
                    inline_keyboard=[
                        [InlineKeyboardButton(text="Подписаться", url=config.url)],
                        [InlineKeyboardButton(text="Проверить подписку", callback_data="view:check")],
                    ]
                )
                response = (
                    f"Чтобы открыть аналитику, подпишитесь на {html.escape(config.title)} "
                    "и нажмите «Проверить подписку»."
                )
    if response:
        await _send(chat_id, response, keyboard=keyboard)
    return False


def _channel_card(
    title: str,
    username: str | None,
    members: int | None,
    observed: datetime,
    windows: ViewWindows,
    *,
    post_id: int | None = None,
    post_views: int | None = None,
) -> str:
    safe_name = html.escape(title[:120])
    link = f"https://t.me/{username}" if username else ""
    lines = [
        f'<b><a href="{link}">{safe_name}</a></b>' if link else f"<b>{safe_name}</b> · приватный канал",
        (f"Подписчики · {members:,}".replace(",", " ") if members is not None else "Подписчики · нет данных"),
        "Просмотры 24 / 48 / 72 ч · "
        + (
            f"{windows.views_24h:,} / {windows.views_48h:,} / {windows.views_72h:,}".replace(",", " ")
            if windows.views_24h is not None
            else "нет данных"
        ),
        "ER24 · " + (f"{windows.er24_percent:.2f}%" if windows.er24_percent is not None else "нет данных"),
        f"Обновлено · {observed:%d.%m.%Y %H:%M} UTC",
    ]
    if post_id is not None and link:
        lines.insert(
            1, f'<a href="{link}/{post_id}">Публикация #{post_id}</a> · '
            + (f"{post_views:,} просмотров".replace(",", " ") if post_views is not None else "нет измерений просмотров")
        )
    elif post_id is not None:
        lines.insert(1, f"Публикация #{post_id} · " + (f"{post_views:,} просмотров".replace(",", " ") if post_views is not None else "нет измерений просмотров"))
    return "\n".join(lines)


async def _post_views(channel_id: int, post_id: int | None) -> int | None:
    if post_id is None:
        return None
    from .models import ViewPostObservation

    async with session_factory() as session:
        result = await session.scalar(select(ViewPostObservation.views).where(
            ViewPostObservation.channel_id == channel_id,
            ViewPostObservation.post_id == post_id,
        ).order_by(ViewPostObservation.observed_at.desc()).limit(1))
        return int(result) if result is not None else None


def _report_keyboard(channel_id: int, *, public_username: str | None = None) -> InlineKeyboardMarkup:
    rows = [
        [InlineKeyboardButton(text="Статистика картинкой", callback_data=f"view:image:{channel_id}")],
        [InlineKeyboardButton(text="Топ публикаций", callback_data=f"view:top:{channel_id}")],
        [InlineKeyboardButton(text="Экспорт CSV", callback_data=f"view:export:{channel_id}")],
    ]
    if public_username:
        rows.append([InlineKeyboardButton(text="В избранное", callback_data=f"view:save:{channel_id}:{public_username}"[:64])])
    else:
        rows.append([InlineKeyboardButton(text="В избранное", callback_data=f"view:saveprivate:{channel_id}")])
    return InlineKeyboardMarkup(inline_keyboard=rows)


async def _report_for(owner_id: int, channel_id: int) -> ViewReport | None:
    from .models import ViewPrivateChannel

    async with session_factory() as session:
        latest = await session.scalar(select(ViewChannelSnapshot).where(
            ViewChannelSnapshot.channel_id == channel_id,
        ).order_by(ViewChannelSnapshot.observed_at.desc()).limit(1))
        if latest is None:
            return None
        if latest.username:
            try:
                current = await view_bot().get_chat(f"@{latest.username}")
            except TelegramAPIError:
                return None
            if current.type != ChatType.CHANNEL or current.id != channel_id:
                return None
        else:
            permitted = await session.scalar(select(ViewPrivateChannel.id).where(
                ViewPrivateChannel.owner_id == owner_id,
                ViewPrivateChannel.channel_id == channel_id,
                ViewPrivateChannel.status == "active",
            ))
            if permitted is None:
                return None
        snapshots = (await session.scalars(select(ViewChannelSnapshot).where(
            ViewChannelSnapshot.channel_id == channel_id,
            ViewChannelSnapshot.subscriber_count.is_not(None),
            ViewChannelSnapshot.observed_at >= datetime.now(UTC) - timedelta(days=30),
        ).order_by(ViewChannelSnapshot.observed_at))).all()
        windows = await measured_windows(session, channel_id, latest.subscriber_count or 0, now=datetime.now(UTC))
    days: dict[str, ReportPoint] = {}
    for snapshot in snapshots:
        if snapshot.subscriber_count is not None:
            days[snapshot.observed_at.date().isoformat()] = ReportPoint(snapshot.observed_at, snapshot.subscriber_count)
    return ViewReport(
        title=latest.title, subscribers=latest.subscriber_count, observed_at=latest.observed_at,
        points=tuple(days.values()), views_24h=windows.views_24h,
        er24=windows.er24_percent, views_48h=windows.views_48h,
        views_72h=windows.views_72h,
    )


async def _send_report_image(chat_id: int, owner_id: int, channel_id: int) -> None:
    report = await _report_for(owner_id, channel_id)
    if report is None:
        await _send(chat_id, "Отчёт недоступен или у вас нет доступа к этому каналу.")
        return
    await view_bot().send_photo(
        chat_id, BufferedInputFile(render_report_png(report), filename="gramlyview-report.png"),
        caption=attribution(f"<b>{html.escape(report.title)}</b>\nДанные на {report.observed_at:%d.%m.%Y %H:%M} UTC"),
        parse_mode=ParseMode.HTML,
    )


async def _send_report_csv(chat_id: int, owner_id: int, channel_id: int) -> None:
    report = await _report_for(owner_id, channel_id)
    if report is None:
        await _send(chat_id, "Отчёт недоступен или у вас нет доступа к этому каналу.")
        return
    output = io.StringIO()
    writer = csv.writer(output)
    writer.writerow(["date_utc", "subscribers"])
    for point in report.points:
        writer.writerow([point.day.isoformat(), point.subscribers])
    writer.writerow([])
    writer.writerow(["Источник", f"GramlyView https://t.me/{get_settings().view_bot_username.lstrip('@')}"])
    await view_bot().send_document(
        chat_id, BufferedInputFile(output.getvalue().encode("utf-8-sig"), filename="gramlyview-subscribers.csv"),
        caption=attribution(f"История подписчиков · {html.escape(report.title)}"), parse_mode=ParseMode.HTML,
    )


async def _send_top_posts(chat_id: int, owner_id: int, channel_id: int) -> None:
    report = await _report_for(owner_id, channel_id)
    if report is None:
        await _send(chat_id, "Публикации недоступны или у вас нет доступа к каналу.")
        return
    from .models import ViewPostObservation

    async with session_factory() as session:
        rows = (await session.scalars(select(ViewPostObservation).where(
            ViewPostObservation.channel_id == channel_id,
            ViewPostObservation.published_at >= datetime.now(UTC) - timedelta(days=7),
        ).order_by(ViewPostObservation.observed_at.desc()).limit(500))).all()
    latest: dict[int, ViewPostObservation] = {}
    for row in rows:
        latest.setdefault(row.post_id, row)
    top = sorted(latest.values(), key=lambda row: row.views, reverse=True)[:5]
    if not top:
        await _send(chat_id, "За последние семь дней измеренных публикаций пока нет.")
        return
    lines = [f"<b>Топ публикаций · {html.escape(report.title)}</b>"]
    for index, row in enumerate(top, 1):
        lines.append(f"{index}. Пост #{row.post_id} · {row.views:,} просмотров · {row.published_at:%d.%m %H:%M} UTC".replace(",", " "))
    await _send(chat_id, "\n".join(lines))


async def _analyze(chat_id: int, owner_id: int, query: str) -> None:
    usernames = parse_channels(query)
    invites = parse_invites(query)
    private_post = PRIVATE_POST_PATTERN.search(query)
    if not usernames and not invites and not private_post:
        await _send(
            chat_id,
            "Не нашёл ссылку на канал. Пришлите @username, t.me/канал или приглашение t.me/+…",
        )
        return
    if not await analytics_available():
        await _send(chat_id, "Аналитика временно приостановлена: доступно менее пяти рабочих аккаунтов сбора. Сохранённые отчёты остаются доступными. Попробуйте позже.")
        return
    if not await _reserve_analysis(owner_id):
        await _send(chat_id, "Слишком частые запросы. Повторите анализ через 10 секунд.")
        return
    if private_post:
        await _analyze_private(
            chat_id, owner_id, -int(f"100{private_post.group(1)}"),
            post_id=int(private_post.group(2)),
        )
    for invite in invites[:MAX_CHANNELS]:
        try:
            channel_id, title, count, observed = await asyncio.wait_for(connect_private(owner_id, invite), timeout=180)
            async with session_factory() as session:
                windows = await measured_windows(session, channel_id, count or 0, now=datetime.now(UTC))
            await _send(chat_id, _channel_card(title, None, count, observed, windows), keyboard=_report_keyboard(channel_id))
        except PrivateChannelError as exc:
            await _send(chat_id, html.escape(str(exc)))
        except Exception as exc:
            logger.error("GramlyView private analysis failed owner_id=%s class=%s", owner_id, type(exc).__name__)
            await _send(chat_id, "Не удалось проанализировать закрытый канал. Попробуйте позже.")
    for username in usernames[:max(0, MAX_CHANNELS - len(invites))]:
        try:
            channel_id, canonical, title, count, observed = await _public_channel_data(username)
        except ValueError:
            await _send(
                chat_id, f"@{html.escape(username)} — это не канал. Отправьте ссылку на публичный канал."
            )
            continue
        except TelegramAPIError:
            await _send(
                chat_id,
                f"Не удалось получить данные @{html.escape(username)}. Проверьте ссылку и доступ бота к каналу.",
            )
            continue
        post = POST_PATTERN.search(query)
        post_id = int(post.group(2)) if post and post.group(1).lower() == username.lower() else None
        try:
            from .view_collector import collect_one

            async with lease_account() as (_account_id, collector):
                await asyncio.wait_for(collect_one(collector, canonical, channel_id), timeout=90)
        except Exception as exc:
            logger.info("View immediate collection deferred channel_id=%s class=%s", channel_id, type(exc).__name__)
        async with session_factory() as session:
            windows = await measured_windows(session, channel_id, count or 0, now=datetime.now(UTC))
        keyboard = _report_keyboard(channel_id, public_username=canonical)
        await _send(
            chat_id,
            _channel_card(title, canonical, count, observed, windows, post_id=post_id,
                          post_views=await _post_views(channel_id, post_id)),
            keyboard=keyboard,
        )


async def _public_channel_data(username: str) -> tuple[int, str, str, int, datetime]:
    now = datetime.now(UTC)
    async with session_factory() as session:
        cached = await session.scalar(
            select(ViewChannelSnapshot)
            .where(
                func.lower(ViewChannelSnapshot.username) == username.lower(),
                ViewChannelSnapshot.observed_at >= now - timedelta(minutes=5),
                ViewChannelSnapshot.subscriber_count.is_not(None),
            )
            .order_by(ViewChannelSnapshot.observed_at.desc(), ViewChannelSnapshot.id.desc())
            .limit(1)
        )
        if cached is not None and cached.subscriber_count is not None:
            return (
                cached.channel_id,
                cached.username,
                cached.title,
                cached.subscriber_count,
                cached.observed_at,
            )
    async with _lookup_slots:
        chat = await view_bot().get_chat(f"@{username}")
        if chat.type != ChatType.CHANNEL:
            raise ValueError("not_a_channel")
        count = await view_bot().get_chat_member_count(chat.id)
    observed = datetime.now(UTC)
    canonical = chat.username or username
    title = chat.title or canonical
    async with session_factory() as session, session.begin():
        session.add(
            ViewChannelSnapshot(
                channel_id=chat.id,
                username=canonical,
                title=title,
                subscriber_count=count,
                observed_at=observed,
            )
        )
    return chat.id, canonical, title, count, observed


async def _analyze_private(chat_id: int, owner_id: int, channel_id: int, *, post_id: int | None = None) -> None:
    from .models import ViewPrivateChannel

    if not await analytics_available():
        await _send(chat_id, "Аналитика временно приостановлена: рабочих аккаунтов меньше пяти.")
        return
    async with session_factory() as session:
        linked = await session.scalar(select(ViewPrivateChannel).where(
            ViewPrivateChannel.owner_id == owner_id,
            ViewPrivateChannel.channel_id == channel_id,
            ViewPrivateChannel.status == "active",
        ))
    if linked is None:
        await _send(chat_id, "Для анализа закрытого канала пришлите его ссылку-приглашение t.me/+…")
        return
    from .view_private import analyze_saved_private
    try:
        title, count, observed = await analyze_saved_private(linked.account_id, channel_id)
        async with session_factory() as session:
            windows = await measured_windows(session, channel_id, count or 0, now=datetime.now(UTC))
        await _send(chat_id, _channel_card(title, None, count, observed, windows, post_id=post_id,
                                          post_views=await _post_views(channel_id, post_id)), keyboard=_report_keyboard(channel_id))
    except PrivateChannelError:
        await _send(chat_id, "Аккаунт сбора потерял доступ к каналу. Отправьте новое приглашение.")
    except Exception as exc:
        logger.error("View saved private analysis failed channel_id=%s class=%s", channel_id, type(exc).__name__)
        await _send(chat_id, "Не удалось обновить закрытый канал. Повторите позже.")


async def _reserve_analysis(owner_id: int) -> bool:
    now = datetime.now(UTC)
    async with session_factory() as session, session.begin():
        await session.execute(
            insert(ViewUserState)
            .values(owner_id=owner_id, updated_at=now)
            .on_conflict_do_nothing(index_elements=[ViewUserState.owner_id])
        )
        state = await session.scalar(
            select(ViewUserState).where(ViewUserState.owner_id == owner_id).with_for_update()
        )
        if state is None:
            raise RuntimeError("View request state was not created")
        if state.last_analysis_at and state.last_analysis_at > now - timedelta(seconds=10):
            return False
        state.last_analysis_at = now
        state.updated_at = now
        return True


async def _favorites(chat_id: int, owner_id: int) -> None:
    async with session_factory() as session:
        rows = (
            await session.scalars(
                select(ViewFavorite)
                .where(ViewFavorite.owner_id == owner_id)
                .order_by(ViewFavorite.created_at.desc())
                .limit(20)
            )
        ).all()
    if not rows:
        await _send(chat_id, "Избранное пусто. Откройте канал и нажмите «В избранное».")
        return
    body = "<b>Избранные каналы</b>\n" + "\n".join(
        (f'• <a href="https://t.me/{row.username}">{html.escape(row.title)}</a>'
         if row.username else f"• {html.escape(row.title)} · закрытый")
        for row in rows
    )
    await _send(chat_id, body)


async def _collections(chat_id: int, owner_id: int) -> None:
    async with session_factory() as session:
        rows = (
            await session.scalars(
                select(ViewCollection)
                .where(
                    ViewCollection.owner_id == owner_id,
                )
                .order_by(ViewCollection.created_at.desc())
                .limit(20)
            )
        ).all()
    keyboard = InlineKeyboardMarkup(
        inline_keyboard=[
            *[
                [InlineKeyboardButton(text=row.name, callback_data=f"view:collection:{row.id}")]
                for row in rows
            ],
            [InlineKeyboardButton(text="Создать коллекцию", callback_data="view:collection:new")],
        ]
    )
    await _send(
        chat_id,
        "<b>Коллекции каналов</b>\nСохраните несколько каналов для быстрого сравнения."
        if rows
        else "Коллекций пока нет. Создайте первую и добавьте каналы.",
        keyboard=keyboard,
    )


async def _collection_detail(chat_id: int, owner_id: int, collection_id: int) -> None:
    async with session_factory() as session:
        collection = await session.scalar(
            select(ViewCollection).where(
                ViewCollection.id == collection_id,
                ViewCollection.owner_id == owner_id,
            )
        )
        items = (
            (
                await session.scalars(
                    select(ViewCollectionItem)
                    .where(ViewCollectionItem.collection_id == collection_id)
                    .order_by(ViewCollectionItem.created_at)
                    .limit(MAX_CHANNELS)
                )
            ).all()
            if collection is not None
            else []
        )
    if collection is None:
        await _send(chat_id, "Коллекция не найдена.")
        return
    lines = [f"<b>{html.escape(collection.name)}</b>"]
    lines += [
        f'• <a href="https://t.me/{item.username}">{html.escape(item.title)}</a>'
        if item.username else f"• {html.escape(item.title)} · закрытый"
        for item in items
    ]
    keyboard = InlineKeyboardMarkup(
        inline_keyboard=[
            [
                InlineKeyboardButton(
                    text="Добавить канал", callback_data=f"view:collection:add:{collection_id}"
                )
            ],
            [
                InlineKeyboardButton(
                    text="Проверить каналы", callback_data=f"view:collection:analyze:{collection_id}"
                )
            ],
            *[
                [
                    InlineKeyboardButton(
                        text=f"Убрать {('@' + item.username) if item.username else item.title[:24]}",
                        callback_data=f"view:collection:remove:{collection_id}:{item.channel_id}",
                    )
                ]
                for item in items
            ],
            [InlineKeyboardButton(text="К коллекциям", callback_data="view:collections")],
        ]
    )
    await _send(
        chat_id,
        "\n".join(lines) if items else f"<b>{html.escape(collection.name)}</b>\nЗдесь пока нет каналов.",
        keyboard=keyboard,
    )


async def _set_pending_action(owner_id: int, action: str) -> None:
    async with session_factory() as session, session.begin():
        await session.execute(
            insert(ViewUserState)
            .values(owner_id=owner_id, pending_action=action, updated_at=datetime.now(UTC))
            .on_conflict_do_update(
                index_elements=[ViewUserState.owner_id],
                set_={
                    "pending_action": action,
                    "updated_at": datetime.now(UTC),
                },
            )
        )


async def _handle_collection_text(chat_id: int, owner_id: int, text: str) -> bool:
    async with session_factory() as session:
        state = await session.get(ViewUserState, owner_id)
        action = state.pending_action if state else ""
    if not action:
        return False
    if action == "collection_name":
        name = " ".join(text.strip().split())[:80]
        if len(name) < 2:
            await _send(chat_id, "Название должно содержать не меньше двух символов.")
            return True
        error = ""
        created = None
        async with session_factory() as session, session.begin():
            count = len(
                (
                    await session.scalars(
                        select(ViewCollection.id).where(
                            ViewCollection.owner_id == owner_id,
                        )
                    )
                ).all()
            )
            if count >= 20:
                error = "Достигнут лимит в 20 коллекций."
            else:
                created = await session.scalar(
                    insert(ViewCollection)
                    .values(owner_id=owner_id, name=name)
                    .on_conflict_do_nothing(constraint="uq_view_collection_owner_name")
                    .returning(ViewCollection.id)
                )
                state = await session.get(ViewUserState, owner_id)
                if state:
                    state.pending_action = ""
        if error:
            await _send(chat_id, error)
            return True
        await _send(chat_id, "Коллекция создана." if created else "Такая коллекция уже есть.")
        await _collections(chat_id, owner_id)
        return True
    if action.startswith("collection_add:"):
        collection_id = int(action.split(":", 1)[1])
        names = parse_channels(text)
        invites = parse_invites(text)
        if len(names) + len(invites) != 1:
            await _send(chat_id, "Отправьте одну публичную ссылку, @username или приглашение закрытого канала.")
            return True
        if invites:
            if not await analytics_available():
                await _send(chat_id, "Аналитика временно приостановлена: рабочих аккаунтов меньше пяти.")
                return True
            try:
                channel_id, title, _members, _observed = await asyncio.wait_for(
                    connect_private(owner_id, invites[0]), timeout=180
                )
                username = ""
            except PrivateChannelError as exc:
                await _send(chat_id, html.escape(str(exc)))
                return True
            except Exception as exc:
                logger.error("View collection private add failed class=%s", type(exc).__name__)
                await _send(chat_id, "Не удалось добавить закрытый канал. Повторите позже.")
                return True
        else:
            try:
                chat = await view_bot().get_chat(f"@{names[0]}")
                if chat.type != ChatType.CHANNEL or not chat.username:
                    raise ValueError("not_public_channel")
                channel_id, username, title = chat.id, chat.username, chat.title or chat.username
            except (TelegramAPIError, ValueError):
                await _send(chat_id, "Не удалось открыть публичный канал. Проверьте ссылку.")
                return True
        error = ""
        async with session_factory() as session, session.begin():
            collection = await session.scalar(
                select(ViewCollection).where(
                    ViewCollection.id == collection_id,
                    ViewCollection.owner_id == owner_id,
                )
            )
            if collection is None:
                error = "Коллекция больше не доступна."
            else:
                count = len(
                    (
                        await session.scalars(
                            select(ViewCollectionItem.id).where(
                                ViewCollectionItem.collection_id == collection_id,
                            )
                        )
                    ).all()
                )
                if count >= MAX_CHANNELS:
                    error = f"В коллекции может быть не больше {MAX_CHANNELS} каналов."
                else:
                    await session.execute(
                        insert(ViewCollectionItem)
                        .values(
                            collection_id=collection_id,
                            channel_id=channel_id,
                            username=username,
                            title=title,
                        )
                        .on_conflict_do_nothing(constraint="uq_view_collection_item")
                    )
                    state = await session.get(ViewUserState, owner_id)
                    if state:
                        state.pending_action = ""
        if error:
            await _send(chat_id, error)
            return True
        await _collection_detail(chat_id, owner_id, collection_id)
        return True
    return False


async def _maybe_offer_hello(chat_id: int, owner_id: int) -> None:
    settings = get_settings()
    if not settings.interface_bot_username:
        return
    now = datetime.now(UTC)
    async with session_factory() as session, session.begin():
        activity = await session.scalar(
            select(OwnerServiceActivity)
            .where(
                OwnerServiceActivity.owner_id == owner_id,
                OwnerServiceActivity.service == "view",
            )
            .with_for_update()
        )
        hello = await session.scalar(
            select(OwnerServiceActivity.id).where(
                OwnerServiceActivity.owner_id == owner_id,
                OwnerServiceActivity.service == "hello",
            )
        )
        if (
            activity is None
            or hello is not None
            or activity.cross_offer_hidden
            or (activity.cross_offer_at and activity.cross_offer_at > now - timedelta(days=30))
        ):
            return
        activity.cross_offer_at = now
    keyboard = InlineKeyboardMarkup(
        inline_keyboard=[
            [
                InlineKeyboardButton(
                    text="Открыть Gramly Hello",
                    url=f"https://t.me/{settings.interface_bot_username.lstrip('@')}?start=from_view",
                )
            ],
            [InlineKeyboardButton(text="Скрыть предложение", callback_data="view:hide_offer")],
        ]
    )
    await _send(
        chat_id,
        "<b>Ещё есть Gramly Hello</b>\nОн встречает новых участников канала персональными сообщениями.",
        keyboard=keyboard,
    )


async def process_view_update(payload: dict[str, Any]) -> None:
    message = payload.get("message")
    callback = payload.get("callback_query")
    if not isinstance(message, dict) and not isinstance(callback, dict):
        return
    event = message if isinstance(message, dict) else callback
    if not isinstance(event, dict):
        return
    sender = event.get("from")
    if not isinstance(sender, dict) or sender.get("is_bot"):
        return
    message_context = message if isinstance(message, dict) else event.get("message") or {}
    chat_id = int(message_context.get("chat", {}).get("id") or 0)
    if not chat_id or chat_id != int(sender["id"]):
        return  # analytics are private; no unsolicited group replies
    data = str(callback.get("data") or "") if isinstance(callback, dict) else ""
    text = str(message.get("text") or message.get("caption") or "") if message else ""
    forwarded = message.get("forward_origin") if message else None
    private_origin_id = 0
    if isinstance(forwarded, dict):
        origin = forwarded.get("chat") or {}
        username = origin.get("username")
        if username:
            text = f"@{username}" if not text else f"@{username}\n{text}"
        elif forwarded.get("type") == "channel":
            private_origin_id = int(origin.get("id") or 0)
    activity_event = (
        "start_from_hello"
        if text.startswith("/start from_hello")
        else "start"
        if text.startswith("/start")
        else "analyze"
        if text or private_origin_id
        else "menu"
    )
    async with session_factory() as session:
        owner = await owner_from_telegram(
            session, User.model_validate(sender), service="view", event=activity_event
        )
        owner_id = owner.id
    if text.startswith("/start"):
        async with session_factory() as session, session.begin():
            state = await session.get(ViewUserState, owner_id)
            if state:
                state.pending_query = ""
                state.pending_action = ""
    if isinstance(callback, dict):
        try:
            await view_bot().answer_callback_query(str(callback["id"]))
        except TelegramAPIError:
            pass
    if data == "view:check":
        if not await _gate(chat_id, owner_id):
            return
        async with session_factory() as session:
            state = await session.get(ViewUserState, owner_id)
            pending = state.pending_query if state else ""
            if state and pending:
                state.pending_query = ""
                await session.commit()
        if pending:
            if pending.startswith("sealed:"):
                pending = TokenKeyring.parse(get_settings().token_encryption_keys).decrypt(pending[7:])
            if pending.startswith("private:") and pending[8:].lstrip("-").isdigit():
                await _analyze_private(chat_id, owner_id, int(pending[8:]))
            elif not await _handle_collection_text(chat_id, owner_id, pending):
                await _analyze(chat_id, owner_id, pending)
        else:
            await _send(
                chat_id,
                "Подписка подтверждена. Отправьте @канал, ссылку на публикацию или список каналов.",
                keyboard=_menu(),
            )
        await _maybe_offer_hello(chat_id, owner_id)
        return
    if data == "view:hide_offer":
        async with session_factory() as session, session.begin():
            activity = await session.scalar(
                select(OwnerServiceActivity).where(
                    OwnerServiceActivity.owner_id == owner_id,
                    OwnerServiceActivity.service == "view",
                )
            )
            if activity:
                activity.cross_offer_hidden = True
        await _send(chat_id, "Больше не будем предлагать Gramly Hello. Ссылка на него остаётся в меню.")
        return
    pending_query = f"private:{private_origin_id}" if private_origin_id else text
    if not await _gate(chat_id, owner_id, pending_query if not pending_query.startswith("/start") else ""):
        return
    if data == "view:favorites":
        await _favorites(chat_id, owner_id)
    elif data.startswith("view:image:"):
        try:
            await _send_report_image(chat_id, owner_id, int(data.rsplit(":", 1)[1]))
        except ValueError:
            await _send(chat_id, "Отчёт не найден.")
    elif data.startswith("view:export:"):
        try:
            await _send_report_csv(chat_id, owner_id, int(data.rsplit(":", 1)[1]))
        except ValueError:
            await _send(chat_id, "Отчёт не найден.")
    elif data.startswith("view:top:"):
        try:
            await _send_top_posts(chat_id, owner_id, int(data.rsplit(":", 1)[1]))
        except ValueError:
            await _send(chat_id, "Публикации не найдены.")
    elif data == "view:collections":
        await _collections(chat_id, owner_id)
    elif data == "view:collection:new":
        await _set_pending_action(owner_id, "collection_name")
        await _send(chat_id, "Как назвать новую коллекцию? Отправьте короткое название одним сообщением.")
    elif data.startswith("view:collection:add:"):
        try:
            collection_id = int(data.rsplit(":", 1)[1])
        except ValueError:
            return
        async with session_factory() as session:
            exists = await session.scalar(
                select(ViewCollection.id).where(
                    ViewCollection.id == collection_id,
                    ViewCollection.owner_id == owner_id,
                )
            )
        if exists is None:
            await _send(chat_id, "Коллекция не найдена.")
            return
        await _set_pending_action(owner_id, f"collection_add:{collection_id}")
        await _send(chat_id, "Отправьте одну ссылку на канал, включая закрытое приглашение, или его @username.")
    elif data.startswith("view:collection:analyze:"):
        try:
            collection_id = int(data.rsplit(":", 1)[1])
        except ValueError:
            return
        async with session_factory() as session:
            collection = await session.scalar(
                select(ViewCollection.id).where(
                    ViewCollection.id == collection_id,
                    ViewCollection.owner_id == owner_id,
                )
            )
            items = (
                (
                    await session.scalars(
                        select(ViewCollectionItem)
                        .where(
                            ViewCollectionItem.collection_id == collection_id,
                        )
                        .limit(MAX_CHANNELS)
                    )
                ).all()
                if collection is not None
                else []
            )
        if not items:
            await _send(chat_id, "Коллекция пуста или недоступна.")
        else:
            public = [item for item in items if item.username]
            private = [item for item in items if not item.username]
            if public:
                await _analyze(chat_id, owner_id, " ".join(f"@{item.username}" for item in public))
            for item in private:
                await _analyze_private(chat_id, owner_id, item.channel_id)
    elif data.startswith("view:collection:remove:"):
        try:
            _, _, _, collection_id_raw, channel_id_raw = data.split(":", 4)
            collection_id, channel_id = int(collection_id_raw), int(channel_id_raw)
        except ValueError:
            return
        async with session_factory() as session, session.begin():
            collection = await session.scalar(
                select(ViewCollection.id).where(
                    ViewCollection.id == collection_id,
                    ViewCollection.owner_id == owner_id,
                )
            )
            if collection is not None:
                await session.execute(
                    delete(ViewCollectionItem).where(
                        ViewCollectionItem.collection_id == collection_id,
                        ViewCollectionItem.channel_id == channel_id,
                    )
                )
        await _collection_detail(chat_id, owner_id, collection_id)
    elif data.startswith("view:collection:"):
        try:
            await _collection_detail(chat_id, owner_id, int(data.rsplit(":", 1)[1]))
        except ValueError:
            await _send(chat_id, "Коллекция не найдена.")
    elif data == "view:method":
        await _send(
            chat_id,
            "Подписчики — текущий счётчик Telegram. Просмотры 24/48/72 ч и ER24 появляются только после разрешённого сбора реальных просмотров постов. Пока измерений нет, вместо нуля показываем «нет данных».",
        )
    elif data.startswith("view:saveprivate:"):
        from .models import ViewPrivateChannel

        try:
            channel_id = int(data.rsplit(":", 1)[1])
        except ValueError:
            await _send(chat_id, "Канал не найден.")
            return
        async with session_factory() as session, session.begin():
            linked = await session.scalar(select(ViewPrivateChannel).where(
                ViewPrivateChannel.owner_id == owner_id,
                ViewPrivateChannel.channel_id == channel_id,
                ViewPrivateChannel.status == "active",
            ))
            if linked is not None:
                await session.execute(insert(ViewFavorite).values(
                    owner_id=owner_id, channel_id=channel_id, username="", title=linked.title,
                ).on_conflict_do_nothing(constraint="uq_view_favorite_owner_channel"))
        await _send(chat_id, "Закрытый канал добавлен в избранное." if linked else "Нет доступа к этому закрытому каналу.")
    elif data.startswith("view:save:"):
        try:
            _, _, raw_id, username = data.split(":", 3)
            channel_id = int(raw_id)
            chat = await view_bot().get_chat(channel_id)
            if chat.username and chat.username.lower() == username.lower():
                async with session_factory() as session:
                    await session.execute(
                        insert(ViewFavorite)
                        .values(
                            owner_id=owner_id,
                            channel_id=channel_id,
                            username=username,
                            title=chat.title or username,
                        )
                        .on_conflict_do_nothing(constraint="uq_view_favorite_owner_channel")
                    )
                    await session.commit()
                await _send(chat_id, f"@{html.escape(username)} добавлен в избранное.")
        except (ValueError, TelegramAPIError):
            await _send(chat_id, "Не удалось сохранить канал. Откройте его снова и повторите попытку.")
    elif text.startswith("/start") or data == "view:menu":
        await _send(
            chat_id,
            "<b>GramlyView</b>\nАналитика Telegram-каналов. Отправьте @username, ссылку на пост, приглашение в закрытый канал или список ссылок — вернём отдельные карточки с измеренными данными.",
            keyboard=_menu(),
        )
        if text.startswith("/start"):
            await _maybe_offer_hello(chat_id, owner_id)
    elif private_origin_id:
        await _analyze_private(chat_id, owner_id, private_origin_id)
    elif text:
        if not await _handle_collection_text(chat_id, owner_id, text):
            await _analyze(chat_id, owner_id, text)
    else:
        await _send(
            chat_id, "Перешлите публикацию или отправьте ссылку на публичный либо закрытый канал.", keyboard=_menu()
        )
