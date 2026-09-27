from __future__ import annotations

from datetime import UTC, datetime, timedelta
from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest
from aiogram.enums import ChatType
from aiogram.exceptions import TelegramBadRequest
from aiogram.methods import SendMessage

from gramly_welcome.config import Settings
from gramly_welcome.models import ViewChannelSnapshot, ViewCollectionRun, ViewPostObservation
from gramly_welcome.view_bot import (
    _channel_card,
    _rank_posts,
    _report_keyboard,
    _styled_markup,
    attribution,
    parse_channels,
    parse_message_targets,
    parse_targets,
)
from gramly_welcome.view_metrics import ViewWindows, measured_windows


def test_channel_parser_deduplicates_public_channels_and_post_links() -> None:
    assert parse_channels("@Durov https://t.me/durov/123 @BotNews t.me/botnews") == ["Durov", "BotNews"]
    assert parse_channels("https://t.me/+private https://example.org/durov") == []
    assert parse_channels("https://t.me/joinchat/UJFJMzqE5glhZWRi") == []


def test_forwarded_hidden_links_and_utf16_offsets_keep_all_channels() -> None:
    text = "🐊 " + "\n".join(f"{index}. Канал {index}" for index in range(1, 15)) + "\nРеклама: @admanager"
    entities = []
    for index in range(1, 15):
        label = f"Канал {index}"
        offset = len(text[:text.index(label)].encode("utf-16-le")) // 2
        entities.append({
            "type": "text_link", "offset": offset, "length": len(label),
            "url": f"https://t.me/channel{index}",
        })
    assert parse_message_targets({"text": text, "entities": entities}) == [
        f"https://t.me/channel{index}" for index in range(1, 15)
    ]
    assert parse_message_targets({"caption": "Канал", "caption_entities": [
        {"type": "text_link", "offset": 0, "length": 5, "url": "https://t.me/+UJFJMzqE5glhZWRi"},
    ]}) == ["https://t.me/+UJFJMzqE5glhZWRi"]


def test_target_parser_preserves_order_and_never_truncates_or_accepts_foreign_domain() -> None:
    text = " ".join(f"https://t.me/channel{index}" for index in range(1, 22))
    assert len(parse_targets(text)) == 21
    assert parse_message_targets({"text": "Проверка", "entities": [
        {"type": "text_link", "offset": 0, "length": 8, "url": "https://example.com/?next=t.me/channel1"},
    ]}) == []


@pytest.mark.asyncio
async def test_batch_limit_reports_skipped_links_and_preserves_valid_results(monkeypatch: pytest.MonkeyPatch) -> None:
    from gramly_welcome import view_bot

    send = AsyncMock()
    lookup = AsyncMock(side_effect=[
        (-100000 - (1 if index == 2 else index), f"channel{index}", f"Канал {index}", 100, datetime(2026, 9, 28, tzinfo=UTC))
        for index in range(1, 21)
    ])

    class Session:
        async def __aenter__(self) -> Session:
            return self

        async def __aexit__(self, *_args: object) -> None:
            return None

    monkeypatch.setattr(view_bot, "analytics_available", AsyncMock(return_value=True))
    monkeypatch.setattr(view_bot, "_reserve_analysis", AsyncMock(return_value=True))
    monkeypatch.setattr(view_bot, "_public_channel_data", lookup)
    monkeypatch.setattr(view_bot, "_send", send)
    monkeypatch.setattr(view_bot, "_send_batch_summary", AsyncMock())
    monkeypatch.setattr(view_bot, "session_factory", lambda: Session())
    monkeypatch.setattr(view_bot, "measured_windows", AsyncMock(return_value=ViewWindows(None, None, None, None, None)))
    monkeypatch.setattr(view_bot, "_post_views", AsyncMock(return_value=None))
    monkeypatch.setattr(view_bot, "lease_account", lambda: (_ for _ in ()).throw(RuntimeError("offline")))
    await view_bot._analyze(42, 7, " ".join(f"https://t.me/channel{index}" for index in range(1, 22)))
    assert lookup.await_count == 20
    assert "Ещё 1 ссылка вне лимита" in send.call_args_list[-1].args[1]
    view_bot._send_batch_summary.assert_awaited_once()
    assert len(view_bot._send_batch_summary.await_args.args[2]) == 19


