from __future__ import annotations

from datetime import UTC, datetime
from types import SimpleNamespace
from typing import Any, cast

import pytest
from sqlalchemy import Table
from sqlalchemy.dialects import postgresql
from sqlalchemy.ext.asyncio import AsyncSession

from gramly_welcome.models import InboxEvent
from gramly_welcome.repository import (
    _expired_inbox_claim_query,
    _inbox_claim_query,
    _inbox_source_claim_query,
    claim_inbox_batch,
    queue_snapshot,
)


def test_inbox_claim_query_uses_one_source_index_and_skip_locked() -> None:
    statement = _inbox_claim_query(datetime.now(UTC), "bot:test").limit(50)
    compiled = str(
        statement.compile(
            dialect=postgresql.dialect(),  # type: ignore[no-untyped-call]
            compile_kwargs={"literal_binds": True},
        )
    )

    assert "inbox_event.source_key = 'bot:test'" in compiled
    assert "LIMIT 50" in compiled
    assert "SKIP LOCKED" in compiled


def test_source_scheduler_is_round_robin_and_lock_safe() -> None:
    statement = _inbox_source_claim_query(datetime.now(UTC)).limit(1)
    compiled = str(
        statement.compile(
            dialect=postgresql.dialect(),  # type: ignore[no-untyped-call]
            compile_kwargs={"literal_binds": True},
        )
    )

    assert "EXISTS" not in compiled
    assert "inbox_event" not in compiled
    assert "inbox_source.pending_count > 0" in compiled
    assert "inbox_source.next_available_at" in compiled
    assert "inbox_source.last_claimed_at" in compiled
    assert "LIMIT 1" in compiled
    assert "SKIP LOCKED" in compiled


@pytest.mark.asyncio
async def test_empty_sources_do_not_age_the_event_queue() -> None:
    statements: list[Any] = []

    class Session:
        async def execute(self, statement: Any) -> SimpleNamespace:
            statements.append(statement)
            return SimpleNamespace(one=lambda: (0, None))

        async def scalar(self, _statement: Any) -> int:
            return 0

    snapshot = await queue_snapshot(cast(AsyncSession, Session()))
    compiled = str(statements[0].compile(dialect=postgresql.dialect()))  # type: ignore[no-untyped-call]
    assert "inbox_source.pending_count >" in compiled
    assert "inbox_source.source_key !=" in compiled
    assert snapshot["events"] == (0, 0, 0.0)


def test_expired_lease_query_is_separate_and_lock_safe() -> None:
    statement = _expired_inbox_claim_query(datetime.now(UTC)).limit(50)
    compiled = str(
        statement.compile(
            dialect=postgresql.dialect(),  # type: ignore[no-untyped-call]
            compile_kwargs={"literal_binds": True},
        )
    )

    assert "lease_expires_at <" in compiled
    assert "processing" in compiled
    assert "SKIP LOCKED" in compiled
    assert "pending" not in compiled


def test_inbox_state_indexes_only_cover_live_queue_paths() -> None:
    table = InboxEvent.__table__
    assert isinstance(table, Table)
    index_names = {index.name for index in table.indexes}

    assert "ix_inbox_fair_pending" in index_names
    assert "ix_inbox_expired_lease" in index_names
    assert "ix_inbox_dead" in index_names
    assert "ix_inbox_claim" not in index_names
    assert "ix_inbox_event_status" not in index_names


@pytest.mark.asyncio
@pytest.mark.parametrize("only_view", [False, True])
async def test_view_claims_are_isolated_from_hello(only_view: bool) -> None:
    statements: list[str] = []

    class Transaction:
        async def __aenter__(self) -> None:
            return None

        async def __aexit__(self, *_args: object) -> None:
            return None

    class Session:
        def begin(self) -> Transaction:
            return Transaction()

        async def scalars(self, statement: Any) -> Any:
            statements.append(str(statement.compile(
                dialect=postgresql.dialect(),  # type: ignore[no-untyped-call]
                compile_kwargs={"literal_binds": True},
            )))
            return SimpleNamespace(all=lambda: [])

        async def scalar(self, statement: Any) -> None:
            statements.append(str(statement.compile(
                dialect=postgresql.dialect(),  # type: ignore[no-untyped-call]
                compile_kwargs={"literal_binds": True},
            )))
            return None

    await claim_inbox_batch(
        cast(AsyncSession, Session()), worker_id="test", limit=2,
        lease_seconds=180, include_owner=False, only_view=only_view,
    )
    assert len(statements) == 2
    if only_view:
        assert all("source_key = 'view'" in sql for sql in statements)
    else:
        assert all("bot_id IS NOT NULL" in sql for sql in statements)
