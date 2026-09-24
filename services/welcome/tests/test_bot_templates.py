from __future__ import annotations

import uuid
from collections import deque
from typing import Any, cast

import pytest
from sqlalchemy.ext.asyncio import AsyncSession

from gramly_welcome.content_service import ContentValidationError, clone_bot_configuration
from gramly_welcome.models import (
    ContentAttachment,
    ContentFlow,
    ContentFlowVersion,
    ContentKeyboard,
    ContentKeyboardButton,
    ContentStep,
    ManagedBot,
)


class _Rows:
    def __init__(self, values: list[Any]) -> None:
        self.values = values

    def all(self) -> list[Any]:
        return self.values


class _FakeSession:
    def __init__(self, *, scalars: list[list[Any]], scalar: list[Any]) -> None:
        self._scalars = deque(scalars)
        self._scalar = deque(scalar)
        self.added: list[Any] = []
        self.committed = False
        self._next_id = 900

    async def scalars(self, _statement: Any) -> _Rows:
        return _Rows(self._scalars.popleft())

    async def scalar(self, _statement: Any) -> Any:
        return self._scalar.popleft()

    def add(self, value: Any) -> None:
        if hasattr(value, "id") and value.id is None:
            value.id = self._next_id
            self._next_id += 1
        self.added.append(value)

    def add_all(self, values: list[Any]) -> None:
        for value in values:
            self.add(value)

    async def flush(self) -> None:
        return None

    async def commit(self) -> None:
        self.committed = True


def _managed(bot_id: int, owner_id: int, *, auto_approve: bool = False) -> ManagedBot:
    return ManagedBot(
        id=bot_id,
        owner_id=owner_id,
        public_id=uuid.uuid4(),
        telegram_id=-bot_id,
        username=f"bot_{bot_id}",
        display_name=f"Bot {bot_id}",
        token_ciphertext="encrypted",
        webhook_secret=f"webhook-{bot_id}",
        path_secret=f"path-{bot_id}",
        auto_approve=auto_approve,
        welcome_delay_seconds=41 if auto_approve else 0,
        approval_delay_seconds=17 if auto_approve else 0,
        is_active=True,
    )


@pytest.mark.asyncio
async def test_clone_bot_configuration_copies_only_reusable_content() -> None:
    owner_id = 7
    source = _managed(10, owner_id, auto_approve=True)
    destination = _managed(11, owner_id)
    flow = ContentFlow(
        id=20,
        bot_id=source.id,
        name="Welcome",
        kind="welcome",
        assignment_mode="selected",
        is_active=True,
    )
    version = ContentFlowVersion(
        id=30,
        flow_id=flow.id,
        version=2,
        status="published",
        author_telegram_id=100,
        first_delay_seconds=12,
    )
    step = ContentStep(
        id=40,
        version_id=version.id,
        position=0,
        payload={"type": "text", "text": "Привет"},
        delay_after_seconds=9,
    )
    attachment = ContentAttachment(
        id=50,
        step_id=step.id,
        position=0,
        media_type="photo",
        storage_key="shared/photo.jpg",
        payload={"native": True},
    )
    keyboard = ContentKeyboard(id=60, step_id=step.id, kind="inline", settings={"resize": False})
    button = ContentKeyboardButton(
        id=70,
        keyboard_id=keyboard.id,
        row=0,
        position=0,
        text="Сайт",
        action_type="url",
        value="https://gramly.tech",
        style="primary",
    )
    session = _FakeSession(
        scalars=[
            [source, destination],
            [flow],
            [version],
            [step],
            [attachment],
            [button],
        ],
        scalar=[0, None, keyboard],
    )

    result = await clone_bot_configuration(
        cast(AsyncSession, session),
        owner_id,
        source.id,
        destination.id,
        business_enabled=True,
    )

    assert result.flows == result.versions == result.steps == 1
    assert session.committed
    assert destination.auto_approve is True
    assert destination.welcome_delay_seconds == 41
    assert destination.approval_delay_seconds == 17
    copied_flow = next(item for item in session.added if isinstance(item, ContentFlow))
    copied_version = next(item for item in session.added if isinstance(item, ContentFlowVersion))
    copied_step = next(item for item in session.added if isinstance(item, ContentStep))
    copied_attachment = next(item for item in session.added if isinstance(item, ContentAttachment))
    assert copied_flow.bot_id == destination.id
    assert copied_version.legacy_version_id is None
    assert copied_step.payload == step.payload and copied_step.payload is not step.payload
    assert copied_attachment.storage_key == attachment.storage_key
    assert copied_attachment.payload is not attachment.payload


@pytest.mark.asyncio
async def test_clone_bot_configuration_blocks_business_rotation_for_free_owner() -> None:
    owner_id = 8
    source = _managed(12, owner_id)
    destination = _managed(13, owner_id)
    session = _FakeSession(scalars=[[source, destination]], scalar=[0, 777])

    with pytest.raises(ContentValidationError, match="Business-ротация"):
        await clone_bot_configuration(
            cast(AsyncSession, session),
            owner_id,
            source.id,
            destination.id,
            business_enabled=False,
        )

    assert not session.committed
    assert session.added == []


@pytest.mark.asyncio
async def test_clone_bot_configuration_blocks_long_welcome_for_free_owner() -> None:
    owner_id = 8
    source = _managed(12, owner_id)
    destination = _managed(13, owner_id)
    session = _FakeSession(scalars=[[source, destination]], scalar=[0, None, 123])

    with pytest.raises(ContentValidationError, match="6–10 сообщениями требуется Business"):
        await clone_bot_configuration(
            cast(AsyncSession, session), owner_id, source.id, destination.id,
            business_enabled=False,
        )

    assert not session.committed
    assert session.added == []