@pytest.mark.asyncio
async def test_picture_input_opens_image_after_single_channel(monkeypatch: pytest.MonkeyPatch) -> None:
    from gramly_welcome import view_bot

    class Session:
        async def __aenter__(self) -> Session:
            return self

        async def __aexit__(self, *_args: object) -> None:
            return None

        async def get(self, _model: object, _key: int) -> SimpleNamespace:
            return SimpleNamespace(pending_action="network_picture")

    analyze = AsyncMock(return_value=[-10042])
    image = AsyncMock()
    clear = AsyncMock()
    monkeypatch.setattr(view_bot, "session_factory", lambda: Session())
    monkeypatch.setattr(view_bot, "owner_from_telegram", AsyncMock(return_value=SimpleNamespace(id=7)))
    monkeypatch.setattr(view_bot, "_gate", AsyncMock(return_value=True))
    monkeypatch.setattr(view_bot, "_analyze", analyze)
    monkeypatch.setattr(view_bot, "_send_report_image", image)
    monkeypatch.setattr(view_bot, "_clear_pending_action", clear)
    await view_bot.process_view_update({"message": {
        "from": {"id": 42, "is_bot": False, "first_name": "Test"},
        "chat": {"id": 42},
        "text": "https://t.me/channel1",
    }})
    clear.assert_awaited_once_with(7)
    analyze.assert_awaited_once_with(42, 7, "https://t.me/channel1")
    image.assert_awaited_once_with(42, 7, -10042)


