from __future__ import annotations

from datetime import UTC, datetime, timedelta
from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest

from gramly_welcome.config import Settings
from gramly_welcome.models import ViewChannelSnapshot, ViewCollectionRun, ViewPostObservation
from gramly_welcome.view_bot import _channel_card, attribution, parse_channels
from gramly_welcome.view_metrics import ViewWindows, measured_windows


def test_channel_parser_deduplicates_public_channels_and_post_links() -> None:
    assert parse_channels("@Durov https://t.me/durov/123 @BotNews t.me/botnews") == ["Durov", "BotNews"]
    assert parse_channels("https://t.me/+private https://example.org/durov") == []
    assert parse_channels("https://t.me/joinchat/UJFJMzqE5glhZWRi") == []


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
    assert "Просмотры 24 / 48 / 72 ч · нет данных" in card
    assert "ER24 · нет данных" in card


@pytest.mark.asyncio
async def test_measured_windows_use_only_collected_posts_and_never_invent_history() -> None:
    now = datetime(2026, 9, 26, 1, tzinfo=UTC)
    run = ViewCollectionRun(
        channel_id=-100123,
        observed_at=now - timedelta(minutes=15),
        coverage_start_at=now - timedelta(hours=73),
        complete=True,
        post_count=3,
        error_code="",
    )
    posts = [
        ViewPostObservation(
            channel_id=-100123,
            post_id=1,
            published_at=now - timedelta(hours=2),
            observed_at=run.observed_at,
            views=100,
        ),
        ViewPostObservation(
            channel_id=-100123,
            post_id=2,
            published_at=now - timedelta(hours=30),
            observed_at=run.observed_at,
            views=50,
        ),
        ViewPostObservation(
            channel_id=-100123,
            post_id=3,
            published_at=now - timedelta(hours=60),
            observed_at=run.observed_at,
            views=20,
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
        100,
        150,
        170,
        10.0,
    )
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
async def test_public_analysis_does_not_start_below_five_accounts(monkeypatch: pytest.MonkeyPatch) -> None:
    from gramly_welcome import view_bot

    send = AsyncMock()
    reserve = AsyncMock()
    monkeypatch.setattr(view_bot, "analytics_available", AsyncMock(return_value=False))
    monkeypatch.setattr(view_bot, "_send", send)
    monkeypatch.setattr(view_bot, "_reserve_analysis", reserve)
    await view_bot._analyze(123, 42, "https://t.me/durov")
    assert "менее пяти" in send.call_args.args[1]
    reserve.assert_not_awaited()
