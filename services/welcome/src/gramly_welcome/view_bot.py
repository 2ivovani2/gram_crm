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
import secrets
from collections.abc import Sequence
from datetime import UTC, datetime, timedelta
from decimal import Decimal, InvalidOperation
from typing import Any, cast

from aiogram import Bot
from aiogram.enums import ChatType, ParseMode
from aiogram.exceptions import TelegramAPIError, TelegramBadRequest
from aiogram.types import (
    BufferedInputFile,
    InlineKeyboardButton,
    InlineKeyboardMarkup,
    InputMediaPhoto,
    LinkPreviewOptions,
    Message,
    User,
)
from sqlalchemy import delete, func, select
from sqlalchemy.dialects.postgresql import insert

from .bot_ui import DEFAULT_PREMIUM_EMOJI, plain_markup, plain_text, premium_text
from .config import get_settings
from .crypto import TokenKeyring
from .db import session_factory
from .models import (
    OwnerServiceActivity,
    ViewChannelSnapshot,
    ViewCollection,
    ViewCollectionClaim,
    ViewCollectionItem,
    ViewCollectionRun,
    ViewFavorite,
    ViewMonitor,
    ViewPostObservation,
    ViewUserState,
)
from .owner_repository import owner_from_telegram
from .required_channel import check_required_membership, required_channel_config
from .view_metrics import ViewWindows, measured_windows
from .view_pool import analytics_available, lease_account
from .view_private import PrivateChannelError, connect_private, parse_invites
from .view_reports import (
    NetworkMetric,
    ReportPoint,
    ViewReport,
    network_summary_value,
    render_network_png,
    render_report_png,
)

logger = logging.getLogger(__name__)
CHANNEL_PATTERN = re.compile(
    r"(?<![\w/])(?:@([A-Za-z][A-Za-z0-9_]{4,31})|(?:https?://)?(?:t\.me|telegram\.me)/([A-Za-z][A-Za-z0-9_]{4,31})(?:/\d+)?)",
    re.IGNORECASE,
)
POST_PATTERN = re.compile(r"(?:https?://)?(?:t\.me|telegram\.me)/([A-Za-z][A-Za-z0-9_]{4,31})/(\d+)", re.I)
PRIVATE_POST_PATTERN = re.compile(r"(?:https?://)?(?:t\.me|telegram\.me)/c/(\d{5,15})/(\d+)", re.I)
TARGET_PATTERN = re.compile(
    r"(?<![\w/])(?:@(?:[A-Za-z][A-Za-z0-9_]{4,31})|"
    r"(?:https?://)?(?:t\.me|telegram\.me)/(?:\+[A-Za-z0-9_-]{8,128}|"
    r"joinchat/[A-Za-z0-9_-]{8,128}|c/\d{5,15}/\d+|"
    r"[A-Za-z][A-Za-z0-9_]{4,31}(?:/\d+)?))",
    re.I,
)
MAX_CHANNELS = 20
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


