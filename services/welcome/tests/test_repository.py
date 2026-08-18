from __future__ import annotations

from datetime import UTC, datetime

from sqlalchemy.dialects import postgresql

from gramly_welcome.repository import _inbox_claim_query


def test_inbox_claim_query_uses_bounded_source_window_and_skip_locked() -> None:
    statement = _inbox_claim_query(datetime.now(UTC), per_source_limit=100).limit(50)
    compiled = str(
        statement.compile(
            dialect=postgresql.dialect(),  # type: ignore[no-untyped-call]
            compile_kwargs={"literal_binds": True},
        )
    )

    assert "source_rank <= 100" in compiled
    assert "LIMIT 50" in compiled
    assert "SKIP LOCKED" in compiled
