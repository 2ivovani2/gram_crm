from __future__ import annotations

from datetime import UTC, datetime

from sqlalchemy import Table
from sqlalchemy.dialects import postgresql

from gramly_welcome.models import InboxEvent
from gramly_welcome.repository import (
    _expired_inbox_claim_query,
    _inbox_claim_query,
    _inbox_source_claim_query,
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