def _plural(count: int, one: str, few: str, many: str) -> str:
    return one if count % 10 == 1 and count % 100 != 11 else (
        few if count % 10 in (2, 3, 4) and count % 100 not in (12, 13, 14) else many
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
    return result


def parse_targets(text: str) -> list[str]:
    """Telegram targets in source order, without silently truncating a batch."""
    result: list[str] = []
    seen: set[str] = set()
    for match in TARGET_PATTERN.finditer(text):
        target = match.group()
        public = CHANNEL_PATTERN.fullmatch(target)
        key = (public.group(1) or public.group(2)).lower() if public else target.lower()
        if key not in seen:
            result.append(target)
            seen.add(key)
    return result


def parse_message_targets(message: dict[str, Any]) -> list[str]:
    """Read visible Telegram URLs and hidden text_link URLs (UTF-16 offsets)."""
    text = str(message.get("text") or message.get("caption") or "")
    entities = message.get("entities") if message.get("text") is not None else message.get("caption_entities")
    rich: list[tuple[int, int, str]] = []
    if isinstance(entities, list):
        encoded = text.encode("utf-16-le")
        for entity in entities:
            if not isinstance(entity, dict) or entity.get("type") != "text_link":
                continue
            url = entity.get("url")
            if not isinstance(url, str) or TARGET_PATTERN.fullmatch(url) is None:
                continue
            offset, length = entity.get("offset"), entity.get("length")
            if not isinstance(offset, int) or not isinstance(length, int) or offset < 0 or length < 0:
                continue
            try:
                start = len(encoded[:offset * 2].decode("utf-16-le"))
                end = len(encoded[:(offset + length) * 2].decode("utf-16-le"))
            except UnicodeDecodeError:
                continue
            if end <= len(text):
                rich.append((start, end, url))
    matches: list[tuple[int, str]] = [(start, url) for start, _end, url in rich]
    for match in TARGET_PATTERN.finditer(text):
        if any(match.start() < end and match.end() > start for start, end, _ in rich):
            continue
        if rich and match.group().startswith("@"):
            line = text[text.rfind("\n", 0, match.start()) + 1:text.find("\n", match.end()) if "\n" in text[match.end():] else len(text)]
            if not re.fullmatch(r"\s*(?:\d+[.)]\s*)?@[A-Za-z][A-Za-z0-9_]{4,31}\s*", line):
                continue  # @admin in a forwarded channel preview is not another target.
        matches.append((match.start(), match.group()))
    result: list[str] = []
    seen: set[str] = set()
    for _position, candidate in sorted(matches, key=lambda item: item[0]):
        for target in parse_targets(candidate):
            public = CHANNEL_PATTERN.fullmatch(target)
            key = (public.group(1) or public.group(2)).lower() if public else target.lower()
            if key not in seen:
                result.append(target)
                seen.add(key)
    return result


def _menu() -> InlineKeyboardMarkup:
    hello = get_settings().interface_bot_username.lstrip("@")
    rows = [
        [InlineKeyboardButton(text="Анализ каналов", callback_data="view:analyze"),
         InlineKeyboardButton(text="Статистика картинкой", callback_data="view:picture")],
        [InlineKeyboardButton(text="Избранное", callback_data="view:favorites"),
         InlineKeyboardButton(text="Коллекции", callback_data="view:collections")],
        [InlineKeyboardButton(text="Топ публикаций", callback_data="view:globaltop:views"),
         InlineKeyboardButton(text="Мониторинг", callback_data="view:monitors")],
        [InlineKeyboardButton(text="Упоминания", callback_data="view:mentions")],
        [InlineKeyboardButton(text="Как считаются показатели", callback_data="view:method")],
    ]
    if hello:
        rows.append([InlineKeyboardButton(text="Gramly Hello", url=f"https://t.me/{hello}?start=from_view")])
    return InlineKeyboardMarkup(inline_keyboard=rows)


def _styled_markup(markup: InlineKeyboardMarkup) -> InlineKeyboardMarkup:
    icon_by_action = (
        ("view:net:", "analytics"), ("view:image:", "media"),
        ("view:picture", "media"), ("view:globaltop:", "preview"),
        ("view:top:", "preview"), ("view:monitors", "timer"),
        ("view:monitor:", "timer"), ("view:mention:", "message"),
        ("view:mentions", "message"), ("view:cpm:", "analytics"),
        ("view:history:", "analytics"),
        ("view:refresh:", "rotation"),
        ("view:export:", "copy"), ("view:collection", "copy"),
        ("view:favorites", "success"), ("view:save", "add"),
        ("view:analyze", "analytics"), ("view:method", "help"),
        ("view:check", "success"), ("view:menu", "home"),
    )
    rows = []
    for row in markup.inline_keyboard:
        styled = []
        for button in row:
            key = next(
                (name for prefix, name in icon_by_action if (button.callback_data or "").startswith(prefix)),
                "next" if button.url else "back" if button.text == "Назад" else None,
            )
            icon = DEFAULT_PREMIUM_EMOJI.get(key or "")
            styled.append(button.model_copy(update={"icon_custom_emoji_id": icon}) if icon else button)
        rows.append(styled)
    return InlineKeyboardMarkup(inline_keyboard=rows)


async def _send(chat_id: int, text: str, *, keyboard: InlineKeyboardMarkup | None = None) -> Any:
    # One exit point ensures even error and collection cards retain attribution
    # when forwarded or copied without their inline keyboard.
    markup = _styled_markup(keyboard or InlineKeyboardMarkup(inline_keyboard=[
        [InlineKeyboardButton(text="Меню", callback_data="view:menu")],
    ]))
    emoji_key = (
        "warning" if any(word in text.lower() for word in ("ошибка", "не удалось", "недоступ", "не нашёл"))
        else "analytics" if "просмотр" in text.lower() or "статист" in text.lower()
        else "home" if text.startswith("<b>GramlyView</b>") else "message"
    )
    rich = premium_text("", emoji_key) + attribution(text)
    try:
        return await view_bot().send_message(
            chat_id, rich, parse_mode=ParseMode.HTML, reply_markup=markup,
            link_preview_options=LinkPreviewOptions(is_disabled=True),
        )
    except TelegramBadRequest:
        return await view_bot().send_message(
            chat_id, plain_text(rich), parse_mode=ParseMode.HTML, reply_markup=plain_markup(markup),
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
        "Средние просмотры постов за 24 / 48 / 72 ч · "
        + " / ".join(
            f"{value:,}".replace(",", " ") if value is not None else "нет данных"
            for value in (windows.views_24h, windows.views_48h, windows.views_72h)
        ),
        "Постов за периоды · "
        + " / ".join(str(count) for count in (windows.posts_24h, windows.posts_48h, windows.posts_72h)),
        (f"Найдено постов при последнем сборе · {windows.scanned_posts_count}"
         if windows.observed_at else "Замеры постов · ещё не запускались"),
        "<blockquote>ER24 · "
        + (f"{windows.er24_percent:.2f}%" if windows.er24_percent is not None else "нет данных")
        + ". Текущие просмотры постов, опубликованных за последние сутки, / подписчики. "
        + "Точные просмотры к возрасту 24 / 48 / 72 ч: "
        + " / ".join(
            f"{value:,}".replace(",", " ") if value is not None else "нет данных"
            for value in (windows.exact_views_24h, windows.exact_views_48h, windows.exact_views_72h)
        )
        + ". Замерено: "
        + " / ".join(str(count) for count in (windows.exact_posts_24h, windows.exact_posts_48h, windows.exact_posts_72h))
        + " постов.</blockquote>",
        f"Подписчики обновлены · {observed:%d.%m.%Y %H:%M} UTC",
        "Замеры просмотров · "
        + (f"{windows.observed_at:%d.%m.%Y %H:%M} UTC" if windows.observed_at else "пока нет"),
    ]
    if windows.last_error_code:
        lines.append(f"Последний сбор не завершён · {html.escape(windows.last_error_code)}. Можно повторить обновление.")
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
        [InlineKeyboardButton(text="Обновить", callback_data=f"view:refresh:{channel_id}"),
         InlineKeyboardButton(text="Картинкой", callback_data=f"view:image:{channel_id}")],
        [InlineKeyboardButton(text="Топ публикаций", callback_data=f"view:top:{channel_id}"),
         InlineKeyboardButton(text="CSV", callback_data=f"view:export:{channel_id}")],
        [InlineKeyboardButton(text="Мониторинг", callback_data=f"view:monitor:toggle:{channel_id}"),
         InlineKeyboardButton(text="Динамика", callback_data=f"view:history:{channel_id}")],
    ]
    if public_username:
        rows.append([InlineKeyboardButton(text="Упоминания", callback_data=f"view:mention:toggle:{channel_id}"),
                     InlineKeyboardButton(text="Цена рекламы", callback_data=f"view:cpm:{channel_id}")])
        rows.append([InlineKeyboardButton(text="В избранное", callback_data=f"view:save:{channel_id}:{public_username}"[:64])])
    else:
        rows.append([InlineKeyboardButton(text="В избранное", callback_data=f"view:saveprivate:{channel_id}")])
    rows[-1].append(InlineKeyboardButton(text="Меню", callback_data="view:menu"))
    return InlineKeyboardMarkup(inline_keyboard=rows)


async def _report_for(owner_id: int, channel_id: int) -> ViewReport | None:
    from .models import ViewPrivateChannel
    from .view_mentions import mention_count_7d

    async with session_factory() as session:
        latest = await session.scalar(select(ViewChannelSnapshot).where(
            ViewChannelSnapshot.channel_id == channel_id,
        ).order_by(ViewChannelSnapshot.observed_at.desc()).limit(1))
        if latest is None:
            return None
        if not latest.username:
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
        windows = await measured_windows(session, channel_id, latest.subscriber_count or 0,
                                         now=datetime.now(UTC), include_exact=False)
    if latest.username:
        try:
            current = await view_bot().get_chat(f"@{latest.username}")
        except TelegramAPIError:
            return None
        if current.type != ChatType.CHANNEL or current.id != channel_id:
            return None
    days: dict[str, ReportPoint] = {}
    for snapshot in snapshots:
        if snapshot.subscriber_count is not None:
            days[snapshot.observed_at.date().isoformat()] = ReportPoint(snapshot.observed_at, snapshot.subscriber_count)
    week_ago = datetime.now(UTC) - timedelta(days=7)
    baseline = next(
        (snapshot.subscriber_count for snapshot in reversed(snapshots)
         if snapshot.observed_at <= week_ago and snapshot.subscriber_count is not None),
        None,
    )
    growth = latest.subscriber_count - baseline if latest.subscriber_count is not None and baseline is not None else None
    mentions = await mention_count_7d(owner_id, channel_id) if latest.username else None
    return ViewReport(
        title=latest.title, subscribers=latest.subscriber_count, observed_at=latest.observed_at,
        points=tuple(days.values()), views_24h=windows.views_24h,
        er24=windows.er24_percent, views_48h=windows.views_48h,
        views_72h=windows.views_72h, growth_7d=growth,
        mentions_7d=mentions,
        views_samples_24h=windows.posts_24h,
        views_samples_48h=windows.posts_48h,
        views_samples_72h=windows.posts_72h,
        scanned_posts_count=windows.scanned_posts_count,
        exact_posts_24h=windows.exact_posts_24h,
        exact_posts_48h=windows.exact_posts_48h,
        exact_posts_72h=windows.exact_posts_72h,
        username=latest.username or None,
    )


