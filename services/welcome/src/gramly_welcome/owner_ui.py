"""One persistent control message, independent of the owner's business FSM.

The existing owner-update lock serializes these sessions across workers. Callback
tokens reference a particular rendered keyboard, so old buttons cannot replay a
mutation. Real Telegram previews are temporary, separately tracked messages.
"""

from __future__ import annotations

import html
import json
import logging
import re
import uuid
from collections.abc import Awaitable, Callable
from contextvars import ContextVar
from html.parser import HTMLParser
from typing import Any, cast

from aiogram import BaseMiddleware, Bot
from aiogram.exceptions import TelegramBadRequest
from aiogram.types import CallbackQuery, InlineKeyboardMarkup, Message, TelegramObject

from .bot_ui import answer_with_ui_fallback, inline_button, plain_markup, plain_text, premium_text

logger = logging.getLogger(__name__)
_current: ContextVar[OwnerUiSession | None] = ContextVar("owner_ui", default=None)


def _navigation(raw: str) -> bool:
    return (
        re.fullmatch(
            r"(?:menu:(?:home|bots|subscription|referrals|analytics)|help:[a-z_-]+|"
            r"(?:bots|bot|guide|requests|stats|show-wdelay|approval-delay|rotation|connect|connect-check|msg|farewell):\d+|"
            r"chain:editor:\d+|chain:step:\d+:\d+|chain:assign:\d+(?::\d+)?)",
            raw,
        )
        is not None
    )


def _legacy_navigation(raw: str) -> bool:
    # Opening a draft/finishing onboarding has application side effects, so old
    # keyboards only get these read-only entry points. Current buttons are signed.
    return _navigation(raw) and not raw.startswith(("msg:", "farewell:", "guide:", "chain:"))


class _ScreenPages(HTMLParser):
    """Split rendered HTML without truncating copy or breaking open entities."""

    def __init__(self, budget: int) -> None:
        super().__init__(convert_charrefs=True)
        self.budget = budget
        self.units = 0
        self.parts: list[str] = []
        self.tags: list[tuple[str, str]] = []
        self.pages: list[str] = []

    def flush(self) -> None:
        if not self.units:
            return
        self.pages.append("".join(self.parts) + "".join(f"</{name}>" for name, _ in reversed(self.tags)))
        self.parts = [opening for _, opening in self.tags]
        self.units = 0

    def handle_starttag(self, tag: str, attrs: list[tuple[str, str | None]]) -> None:
        if tag == "tg-emoji" and self.units > self.budget - 32:
            self.flush()
        opening = self.get_starttag_text() or f"<{tag}>"
        self.tags.append((tag, opening))
        self.parts.append(opening)

    def handle_endtag(self, tag: str) -> None:
        self.parts.append(f"</{tag}>")
        if self.tags and self.tags[-1][0] == tag:
            self.tags.pop()

    def handle_data(self, data: str) -> None:
        for character in data:
            units = len(character.encode("utf-16-le")) // 2
            if self.units + units > self.budget:
                self.flush()
            self.parts.append(html.escape(character, quote=False))
            self.units += units


def screen_pages(text: str) -> list[str]:
    parser = _ScreenPages(3500)
    parser.feed(text)
    parser.close()
    parser.flush()
    return parser.pages or [text]