def test_attribution_is_visible_text_not_only_inline_button(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(
        "gramly_welcome.view_bot.get_settings",
        lambda: Settings(view_bot_username="gramly_view_bot"),
    )
    card = attribution("<b>Канал</b>\nНет данных")
    assert card.endswith(
        'Аналитика подготовлена в GramlyView · <a href="https://t.me/gramly_view_bot">Открыть бот</a>'
    )


def test_unknown_metrics_are_not_rendered_as_zero() -> None:
    card = _channel_card(
        "Канал & друзья",
        "public_channel",
        123,
        datetime(2026, 9, 26, tzinfo=UTC),
        ViewWindows(None, None, None, None, None),
    )
    assert "Канал &amp; друзья" in card
    assert "Средние просмотры поста через 24 / 48 / 72 ч · нет данных / нет данных / нет данных" in card
    assert "ER24 · нет данных" in card
    assert "<blockquote>" in card and "</blockquote>" in card


def test_global_top_uses_latest_observation_and_measured_metric() -> None:
    now = datetime(2026, 9, 28, tzinfo=UTC)
    rows = [
        SimpleNamespace(channel_id=1, post_id=11, views=500, forwards=20,
                        replies_count=None, reactions_count=10, published_at=now),
        SimpleNamespace(channel_id=1, post_id=11, views=300, forwards=10,
                        replies_count=None, reactions_count=5, published_at=now),
        SimpleNamespace(channel_id=2, post_id=22, views=400, forwards=30,
                        replies_count=8, reactions_count=9, published_at=now),
    ]
    assert [(row.channel_id, row.post_id) for row in _rank_posts(rows, "views")] == [(1, 11), (2, 22)]
    assert [(row.channel_id, row.post_id) for row in _rank_posts(rows, "forwards")] == [(2, 22), (1, 11)]
    assert [(row.channel_id, row.post_id) for row in _rank_posts(rows, "comments")] == [(2, 22)]


def test_report_actions_have_premium_icons_and_navigation() -> None:
    keyboard = _styled_markup(_report_keyboard(-10077, public_username="public_channel"))
    buttons = [button for row in keyboard.inline_keyboard for button in row]
    assert {button.callback_data for button in buttons} >= {
        "view:refresh:-10077", "view:image:-10077", "view:top:-10077",
        "view:export:-10077", "view:menu",
    }
    assert all(button.icon_custom_emoji_id for button in buttons)


@pytest.mark.asyncio
async def test_view_message_falls_back_if_telegram_rejects_premium(monkeypatch: pytest.MonkeyPatch) -> None:
    from gramly_welcome import view_bot

    send = AsyncMock(side_effect=[
        TelegramBadRequest(method=SendMessage(chat_id=42, text="test"), message="custom emoji not allowed"),
        SimpleNamespace(message_id=1),
    ])
    monkeypatch.setattr(view_bot, "view_bot", lambda: SimpleNamespace(send_message=send))
    monkeypatch.setattr(view_bot, "get_settings", lambda: Settings(view_bot_username="gramly_view_bot"))
    await view_bot._send(42, "<b>Отчёт</b>", keyboard=_report_keyboard(-10077))
    assert "tg-emoji" in send.call_args_list[0].args[1]
    assert "tg-emoji" not in send.call_args_list[1].args[1]
    first = send.call_args_list[0].kwargs["reply_markup"]
    second = send.call_args_list[1].kwargs["reply_markup"]
    assert first.inline_keyboard[0][0].icon_custom_emoji_id
    assert getattr(second.inline_keyboard[0][0], "icon_custom_emoji_id", None) is None


@pytest.mark.asyncio
async def test_back_from_collection_name_treats_next_link_as_channel(monkeypatch: pytest.MonkeyPatch) -> None:
    from gramly_welcome import view_bot

    state = SimpleNamespace(pending_action="collection_name", pending_query="")

    class Session:
        async def __aenter__(self) -> Session:
            return self

        async def __aexit__(self, *_args: object) -> None:
            return None

        def begin(self) -> Session:
            return self

        async def get(self, _model: object, _key: int) -> object:
            return state

    analyze = AsyncMock()
    monkeypatch.setattr(view_bot, "session_factory", lambda: Session())
    monkeypatch.setattr(view_bot, "owner_from_telegram", AsyncMock(return_value=SimpleNamespace(id=7)))
    monkeypatch.setattr(view_bot, "view_bot", lambda: SimpleNamespace(answer_callback_query=AsyncMock()))
    monkeypatch.setattr(view_bot, "_gate", AsyncMock(return_value=True))
    monkeypatch.setattr(view_bot, "_send", AsyncMock())
    monkeypatch.setattr(view_bot, "_analyze", analyze)
    user = {"id": 42, "is_bot": False, "first_name": "Test"}
    await view_bot.process_view_update({"callback_query": {
        "id": "cb", "from": user, "data": "view:menu", "message": {"chat": {"id": 42}},
    }})
    assert state.pending_action == ""
    await view_bot.process_view_update({"message": {
        "from": user, "chat": {"id": 42}, "text": "https://t.me/public_channel",
    }})
    analyze.assert_awaited_once_with(42, 7, "https://t.me/public_channel")


@pytest.mark.asyncio
async def test_collection_name_rejects_channel_link(monkeypatch: pytest.MonkeyPatch) -> None:
    from gramly_welcome import view_bot

    class Session:
        async def __aenter__(self) -> Session:
            return self

        async def __aexit__(self, *_args: object) -> None:
            return None

        async def get(self, _model: object, _key: int) -> object:
            return SimpleNamespace(pending_action="collection_name")

    send = AsyncMock()
    monkeypatch.setattr(view_bot, "session_factory", lambda: Session())
    monkeypatch.setattr(view_bot, "_send", send)
    for link in ("https://t.me/public_channel", "https://t.me/+AbCdEfGh12345678"):
        assert await view_bot._handle_collection_text(42, 7, link)
        assert "название коллекции" in send.call_args.args[1]


@pytest.mark.asyncio
async def test_measured_windows_use_only_collected_posts_and_never_invent_history() -> None:
    now = datetime(2026, 9, 26, 1, tzinfo=UTC)
    run = ViewCollectionRun(
        channel_id=-100123,
        observed_at=now - timedelta(minutes=15),
        coverage_start_at=now - timedelta(hours=73),
        complete=True,
        post_count=4,
        error_code="",
    )
    posts = [
        ViewPostObservation(
            channel_id=-100123,
            post_id=1,
            published_at=now - timedelta(hours=30),
            observed_at=now - timedelta(hours=6),
            views=600,
        ),
        ViewPostObservation(
            channel_id=-100123,
            post_id=1,
            published_at=now - timedelta(hours=30),
            observed_at=now - timedelta(hours=5),
            views=800,
        ),
        ViewPostObservation(
            channel_id=-100123,
            post_id=2,
            published_at=now - timedelta(hours=73),
            observed_at=now - timedelta(hours=49),
            views=800,
        ),
        ViewPostObservation(
            channel_id=-100123,
            post_id=2,
            published_at=now - timedelta(hours=73),
            observed_at=now - timedelta(hours=25),
            views=1000,
        ),
        ViewPostObservation(
            channel_id=-100123,
            post_id=2,
            published_at=now - timedelta(hours=73),
            observed_at=now - timedelta(hours=1),
            views=1200,
        ),
        ViewPostObservation(
            channel_id=-100123,
            post_id=3,
            published_at=now - timedelta(hours=80),
            observed_at=now - timedelta(hours=3),
            views=10_000,
        ),
    ]

    class FakeScalars:
        def all(self) -> list[ViewPostObservation]:
            return posts

    class FakeSession:
        async def scalar(self, _statement: object) -> ViewCollectionRun:
            return run

        async def scalars(self, _statement: object) -> FakeScalars:
            return FakeScalars()

    result = await measured_windows(FakeSession(), -100123, 1000, now=now)  # type: ignore[arg-type]
    assert (result.views_24h, result.views_48h, result.views_72h, result.er24_percent) == (
        700,
        1000,
        1200,
        70.0,
    )
    assert (result.posts_24h, result.posts_48h, result.posts_72h) == (2, 1, 1)
    run.observed_at = now - timedelta(hours=3)
    assert (await measured_windows(FakeSession(), -100123, 1000, now=now)).views_24h == 700  # type: ignore[arg-type]
    run.observed_at = now - timedelta(minutes=15)
    unsampled = posts[:]
    posts[:] = [posts[-1]]
    no_history = await measured_windows(FakeSession(), -100123, 1000, now=now)  # type: ignore[arg-type]
    assert (no_history.views_24h, no_history.views_48h, no_history.views_72h) == (None, None, None)
    assert (no_history.posts_24h, no_history.posts_48h, no_history.posts_72h) == (0, 0, 0)
    posts[:] = unsampled
    run.complete = False
    missing = await measured_windows(FakeSession(), -100123, 1000, now=now)  # type: ignore[arg-type]
    assert missing.views_24h is None and missing.er24_percent is None


@pytest.mark.asyncio
async def test_view_gate_denies_unconfigured_and_telegram_lookup_error(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    from gramly_welcome import view_bot

    class Session:
        async def __aenter__(self) -> Session:
            return self

        async def __aexit__(self, *_args: object) -> None:
            return None

        async def get(self, _model: object, _key: int) -> object:
            return SimpleNamespace(id=7, telegram_id=42)

    send = AsyncMock()
    monkeypatch.setattr(view_bot, "session_factory", lambda: Session())
    monkeypatch.setattr(view_bot, "_send", send)
    monkeypatch.setattr(view_bot, "view_bot", lambda: object())
    monkeypatch.setattr(
        view_bot,
        "required_channel_config",
        AsyncMock(
            return_value=SimpleNamespace(
                enabled=False,
                channel_id=0,
                url="",
                title="News",
            )
        ),
    )
    assert not await view_bot._gate(42, 7)
    assert "не настроена" in send.call_args.args[1]

    monkeypatch.setattr(
        view_bot,
        "required_channel_config",
        AsyncMock(
            return_value=SimpleNamespace(
                enabled=True,
                channel_id=-1001,
                url="https://t.me/news",
                title="News",
            )
        ),
    )
    monkeypatch.setattr(
        view_bot,
        "check_required_membership",
        AsyncMock(
            return_value=SimpleNamespace(
                allowed=True,
                temporarily_unavailable=True,
            )
        ),
    )
    assert not await view_bot._gate(42, 7)
    assert "Не удалось проверить" in send.call_args.args[1]


@pytest.mark.asyncio
async def test_private_report_requires_a_link_for_the_requesting_user(monkeypatch: pytest.MonkeyPatch) -> None:
    from gramly_welcome import view_bot

    class Session:
        async def __aenter__(self) -> Session:
            return self

        async def __aexit__(self, *_args: object) -> None:
            return None

        async def scalar(self, statement: object) -> object:
            if "view_channel_snapshot" in str(statement):
                return ViewChannelSnapshot(
                    channel_id=-10077, username="", title="Только по приглашению",
                    subscriber_count=100, observed_at=datetime.now(UTC),
                )
            return None

    monkeypatch.setattr(view_bot, "session_factory", lambda: Session())
    assert await view_bot._report_for(42, -10077) is None


@pytest.mark.asyncio
async def test_public_report_rechecks_channel_identity_before_export(monkeypatch: pytest.MonkeyPatch) -> None:
    from gramly_welcome import view_bot

    session_closed = False

    class Session:
        async def __aenter__(self) -> Session:
            return self

        async def __aexit__(self, *_args: object) -> None:
            nonlocal session_closed
            session_closed = True
            return None

        async def scalar(self, _statement: object) -> ViewChannelSnapshot:
            return ViewChannelSnapshot(
                channel_id=-10077, username="old_public", title="Бывший публичный",
                subscriber_count=100, observed_at=datetime.now(UTC),
            )

        async def scalars(self, _statement: object) -> SimpleNamespace:
            return SimpleNamespace(all=lambda: [])

    async def changed_identity(_username: str) -> SimpleNamespace:
        assert session_closed
        return SimpleNamespace(id=-10088, type=ChatType.CHANNEL)

    bot = SimpleNamespace(get_chat=AsyncMock(side_effect=changed_identity))
    monkeypatch.setattr(view_bot, "session_factory", lambda: Session())
    monkeypatch.setattr(view_bot, "view_bot", lambda: bot)
    monkeypatch.setattr(view_bot, "measured_windows", AsyncMock(return_value=ViewWindows(None, None, None, None, None)))
    assert await view_bot._report_for(42, -10077) is None
    bot.get_chat.assert_awaited_once_with("@old_public")


@pytest.mark.asyncio
async def test_public_analysis_does_not_start_below_three_accounts(monkeypatch: pytest.MonkeyPatch) -> None:
    from gramly_welcome import view_bot

    send = AsyncMock()
    reserve = AsyncMock()
    monkeypatch.setattr(view_bot, "analytics_available", AsyncMock(return_value=False))
    monkeypatch.setattr(view_bot, "_send", send)
    monkeypatch.setattr(view_bot, "_reserve_analysis", reserve)
    await view_bot._analyze(123, 42, "https://t.me/durov")
    assert "менее трёх" in send.call_args.args[1]
    reserve.assert_not_awaited()