async def _send_report_image(chat_id: int, owner_id: int, channel_id: int) -> None:
    report = await _report_for(owner_id, channel_id)
    if report is None:
        await _send(chat_id, "Отчёт недоступен или у вас нет доступа к этому каналу.")
        return
    photo = BufferedInputFile(
        render_report_png(report, bot_username=get_settings().view_bot_username.lstrip("@")),
        filename="gramlyview-report.png",
    )
    caption = premium_text("", "analytics") + attribution(
        f"<b>{html.escape(report.title)}</b>\nДанные на {report.observed_at:%d.%m.%Y %H:%M} UTC"
    )
    keyboard = _styled_markup(InlineKeyboardMarkup(inline_keyboard=[
        [InlineKeyboardButton(text="Обновить", callback_data=f"view:refresh:{channel_id}")],
        [InlineKeyboardButton(text="Меню", callback_data="view:menu")],
    ]))
    try:
        await view_bot().send_photo(chat_id, photo, caption=caption, parse_mode=ParseMode.HTML, reply_markup=keyboard)
    except TelegramBadRequest:
        await view_bot().send_photo(
            chat_id, photo, caption=plain_text(caption), parse_mode=ParseMode.HTML,
            reply_markup=plain_markup(keyboard),
        )


_NETWORK_METRICS = ("subscribers", "growth", "views", "er", "mentions")
_NETWORK_LABELS = {
    "subscribers": "Подписчики", "growth": "Рост", "views": "Просмотры",
    "er": "ER", "mentions": "Упоминания",
}


def _network_keyboard(key: str, selected: str, reports: list[ViewReport]) -> InlineKeyboardMarkup:
    buttons = [
        InlineKeyboardButton(
            text=("• " if metric == selected else "")
            + ("Упоминания: нет данных" if metric == "mentions" and all(
                report.mentions_7d is None for report in reports
            ) else label),
            callback_data=f"view:net:{key}:{metric}",
        )
        for metric, label in _NETWORK_LABELS.items()
    ]
    return InlineKeyboardMarkup(inline_keyboard=[buttons[:3], buttons[3:], [
        InlineKeyboardButton(text="Каналы по отдельности", callback_data=f"view:list:{key}:0"),
    ], [
        InlineKeyboardButton(text="Меню", callback_data="view:menu"),
    ]])


async def _network_reports(owner_id: int, ids: list[int]) -> list[ViewReport]:
    slots = asyncio.Semaphore(4)

    async def one(channel_id: int) -> ViewReport | None:
        try:
            async with slots:
                return await _report_for(owner_id, channel_id)
        except TelegramAPIError:
            return None

    return [report for report in await asyncio.gather(*(one(channel_id) for channel_id in ids[:20]))
            if report is not None]


async def _send_network_photo(
    chat_id: int, owner_id: int, ids: list[int], key: str, metric: str,
    *, message_id: int | None = None, reports: list[ViewReport] | None = None,
) -> None:
    reports = reports if reports is not None else await _network_reports(owner_id, ids)
    if not reports:
        await _send(chat_id, "Отчёт больше не доступен. Отправьте ссылки ещё раз.")
        return
    metric = metric if metric in _NETWORK_METRICS else "subscribers"
    caption = attribution(
        f"<b>Статистика сети · {len(reports)} каналов</b>\n"
        f"Показатель: {_NETWORK_LABELS[metric]} · сформировано {datetime.now(UTC):%d.%m.%Y %H:%M} UTC"
        + ("\nПокрытие: отслеживаемые публичные каналы." if metric == "mentions" else "")
    )
    markup = _styled_markup(_network_keyboard(key, metric, reports))
    image_bytes = render_network_png(
        reports, metric=cast(NetworkMetric, metric), bot_username=get_settings().view_bot_username.lstrip("@"),
    )
    if message_id is not None:
        try:
            await view_bot().edit_message_media(
                chat_id=chat_id, message_id=message_id,
                media=InputMediaPhoto(media=BufferedInputFile(image_bytes, filename="gramlyview-network.png"),
                                      caption=caption, parse_mode=ParseMode.HTML),
                reply_markup=markup,
            )
            return
        except TelegramBadRequest:
            try:
                await view_bot().edit_message_media(
                    chat_id=chat_id, message_id=message_id,
                    media=InputMediaPhoto(media=BufferedInputFile(image_bytes, filename="gramlyview-network.png"),
                                          caption=caption, parse_mode=ParseMode.HTML),
                    reply_markup=plain_markup(markup),
                )
                return
            except TelegramBadRequest:
                # Telegram may reject editing an old photo; send the result below.
                pass
    try:
        await view_bot().send_photo(
            chat_id, BufferedInputFile(image_bytes, filename="gramlyview-network.png"),
            caption=caption, parse_mode=ParseMode.HTML, reply_markup=markup,
        )
    except TelegramBadRequest:
        await view_bot().send_photo(
            chat_id, BufferedInputFile(image_bytes, filename="gramlyview-network.png"),
            caption=plain_text(caption), parse_mode=ParseMode.HTML,
            reply_markup=plain_markup(markup),
        )


async def _send_batch_summary(
    chat_id: int, owner_id: int, successful_ids: list[int],
    *, failures: list[str] | None = None, overflow: int = 0,
) -> None:
    ids = list(dict.fromkeys(successful_ids))[:20]
    reports = await _network_reports(owner_id, ids)
    if not reports:
        await _send(chat_id, "Доступ к результатам изменился. Пришлите ссылки снова.")
        return
    key = secrets.token_hex(4)
    async with session_factory() as session, session.begin():
        await session.execute(
            insert(ViewUserState).values(
                owner_id=owner_id, report_channel_ids=ids, report_key=key,
                updated_at=datetime.now(UTC),
            ).on_conflict_do_update(
                index_elements=[ViewUserState.owner_id],
                set_={"report_channel_ids": ids, "report_key": key, "updated_at": datetime.now(UTC)},
            )
        )
    subscribers = [report.subscribers for report in reports if report.subscribers is not None]
    total = f"{sum(subscribers):,}".replace(",", " ") if subscribers else "нет данных"
    lines = [
        f"<b>Сеть · {len(reports)} каналов</b>",
        f"Подписчики · {total} ({len(subscribers)}/{len(reports)} каналов)",
    ]
    for hours in (24, 48, 72):
        measured = [
            (getattr(report, f"views_{hours}h"), getattr(report, f"views_samples_{hours}h"))
            for report in reports
        ]
        valid = [(value, count) for value, count in measured if value is not None and count > 0]
        count = sum(count for _, count in valid)
        average = round(sum(value * count for value, count in valid) / count) if count else None
        shown = f"{average:,}".replace(",", " ") if average is not None else "нет данных"
        lines.append(f"Посты за {hours} ч · {shown} просмотров в среднем · "
                     f"{count} {_plural(count, 'пост', 'поста', 'постов')} / "
                     f"{len(valid)} {_plural(len(valid), 'канал', 'канала', 'каналов')}")
    er = network_summary_value(reports, "er")
    lines.append(f"ER24 · {er:.2f}%" if er is not None else "ER24 · нет данных")
    scanned = sum(report.scanned_posts_count for report in reports)
    lines.append(f"<blockquote>Найдено при сборе: {scanned} постов. Показаны текущие просмотры постов, "
                 "вышедших за указанный период; это не просмотры, набранные за 24/48/72 часа.</blockquote>")
    if failures:
        lines.append(f"Не удалось проверить {len(failures)}: {', '.join(html.escape(item[:35]) for item in failures[:3])}"
                     + (" и другие" if len(failures) > 3 else ""))
    if overflow:
        lines.append(f"Ещё {overflow} {_plural(overflow, 'ссылка', 'ссылки', 'ссылок')} "
                     f"вне лимита {MAX_CHANNELS}; пришлите их отдельно.")
    await _send(
        chat_id, "\n".join(lines),
        keyboard=InlineKeyboardMarkup(inline_keyboard=[
            [InlineKeyboardButton(text="Каналы по отдельности", callback_data=f"view:list:{key}:0")],
            [InlineKeyboardButton(text="Меню", callback_data="view:menu")],
        ]),
    )
    await _send_network_photo(chat_id, owner_id, ids, key, "subscribers", reports=reports)