class OwnerUiSession:
    def __init__(self, bot: Bot, redis: Any, chat_id: int, user_id: int) -> None:
        self.bot, self.redis, self.chat_id = bot, redis, chat_id
        self.key = f"welcome:owner-ui:{bot.id}:{chat_id}:{user_id}"
        self.data: dict[str, Any] = {}
        self.back_route = "menu:home"
        self.notices: list[str] = []

    async def load(self) -> None:
        raw = await self.redis.get(self.key)
        self.data = json.loads(raw) if raw else {}

    async def save(self) -> None:
        await self.redis.set(self.key, json.dumps(self.data), ex=90 * 86400)

    def navigate(self, route: str, *, back: bool = False) -> None:
        current = str(self.data.get("route") or "menu:home")
        history = list(self.data.get("history") or [])
        if route == "menu:home":
            history = []
        elif back or route in history:
            if route in history:
                history = history[: history.index(route)]
        elif _navigation(route) and route != current:
            history = [*history, current][-12:]
        self.back_route = (history[-1] if history else "menu:home") if _navigation(route) else current
        if _navigation(route):
            self.data.update(route=route, history=history)

    async def cleanup_preview(self) -> None:
        remaining = []
        for message_id in self.data.get("preview_ids", []):
            if message_id == self.data.get("message_id"):
                continue
            try:
                await self.bot.delete_message(self.chat_id, message_id)
            except TelegramBadRequest:
                # Already removed or Telegram's deletion window has elapsed.
                continue
            except Exception:
                remaining.append(message_id)
                logger.warning("Temporary owner preview cleanup deferred")
        self.data["preview_ids"] = remaining
        await self.save()

    async def track_preview(self, message_ids: tuple[int, ...]) -> None:
        self.data["preview_ids"] = list(dict.fromkeys([*self.data.get("preview_ids", []), *message_ids]))
        await self.save()

    async def render(self, text: str, markup: InlineKeyboardMarkup | None) -> None:
        if markup is None:
            self.notices.append(text)
            # Error/input feedback keeps the current actionable controls.
            raw_markup = self.data.get("markup")
            markup = InlineKeyboardMarkup.model_validate(raw_markup) if raw_markup else None
        elif self.notices:
            text = "\n\n".join([*self.notices, text])
            self.notices.clear()
        if "<tg-emoji" not in text:
            text = premium_text(text, "message")
        rows = [list(row) for row in markup.inline_keyboard] if markup else []
        if not any(
            button.callback_data
            and (
                button.callback_data.startswith("$back:")
                or any(
                    word in button.text.lower()
                    for word in ("назад", "отмена", "к боту", "к цепоч", "к списку")
                )
            )
            for row in rows
            for button in row
        ):
            rows.append([inline_button("Назад", callback_data=f"$back:{self.back_route}", emoji_key="back")])
        raw_markup = InlineKeyboardMarkup(inline_keyboard=rows)
        self.data.update(pages=screen_pages(text), markup=raw_markup.model_dump(exclude_none=True))
        await self.display_page(0)

    async def display_page(self, index: int) -> None:
        pages = list(self.data.get("pages") or ["Откройте меню командой /start."])
        index = max(0, min(index, len(pages) - 1))
        text = pages[index]
        raw_markup = InlineKeyboardMarkup.model_validate(self.data.get("markup") or {"inline_keyboard": []})
        rows = [list(row) for row in raw_markup.inline_keyboard]
        if len(pages) > 1:
            controls = []
            if index:
                controls.append(
                    inline_button(
                        "Предыдущая страница", callback_data=f"owner-ui-page:{index - 1}", emoji_key="back"
                    )
                )
            if index + 1 < len(pages):
                controls.append(
                    inline_button(
                        "Следующая страница", callback_data=f"owner-ui-page:{index + 1}", emoji_key="next"
                    )
                )
            rows.insert(0, controls)
            text += f"\n\n<i>Страница {index + 1} из {len(pages)}</i>"
        revision = uuid.uuid4().hex[:12]
        actions: dict[str, str] = {}
        encoded_rows = []
        for row in rows:
            encoded = []
            for button in row:
                if button.callback_data:
                    token = f"ui:{revision}:{len(actions)}"
                    actions[token] = button.callback_data
                    button = button.model_copy(update={"callback_data": token})
                encoded.append(button)
            encoded_rows.append(encoded)
        encoded_markup = InlineKeyboardMarkup(inline_keyboard=encoded_rows)
        anchor = self.data.get("message_id")
        try:
            if anchor:
                await self._edit(anchor, text, encoded_markup)
            else:
                anchor = await self._send(text, encoded_markup)
        except TelegramBadRequest as exc:
            error = str(exc).lower()
            if not anchor or not any(
                reason in error
                for reason in (
                    "message to edit not found",
                    "message can't be edited",
                    "there is no text in the message",
                )
            ):
                raise
            anchor = await self._send(text, encoded_markup)
        self.data.update(message_id=anchor, actions=actions, page=index)
        await self.save()

    async def _send(self, text: str, markup: InlineKeyboardMarkup) -> int:
        try:
            message = await self.bot.send_message(self.chat_id, text, reply_markup=markup)
        except TelegramBadRequest:
            message = await self.bot.send_message(
                self.chat_id, plain_text(text), reply_markup=plain_markup(markup)
            )
        return message.message_id

    async def _edit(self, anchor: int, text: str, markup: InlineKeyboardMarkup) -> None:
        try:
            await self.bot.edit_message_text(
                text, chat_id=self.chat_id, message_id=anchor, reply_markup=markup
            )
        except TelegramBadRequest as exc:
            if "message is not modified" in str(exc).lower():
                return
            await self.bot.edit_message_text(
                plain_text(text),
                chat_id=self.chat_id,
                message_id=anchor,
                reply_markup=plain_markup(markup),
            )


