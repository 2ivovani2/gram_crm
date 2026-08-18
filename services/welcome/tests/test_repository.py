from __future__ import annotations

from datetime import UTC, datetime

from sqlalchemy.dialects import postgresql

from gramly_welcome.repository import _expired_inbox_claim_query, _inbox_claim_query


def test_inbox_claim_query_uses_bounded_source_window_and_skip_locked() -> None:
    statement = _inbox_claim_query(datetime.now(UTC), per_source_limit=100).limit(50)
    compiled = str(
        statement.compile(
            dialect=postgresql.dialect(),  # type: ignore[no-untyped-call]
            compile_kwargs={"literal_binds": True},
        )
    )

    assert "JOIN LATERAL" in compiled
    assert "LIMIT 100" in compiled
    assert "eligible_sources" in compiled
    assert "LIMIT 50" in compiled
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