async def _send_channel_detail(chat_id: int, owner_id: int, channel_id: int) -> None:
    report = await _report_for(owner_id, channel_id)
    if report is None:
        await _send(chat_id, "Отчёт недоступен или изменились права на канал.")
        return
    async with session_factory() as session:
        windows = await measured_windows(session, channel_id, report.subscribers or 0)
    await _send(
        chat_id,
        _channel_card(report.title, report.username, report.subscribers, report.observed_at, windows),
        keyboard=_report_keyboard(channel_id, public_username=report.username),
    )


async def _handle_cpm_text(chat_id: int, owner_id: int, action: str, text: str) -> bool:
    if not action.startswith("cpm:"):
        return False
    try:
        channel_id = int(action[4:])
        cpm = Decimal(text.strip().replace(" ", "").replace(",", "."))
        if not cpm.is_finite() or cpm < 1 or cpm > 1_000_000:
            raise ValueError
    except (InvalidOperation, ValueError):
        await _send(chat_id, "Укажите CPM в рублях за 1 000 просмотров: число от 1 до 1 000 000.")
        return True
    report = await _report_for(owner_id, channel_id)
    await _clear_pending_action(owner_id)
    if report is None or not report.username or report.views_24h is None:
        await _send(chat_id, "Для расчёта пока нет доступных просмотров постов за сутки.")
        return True
    estimate = (Decimal(report.views_24h) * cpm / Decimal(1000)).quantize(Decimal("0.01"))
    await _send(
        chat_id,
        f"<b>Ориентир цены · {html.escape(report.title)}</b>\n"
        f"Средние текущие просмотры постов за 24 ч · {report.views_24h:,}\n"
        f"Ваш CPM · {cpm:g} ₽ / 1 000 просмотров\n"
        f"Расчёт · {estimate:,.2f} ₽".replace(",", " ")
        + f"\n<blockquote>{report.views_samples_24h or 0} постов в выборке. "
          "Это арифметический ориентир, а не рыночная цена или прогноз охвата.</blockquote>",
        keyboard=_report_keyboard(channel_id, public_username=report.username),
    )
    return True


async def _send_network_list(chat_id: int, owner_id: int, ids: list[int], key: str, page: int) -> None:
    page = max(0, min(page, 3))
    rows = []
    for index in range(page * 5, min((page + 1) * 5, len(ids))):
        report = await _report_for(owner_id, ids[index])
        if report is not None:
            rows.append([InlineKeyboardButton(text=report.title[:38], callback_data=f"view:detail:{key}:{index}")])
    navigation = []
    if page:
        navigation.append(InlineKeyboardButton(text="Назад", callback_data=f"view:list:{key}:{page - 1}"))
    if (page + 1) * 5 < len(ids):
        navigation.append(InlineKeyboardButton(text="Далее", callback_data=f"view:list:{key}:{page + 1}"))
    await _send(chat_id, f"<b>Каналы сети</b> · {len(ids)}", keyboard=InlineKeyboardMarkup(inline_keyboard=[
        *rows, *([navigation] if navigation else []),
        [InlineKeyboardButton(text="К картинке", callback_data=f"view:net:{key}:subscribers")],
    ]))


async def _collect_public_if_needed(channel_id: int, username: str, deadline: float) -> None:
    remaining = deadline - asyncio.get_running_loop().time()
    if remaining < 2:
        return
    async def latest_success() -> datetime | None:
        async with session_factory() as session:
            return cast(datetime | None, await session.scalar(select(ViewCollectionRun.observed_at).where(
                ViewCollectionRun.channel_id == channel_id,
                ViewCollectionRun.error_code == "",
            ).order_by(ViewCollectionRun.observed_at.desc()).limit(1)))

    latest = await latest_success()
    if latest is not None and latest >= datetime.now(UTC) - timedelta(minutes=10):
        return
    now = datetime.now(UTC)
    claim_key = secrets.token_hex(8)
    async with session_factory() as session, session.begin():
        acquired = await session.scalar(insert(ViewCollectionClaim).values(
            channel_id=channel_id, claim_key=claim_key, claimed_until=now + timedelta(seconds=30),
        ).on_conflict_do_update(
            index_elements=[ViewCollectionClaim.channel_id],
            set_={"claim_key": claim_key, "claimed_until": now + timedelta(seconds=30)},
            where=ViewCollectionClaim.claimed_until < now,
        ).returning(ViewCollectionClaim.claim_key))
    if acquired != claim_key:
        wait_until = min(deadline, asyncio.get_running_loop().time() + 15)
        while asyncio.get_running_loop().time() < wait_until:
            await asyncio.sleep(1)
            updated = await latest_success()
            if updated is not None and updated != latest:
                return
        return
    from .view_collector import collect_one
    try:
        # The claim is a short DB transaction; no connection is held while Telegram responds.
        async with asyncio.timeout(min(15, deadline - asyncio.get_running_loop().time())):
            async with lease_account() as (_account_id, collector):
                await collect_one(collector, username, channel_id)
    finally:
        async with session_factory() as session, session.begin():
            await session.execute(delete(ViewCollectionClaim).where(
                ViewCollectionClaim.channel_id == channel_id,
                ViewCollectionClaim.claim_key == claim_key,
            ))


async def _monitors(chat_id: int, owner_id: int) -> None:
    async with session_factory() as session:
        rows = (await session.scalars(
            select(ViewMonitor).where(ViewMonitor.owner_id == owner_id, ViewMonitor.enabled.is_(True))
            .order_by(ViewMonitor.created_at.desc()).limit(20)
        )).all()
    if not rows:
        await _send(
            chat_id,
            "<b>Мониторинг</b>\n<blockquote>Откройте карточку канала и включите ежедневную сводку. "
            "Она приходит в 09:00 по Москве, когда появились новые измерения.</blockquote>",
            keyboard=_menu(),
        )
        return
    accessible = []
    for row in rows:
        report = await _report_for(owner_id, row.channel_id)
        if report is not None:
            accessible.append((row, report))
    names = [html.escape(report.title) for _row, report in accessible]
    await _send(
        chat_id,
        "<b>Мониторинг · ежедневная сводка</b>\n" + "\n".join(f"• {name}" for name in names)
        if names else "Активных доступных каналов сейчас нет.",
        keyboard=InlineKeyboardMarkup(inline_keyboard=[
            *[[InlineKeyboardButton(text=f"Открыть {report.title[:24]}", callback_data=f"view:refresh:{row.channel_id}")]
              for row, report in accessible],
            [InlineKeyboardButton(text="Меню", callback_data="view:menu")],
        ]),
    )