async def render_owner_ui(
    message: Message, text: str, *, reply_markup: InlineKeyboardMarkup | None = None
) -> None:
    current = _current.get()
    if current is None:
        await answer_with_ui_fallback(message, text, reply_markup=reply_markup)
    else:
        await current.render(text, reply_markup)


async def track_preview(message_ids: tuple[int, ...]) -> None:
    current = _current.get()
    if current is not None:
        await current.track_preview(message_ids)


async def show_owner_ui_page(index: int) -> None:
    current = _current.get()
    if current is not None:
        await current.display_page(index)


class OwnerUiMiddleware(BaseMiddleware):
    async def __call__(
        self,
        handler: Callable[[TelegramObject, dict[str, Any]], Awaitable[Any]],
        event: TelegramObject,
        data: dict[str, Any],
    ) -> Any:
        if not isinstance(event, (Message, CallbackQuery)) or (
            isinstance(event, Message) and event.successful_payment
        ):
            return await handler(event, data)
        message = event if isinstance(event, Message) else event.message
        if not isinstance(message, Message) or event.from_user is None:
            return await handler(event, data)
        session = OwnerUiSession(
            cast(Bot, data["bot"]), data["state"].storage.redis, message.chat.id, event.from_user.id
        )
        await session.load()
        if isinstance(event, CallbackQuery):
            raw = event.data or ""
            decoded = session.data.get("actions", {}).get(raw)
            if decoded is not None and session.data.get("message_id") == message.message_id:
                # Persist consumption before business code. If its commit succeeds
                # but Telegram's response fails, replay cannot toggle/pay twice.
                session.data["actions"].pop(raw, None)
                back = decoded.startswith("$back:")
                decoded = decoded.removeprefix("$back:")
                session.navigate(decoded, back=back)
                data["owner_ui_leave_form"] = back or _navigation(decoded)
                event = event.model_copy(update={"data": decoded})
            elif _legacy_navigation(raw):
                # Old notifications/legacy menus can navigate, never mutate.
                session.navigate(raw)
                data["owner_ui_leave_form"] = True
            else:
                await event.answer("Этот экран устарел. Откройте актуальное меню командой /start.")
                return None
        else:
            command = (event.text or "").split(maxsplit=1)[0] if event.text else ""
            command = command.split("@", 1)[0]
            route = {
                "/start": "menu:home",
                "/help": "help:home",
                "🤖 Мои Боты": "menu:bots",
                "💳 Подписка": "menu:subscription",
                "💎 ПОДПИСКА": "menu:subscription",
                "🤝 Партнёрская программа": "menu:referrals",
                "📈 Аналитика": "menu:analytics",
            }.get(command if command.startswith("/") else (event.text or ""), "input")
            session.navigate(route)
            data["owner_ui_leave_form"] = route != "input"
        await session.cleanup_preview()
        token = _current.set(session)
        try:
            return await handler(event, data)
        finally:
            _current.reset(token)