async def _toggle_monitor(chat_id: int, owner_id: int, channel_id: int) -> None:
    async with session_factory() as session:
        existing = await session.scalar(select(ViewMonitor).where(
            ViewMonitor.owner_id == owner_id, ViewMonitor.channel_id == channel_id,
        ))
    if existing is None or not existing.enabled:
        report = await _report_for(owner_id, channel_id)
        if report is None:
            await _send(chat_id, "Нет доступа к каналу. Мониторинг не включён.")
            return
    async with session_factory() as session, session.begin():
        monitor = await session.scalar(select(ViewMonitor).where(
            ViewMonitor.owner_id == owner_id, ViewMonitor.channel_id == channel_id,
        ).with_for_update())
        if monitor is None:
            session.add(ViewMonitor(owner_id=owner_id, channel_id=channel_id, enabled=True))
            enabled = True
        else:
            monitor.enabled = not monitor.enabled
            enabled = monitor.enabled
    await _send(
        chat_id,
        "Ежедневная сводка включена. Она придёт в 09:00 по Москве после новых измерений."
        if enabled else "Ежедневная сводка отключена.",
        keyboard=InlineKeyboardMarkup(inline_keyboard=[
            [InlineKeyboardButton(text="Открыть мониторинг", callback_data="view:monitors")],
            [InlineKeyboardButton(text="Меню", callback_data="view:menu")],
        ]),
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
    document = BufferedInputFile(output.getvalue().encode("utf-8-sig"), filename="gramlyview-subscribers.csv")
    caption = premium_text("", "analytics") + attribution(f"История подписчиков · {html.escape(report.title)}")
    keyboard = _styled_markup(InlineKeyboardMarkup(inline_keyboard=[
        [InlineKeyboardButton(text="Обновить", callback_data=f"view:refresh:{channel_id}")],
        [InlineKeyboardButton(text="Меню", callback_data="view:menu")],
    ]))
    try:
        await view_bot().send_document(
            chat_id, document, caption=caption, parse_mode=ParseMode.HTML, reply_markup=keyboard,
        )
    except TelegramBadRequest:
        await view_bot().send_document(
            chat_id, document, caption=plain_text(caption), parse_mode=ParseMode.HTML,
            reply_markup=plain_markup(keyboard),
        )


async def _send_top_posts(chat_id: int, owner_id: int, channel_id: int, metric: str = "views") -> None:
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
    metric = metric if metric in _TOP_FIELDS else "views"
    top = _rank_posts(list(latest.values()), metric)[:5]
    if not top:
        await _send(chat_id, "За последние семь дней измеренных публикаций пока нет.")
        return
    label, field = _TOP_FIELDS[metric]
    lines = [f"<b>Топ публикаций · {html.escape(report.title)}</b>",
             f"Сортировка: {label.lower()} · измеренные посты за 7 дней."]
    for index, row in enumerate(top, 1):
        post = (f'<a href="https://t.me/{report.username}/{row.post_id}">Пост #{row.post_id}</a>'
                if report.username else f"Пост #{row.post_id}")
        lines.append(f"{index}. {post} · {getattr(row, field):,} · {row.published_at:%d.%m %H:%M} UTC".replace(",", " "))
    sort = [InlineKeyboardButton(text=("• " if name == metric else "") + title,
                               callback_data=f"view:top:{channel_id}:{name}")
            for name, (title, _field) in _TOP_FIELDS.items()]
    await _send(chat_id, "\n".join(lines), keyboard=InlineKeyboardMarkup(inline_keyboard=[
        sort[:2], sort[2:],
        [InlineKeyboardButton(text="Обновить", callback_data=f"view:refresh:{channel_id}")],
        [InlineKeyboardButton(text="Меню", callback_data="view:menu")],
    ]))


async def _send_history(chat_id: int, owner_id: int, channel_id: int) -> None:
    report = await _report_for(owner_id, channel_id)
    if report is None:
        await _send(chat_id, "История канала недоступна.")
        return
    points = report.points[-30:]
    lines = [f"<b>Подписчики · {html.escape(report.title)}</b>"]
    if not points:
        lines.append("История начнётся с первого замера GramlyView.")
    else:
        previous: int | None = None
        for point in points:
            delta = point.subscribers - previous if previous is not None else None
            change = f" · {delta:+,}".replace(",", " ") if delta is not None else " · первый замер"
            lines.append(f"{point.day:%d.%m} · {point.subscribers:,}{change}".replace(",", " "))
            previous = point.subscribers
    lines.append("<blockquote>Только сохранённые замеры; прошлые значения не восстанавливаем.</blockquote>")
    await _send(chat_id, "\n".join(lines), keyboard=_report_keyboard(channel_id, public_username=report.username))


_TOP_FIELDS = {
    "views": ("Просмотры", "views"),
    "forwards": ("Пересылки", "forwards"),
    "comments": ("Комментарии", "replies_count"),
    "reactions": ("Реакции", "reactions_count"),
}


def _rank_posts(observations: Sequence[ViewPostObservation], metric: str) -> list[ViewPostObservation]:
    latest: dict[tuple[int, int], ViewPostObservation] = {}
    for observation in observations:
        latest.setdefault((observation.channel_id, observation.post_id), observation)
    field = _TOP_FIELDS[metric][1]
    return sorted(
        (row for row in latest.values() if getattr(row, field) is not None),
        key=lambda row: (getattr(row, field), row.published_at), reverse=True,
    )


async def _global_top_posts(chat_id: int, owner_id: int, metric: str, page: int = 0) -> None:
    if metric not in _TOP_FIELDS:
        metric = "views"
    page = max(0, min(page, 99))
    async with session_factory() as session:
        favorite_ids = (await session.scalars(select(ViewFavorite.channel_id).where(
            ViewFavorite.owner_id == owner_id,
        ))).all()
        collection_ids = (await session.scalars(
            select(ViewCollectionItem.channel_id).join(ViewCollection).where(ViewCollection.owner_id == owner_id)
        )).all()
        state = await session.get(ViewUserState, owner_id)
        ids = list(dict.fromkeys([*favorite_ids, *collection_ids, *((state.report_channel_ids or []) if state else [])]))[:20]
    reports: dict[int, ViewReport] = {}
    for channel_id in ids:
        report = await _report_for(owner_id, channel_id)
        if report is not None:
            reports[channel_id] = report
    if not reports:
        await _send(chat_id, "Для топа сначала проанализируйте каналы или добавьте их в избранное.", keyboard=_menu())
        return
    async with session_factory() as session:
        observations = (await session.scalars(
            select(ViewPostObservation).where(
                ViewPostObservation.channel_id.in_(reports),
                ViewPostObservation.published_at >= datetime.now(UTC) - timedelta(days=7),
            ).order_by(ViewPostObservation.observed_at.desc()).limit(10_000)
        )).all()
    label, field = _TOP_FIELDS[metric]
    ranked = _rank_posts(observations, metric)
    if not ranked:
        await _send(chat_id, f"<b>Топ · {label.lower()}</b>\nЗамеров за семь дней пока нет.", keyboard=_menu())
        return
    start = page * 5
    if start >= len(ranked):
        page = 0
        start = 0
    lines = [f"<b>Топ публикаций · {label.lower()}</b>",
             f"Среди измеренных постов {len(reports)} каналов за 7 дней."]
    for index, row in enumerate(ranked[start:start + 5], start + 1):
        report = reports[row.channel_id]
        value = f"{getattr(row, field):,}".replace(",", " ")
        if report.username:
            url = f"https://t.me/{report.username}/{row.post_id}"
        else:
            internal = str(abs(row.channel_id)).removeprefix("100")
            url = f"https://t.me/c/{internal}/{row.post_id}"
        lines.append(
            f'{index}. {html.escape(report.title[:42])} · '
            f'<a href="{html.escape(url, quote=True)}">пост #{row.post_id}</a> · {value}'
        )
    sort_buttons = [InlineKeyboardButton(
        text=("• " if name == metric else "") + title,
        callback_data=f"view:globaltop:{name}:0",
    ) for name, (title, _field) in _TOP_FIELDS.items()]
    page_buttons = []
    if page:
        page_buttons.append(InlineKeyboardButton(text="Назад", callback_data=f"view:globaltop:{metric}:{page - 1}"))
    if start + 5 < len(ranked):
        page_buttons.append(InlineKeyboardButton(text="Далее", callback_data=f"view:globaltop:{metric}:{page + 1}"))
    await _send(chat_id, "\n".join(lines), keyboard=InlineKeyboardMarkup(inline_keyboard=[
        sort_buttons[:2], sort_buttons[2:], *([page_buttons] if page_buttons else []),
        [InlineKeyboardButton(text="Меню", callback_data="view:menu")],
    ]))


async def _analyze(chat_id: int, owner_id: int, query: str) -> list[int]:
    targets = parse_targets(query)
    if not targets:
        await _send(
            chat_id,
            "Не нашёл ссылку на канал. Пришлите @username, t.me/канал или приглашение t.me/+…",
        )
        return []
    if not await analytics_available():
        await _send(chat_id, "Аналитика временно приостановлена: доступно менее трёх рабочих аккаунтов сбора. Сохранённые отчёты остаются доступными. Попробуйте позже.")
        return []
    if not await _reserve_analysis(owner_id):
        await _send(chat_id, "Слишком частые запросы. Повторите анализ через 10 секунд.")
        return []
    overflow = max(0, len(targets) - MAX_CHANNELS)
    targets = targets[:MAX_CHANNELS]
    batch = len(targets) > 1
    progress = None
    if len(targets) > 1:
        progress = await _send(chat_id, f"Проверяю {len(targets)} каналов. Затем покажу сводку и картинку сети.")
    seen_channel_ids: set[int] = set()
    successful_ids: list[int] = []
    failures: list[str] = []
    succeeded = failed = 0
    fresh_deadline = asyncio.get_running_loop().time() + (90 if batch else 30)
    for index, target in enumerate(targets, 1):
        target_label = "Закрытый канал" if parse_invites(target) else html.escape(target)
        try:
            private_post = PRIVATE_POST_PATTERN.fullmatch(target)
            invites = parse_invites(target)
            if private_post:
                channel_id = -int(f"100{private_post.group(1)}")
                if channel_id not in seen_channel_ids:
                    if await _analyze_private(chat_id, owner_id, channel_id,
                                              post_id=int(private_post.group(2)), send_card=not batch):
                        seen_channel_ids.add(channel_id)
                        successful_ids.append(channel_id)
                        succeeded += 1
                    else:
                        failed += 1
                        failures.append(target_label)
            elif invites:
                channel_id, title, count, observed = await asyncio.wait_for(
                    connect_private(owner_id, invites[0]), timeout=30 if len(targets) > 1 else 180,
                )
                if channel_id not in seen_channel_ids:
                    async with session_factory() as session:
                        windows = await measured_windows(session, channel_id, count or 0, now=datetime.now(UTC))
                    if not batch:
                        await _send(chat_id, _channel_card(title, None, count, observed, windows), keyboard=_report_keyboard(channel_id))
                    seen_channel_ids.add(channel_id)
                    successful_ids.append(channel_id)
                    succeeded += 1
            else:
                usernames = parse_channels(target)
                if not usernames:
                    continue
                username = usernames[0]
                channel_id, canonical, title, count, observed = await _public_channel_data(username)
                if channel_id in seen_channel_ids:
                    continue
                post = POST_PATTERN.fullmatch(target)
                post_id = int(post.group(2)) if post else None
                try:
                    await _collect_public_if_needed(channel_id, canonical, fresh_deadline)
                except Exception as exc:
                    logger.info("View immediate collection deferred channel_id=%s class=%s", channel_id, type(exc).__name__)
                async with session_factory() as session:
                    windows = await measured_windows(session, channel_id, count or 0, now=datetime.now(UTC))
                if not batch:
                    await _send(
                        chat_id,
                        _channel_card(title, canonical, count, observed, windows, post_id=post_id,
                                      post_views=await _post_views(channel_id, post_id)),
                        keyboard=_report_keyboard(channel_id, public_username=canonical),
                    )
                seen_channel_ids.add(channel_id)
                successful_ids.append(channel_id)
                succeeded += 1
        except ValueError as exc:
            failed += 1
            failures.append(target_label)
            reason = str(exc) if isinstance(exc, PrivateChannelError) else "Это не канал"
            if not batch:
                await _send(chat_id, f"{target_label} · {html.escape(reason)}")
        except TelegramAPIError:
            failed += 1
            failures.append(target_label)
            if not batch:
                await _send(chat_id, f"{target_label} · не удалось получить данные. Проверьте ссылку и доступ к каналу.")
        except Exception as exc:
            failed += 1
            failures.append(target_label)
            logger.error("GramlyView analysis failed owner_id=%s class=%s", owner_id, type(exc).__name__)
            if not batch:
                await _send(chat_id, f"{target_label} · не удалось проанализировать. Попробуйте позже.")
        if isinstance(progress, Message) and index % 5 == 0 and index < len(targets):
            try:
                await view_bot().edit_message_text(
                    attribution(f"Проверено {index} из {len(targets)} каналов."),
                    chat_id=chat_id, message_id=progress.message_id, parse_mode=ParseMode.HTML,
                    link_preview_options=LinkPreviewOptions(is_disabled=True),
                )
            except TelegramAPIError:
                pass
    if (len(targets) > 1 or overflow) and len(successful_ids) < 2:
        summary = f"Готово: {succeeded} каналов, ошибок: {failed}."
        if overflow:
            suffix = (
                "ссылка" if overflow % 10 == 1 and overflow % 100 != 11
                else "ссылки" if overflow % 10 in (2, 3, 4) and overflow % 100 not in (12, 13, 14)
                else "ссылок"
            )
            summary += f" Ещё {overflow} {suffix} вне лимита: за один раз можно проверить {MAX_CHANNELS}. Пришлите оставшиеся отдельно."
        await _send(chat_id, summary)
    if len(successful_ids) == 1 and batch:
        await _send_channel_detail(chat_id, owner_id, successful_ids[0])
    if len(successful_ids) > 1:
        try:
            await _send_batch_summary(chat_id, owner_id, successful_ids,
                                      failures=failures, overflow=overflow)
        except Exception as exc:
            logger.error("GramlyView network summary failed owner_id=%s class=%s", owner_id, type(exc).__name__)
            await _send(chat_id, "Сводную картинку пока не удалось создать. Повторите позже.")
    if isinstance(progress, Message):
        try:
            await view_bot().delete_message(chat_id, progress.message_id)
        except TelegramAPIError:
            pass
    return successful_ids


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


async def _analyze_private(chat_id: int, owner_id: int, channel_id: int, *, post_id: int | None = None,
                           send_card: bool = True) -> bool:
    from .models import ViewPrivateChannel

    if not await analytics_available():
        if send_card:
            await _send(chat_id, "Аналитика временно приостановлена: рабочих аккаунтов меньше трёх.")
        return False
    async with session_factory() as session:
        linked = await session.scalar(select(ViewPrivateChannel).where(
            ViewPrivateChannel.owner_id == owner_id,
            ViewPrivateChannel.channel_id == channel_id,
            ViewPrivateChannel.status == "active",
        ))
    if linked is None:
        if send_card:
            await _send(chat_id, "Для анализа закрытого канала пришлите его ссылку-приглашение t.me/+…")
        return False
    from .view_private import analyze_saved_private
    try:
        title, count, observed = await analyze_saved_private(linked.account_id, channel_id)
        async with session_factory() as session:
            windows = await measured_windows(session, channel_id, count or 0, now=datetime.now(UTC))
        if send_card:
            await _send(chat_id, _channel_card(title, None, count, observed, windows, post_id=post_id,
                                              post_views=await _post_views(channel_id, post_id)), keyboard=_report_keyboard(channel_id))
        return True
    except PrivateChannelError:
        if send_card:
            await _send(chat_id, "Аккаунт сбора потерял доступ к каналу. Отправьте новое приглашение.")
    except Exception as exc:
        logger.error("View saved private analysis failed channel_id=%s class=%s", channel_id, type(exc).__name__)
        if send_card:
            await _send(chat_id, "Не удалось обновить закрытый канал. Повторите позже.")
    return False


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
        await _send(chat_id, "<b>Избранное пока пусто</b>\n<blockquote>Отправьте ссылку на канал и нажмите «В избранное» под отчётом.</blockquote>", keyboard=_menu())
        return
    body = "<b>Избранные каналы</b>\n" + "\n".join(
        (f'• <a href="https://t.me/{row.username}">{html.escape(row.title)}</a>'
         if row.username else f"• {html.escape(row.title)} · закрытый")
        for row in rows
    )
    keyboard = InlineKeyboardMarkup(inline_keyboard=[
        *[[InlineKeyboardButton(text=f"Открыть {row.title[:28]}", callback_data=f"view:refresh:{row.channel_id}")]
          for row in rows],
        [InlineKeyboardButton(text="Меню", callback_data="view:menu")],
    ])
    await _send(chat_id, body, keyboard=keyboard)


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
            [InlineKeyboardButton(text="Меню", callback_data="view:menu")],
        ]
    )
    await _send(
        chat_id,
        "<b>Коллекции</b>\nВыберите коллекцию или создайте новую."
        if rows
        else "<b>Коллекций пока нет</b>\n<blockquote>Создайте коллекцию и добавьте каналы, которые хотите проверять вместе.</blockquote>",
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
                    text="Анализировать все", callback_data=f"view:collection:analyze:{collection_id}"
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


async def _clear_pending_action(owner_id: int) -> None:
    async with session_factory() as session, session.begin():
        state = await session.get(ViewUserState, owner_id)
        if state:
            state.pending_action = ""


async def _handle_collection_text(chat_id: int, owner_id: int, text: str) -> bool:
    async with session_factory() as session:
        state = await session.get(ViewUserState, owner_id)
        action = state.pending_action if state else ""
    if not action:
        return False
    if action == "collection_name":
        name = " ".join(text.strip().split())[:80]
        if parse_channels(text) or parse_invites(text) or re.search(r"(?:https?://|t\.me/|telegram\.me/|@\w)", text, re.I):
            await _send(chat_id, "Это похоже на ссылку или @канал. Отправьте название коллекции без ссылки либо вернитесь в меню.")
            return True
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
                await _send(chat_id, "Аналитика временно приостановлена: рабочих аккаунтов меньше трёх.")
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
    targets = parse_message_targets(message) if isinstance(message, dict) else []
    query = "\n".join(targets) if targets else text
    forwarded = message.get("forward_origin") if message else None
    private_origin_id = 0
    if isinstance(forwarded, dict):
        origin = forwarded.get("chat") or {}
        username = origin.get("username")
        if username:
            query = f"@{username}" if not query else f"@{username}\n{query}"
        elif forwarded.get("type") == "channel":
            private_origin_id = int(origin.get("id") or 0)
    activity_event = (
        "start_from_hello"
        if text.startswith("/start from_hello")
        else "start"
        if text.startswith("/start")
        else "analyze"
        if query or private_origin_id
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
        # Every navigation action leaves the previous text-entry screen.
        await _clear_pending_action(owner_id)
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
    pending_query = f"private:{private_origin_id}" if private_origin_id else query
    if not await _gate(chat_id, owner_id, pending_query if not pending_query.startswith("/start") else ""):
        return
    if data == "view:favorites":
        await _favorites(chat_id, owner_id)
    elif data == "view:mentions":
        from .view_mentions import list_mention_monitors

        monitors = await list_mention_monitors(owner_id)
        if not monitors:
            await _send(chat_id, "<b>Упоминания</b>\nВ карточке публичного канала включите «Упоминания». "
                        "Проверяем только каналы, уже отслеживаемые GramlyView; весь Telegram не охватываем.",
                        keyboard=_menu())
        else:
            lines = ["<b>Упоминания · включены</b>"]
            rows = []
            for monitor in monitors:
                lines.append(f"@{html.escape(monitor.username)}")
                rows.append([InlineKeyboardButton(text=f"Выключить @{monitor.username}",
                                                callback_data=f"view:mention:toggle:{monitor.channel_id}")])
            lines.append("<blockquote>Новые ссылки ищем в отслеживаемых публичных каналах после очередного сбора.</blockquote>")
            await _send(chat_id, "\n".join(lines), keyboard=InlineKeyboardMarkup(inline_keyboard=[
                *rows, [InlineKeyboardButton(text="Меню", callback_data="view:menu")],
            ]))
    elif data.startswith("view:mention:toggle:"):
        from .models import ViewMentionMonitor
        from .view_mentions import subscribe_mentions, unsubscribe_mentions

        try:
            channel_id = int(data.rsplit(":", 1)[1])
        except ValueError:
            await _send(chat_id, "Канал не найден.")
            return
        report = await _report_for(owner_id, channel_id)
        if report is None or not report.username:
            await _send(chat_id, "Для упоминаний нужен доступный публичный канал.")
            return
        async with session_factory() as session:
            active = await session.scalar(select(ViewMentionMonitor.id).where(
                ViewMentionMonitor.owner_id == owner_id,
                ViewMentionMonitor.channel_id == channel_id,
                ViewMentionMonitor.enabled.is_(True),
            ))
        if active is None:
            try:
                await subscribe_mentions(owner_id, channel_id, report.username)
            except ValueError as exc:
                await _send(chat_id, html.escape(str(exc)))
            else:
                await _send(chat_id, f"Упоминания @{html.escape(report.username)} включены. "
                            "Покрытие ограничено отслеживаемыми публичными каналами.")
        else:
            await unsubscribe_mentions(owner_id, channel_id)
            await _send(chat_id, f"Упоминания @{html.escape(report.username)} выключены.")
    elif data.startswith("view:cpm:"):
        try:
            channel_id = int(data.rsplit(":", 1)[1])
        except ValueError:
            await _send(chat_id, "Канал не найден.")
            return
        report = await _report_for(owner_id, channel_id)
        if report is None or not report.username:
            await _send(chat_id, "Расчёт недоступен для этого канала.")
            return
        await _set_pending_action(owner_id, f"cpm:{channel_id}")
        await _send(chat_id, "Введите желаемый CPM в рублях за 1 000 просмотров. "
                    "Покажу расчёт по измеренным постам за последние сутки.", keyboard=InlineKeyboardMarkup(
                        inline_keyboard=[[InlineKeyboardButton(text="Назад", callback_data="view:menu")]],
                    ))
    elif data == "view:monitors":
        await _monitors(chat_id, owner_id)
    elif data.startswith("view:monitor:toggle:"):
        try:
            channel_id = int(data.rsplit(":", 1)[1])
        except ValueError:
            await _send(chat_id, "Канал не найден.")
            return
        await _toggle_monitor(chat_id, owner_id, channel_id)
    elif data == "view:analyze":
        await _send(
            chat_id,
            "Отправьте ссылку на канал, публикацию или пересланный список. За раз обработаем до 20 каналов.",
            keyboard=InlineKeyboardMarkup(inline_keyboard=[
                [InlineKeyboardButton(text="Назад", callback_data="view:menu")],
            ]),
        )
    elif data == "view:picture":
        await _set_pending_action(owner_id, "network_picture")
        await _send(
            chat_id,
            "<b>Статистика картинкой</b>\nОтправьте одну или несколько ссылок на каналы. "
            "После анализа появится картинка с переключением показателей.",
            keyboard=InlineKeyboardMarkup(inline_keyboard=[
                [InlineKeyboardButton(text="Назад", callback_data="view:menu")],
            ]),
        )
    elif data.startswith("view:net:"):
        parts = data.split(":")
        if len(parts) != 4 or parts[3] not in _NETWORK_METRICS:
            await _send(chat_id, "Показатель не найден.")
            return
        async with session_factory() as session:
            state = await session.get(ViewUserState, owner_id)
            ids = list(state.report_channel_ids or []) if state and state.report_key == parts[2] else []
        if not ids:
            await _send(chat_id, "Это старая сводка. Отправьте список каналов ещё раз.")
            return
        source_message = callback.get("message") if isinstance(callback, dict) else None
        photo_message_id = (
            int(source_message["message_id"])
            if isinstance(source_message, dict) and source_message.get("photo") else None
        )
        await _send_network_photo(chat_id, owner_id, ids, parts[2], parts[3], message_id=photo_message_id)
    elif data.startswith("view:list:") or data.startswith("view:detail:"):
        parts = data.split(":")
        if len(parts) != 4 or not parts[3].isdigit():
            await _send(chat_id, "Сводка не найдена.")
            return
        async with session_factory() as session:
            state = await session.get(ViewUserState, owner_id)
            ids = list(state.report_channel_ids or []) if state and state.report_key == parts[2] else []
        index = int(parts[3])
        if not ids or index < 0:
            await _send(chat_id, "Это старая сводка. Пришлите ссылки ещё раз.")
        elif parts[1] == "detail" and index < len(ids):
            await _send_channel_detail(chat_id, owner_id, ids[index])
        elif parts[1] == "list":
            await _send_network_list(chat_id, owner_id, ids, parts[2], index)
        else:
            await _send(chat_id, "Канал не найден.")
    elif data.startswith("view:refresh:"):
        try:
            channel_id = int(data.rsplit(":", 1)[1])
        except ValueError:
            await _send(chat_id, "Отчёт не найден.")
            return
        report = await _report_for(owner_id, channel_id)
        if report is None:
            await _send(chat_id, "Отчёт недоступен или у вас нет доступа к этому каналу.")
            return
        async with session_factory() as session:
            latest = await session.scalar(select(ViewChannelSnapshot).where(
                ViewChannelSnapshot.channel_id == channel_id,
            ).order_by(ViewChannelSnapshot.observed_at.desc()).limit(1))
        if latest is not None and latest.username:
            await _analyze(chat_id, owner_id, f"@{latest.username}")
        elif await _reserve_analysis(owner_id):
            await _analyze_private(chat_id, owner_id, channel_id)
        else:
            await _send(chat_id, "Слишком частые запросы. Повторите анализ через 10 секунд.")
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
            parts = data.split(":")
            await _send_top_posts(chat_id, owner_id, int(parts[2]), parts[3] if len(parts) > 3 else "views")
        except ValueError:
            await _send(chat_id, "Публикации не найдены.")
    elif data.startswith("view:history:"):
        try:
            await _send_history(chat_id, owner_id, int(data.rsplit(":", 1)[1]))
        except ValueError:
            await _send(chat_id, "История не найдена.")
    elif data.startswith("view:globaltop:"):
        parts = data.split(":")
        try:
            page = int(parts[3]) if len(parts) > 3 else 0
        except ValueError:
            page = 0
        await _global_top_posts(chat_id, owner_id, parts[2] if len(parts) > 2 else "views", page)
    elif data == "view:collections":
        await _collections(chat_id, owner_id)
    elif data == "view:collection:new":
        await _set_pending_action(owner_id, "collection_name")
        await _send(
            chat_id,
            "Как назвать новую коллекцию? Отправьте короткое название одним сообщением.",
            keyboard=InlineKeyboardMarkup(inline_keyboard=[
                [InlineKeyboardButton(text="Назад", callback_data="view:collections")],
            ]),
        )
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
        await _send(
            chat_id,
            "Отправьте одну ссылку на канал, включая закрытое приглашение, или его @username.",
            keyboard=InlineKeyboardMarkup(inline_keyboard=[
                [InlineKeyboardButton(text="Назад", callback_data=f"view:collection:{collection_id}")],
            ]),
        )
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
            "<b>Как считаются показатели</b>\n"
            "Подписчики — текущий счётчик Telegram. Основные просмотры за 24/48/72 часа — "
            "средние <b>текущие</b> просмотры постов, опубликованных за соответствующий период. "
            "ER24 — среднее по постам за сутки, делённое на число подписчиков.\n"
            "<blockquote>Точные просмотры к возрасту поста 24/48/72 часа — отдельные замеры. "
            "Их прошлые значения нельзя восстановить; без замеров пишем «нет данных».</blockquote>",
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
            "<b>GramlyView</b>\nАналитика Telegram-каналов.\n<blockquote>Отправьте @username, ссылку на пост, приглашение в закрытый канал или список ссылок. Для сети покажем одну сводку и картинку.</blockquote>",
            keyboard=_menu(),
        )
        if text.startswith("/start"):
            await _maybe_offer_hello(chat_id, owner_id)
    elif private_origin_id:
        await _analyze_private(chat_id, owner_id, private_origin_id)
    elif query:
        async with session_factory() as session:
            state = await session.get(ViewUserState, owner_id)
            picture_requested = bool(state and state.pending_action == "network_picture")
            pending_action = state.pending_action if state else ""
        if await _handle_cpm_text(chat_id, owner_id, pending_action, query):
            return
        if picture_requested:
            await _clear_pending_action(owner_id)
            successful_ids = await _analyze(chat_id, owner_id, query)
            if len(successful_ids) == 1:
                await _send_report_image(chat_id, owner_id, successful_ids[0])
        elif not await _handle_collection_text(chat_id, owner_id, query):
            await _analyze(chat_id, owner_id, query)
    else:
        await _send(
            chat_id, "Перешлите публикацию или отправьте ссылку на публичный либо закрытый канал.", keyboard=_menu()
        )
