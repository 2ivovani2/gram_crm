from __future__ import annotations

import sqlite3
from datetime import UTC, datetime
from pathlib import Path

import pytest
from cryptography.fernet import Fernet

from gramly_welcome import view_pool
from gramly_welcome.config import Settings
from gramly_welcome.view_pool import MAX_SESSION_BYTES, ViewPoolError, validate_session_file
from gramly_welcome.view_private import parse_invites
from gramly_welcome.view_reports import ReportPoint, ViewReport, render_report_png


def test_private_invites_are_deduplicated_without_accepting_arbitrary_urls() -> None:
    assert parse_invites(
        "https://t.me/+UJFJMzqE5glhZWRi t.me/joinchat/UJFJMzqE5glhZWRi "
        "https://example.org/+UJFJMzqE5glhZWRi"
    ) == ["UJFJMzqE5glhZWRi"]


def test_session_upload_rejects_non_telethon_sqlite_and_oversize(tmp_path: Path) -> None:
    with pytest.raises(ViewPoolError):
        validate_session_file(b"not sqlite")
    with pytest.raises(ViewPoolError):
        validate_session_file(b"SQLite format 3\x00" + b"x" * MAX_SESSION_BYTES)
    path = tmp_path / "other.session"
    with sqlite3.connect(path) as db:
        db.execute("CREATE TABLE unrelated (id integer)")
    with pytest.raises(ViewPoolError, match="Telethon"):
        validate_session_file(path.read_bytes())


def test_session_upload_accepts_intact_telethon_schema(tmp_path: Path) -> None:
    path = tmp_path / "account.session"
    with sqlite3.connect(path) as db:
        db.execute("CREATE TABLE version (version integer)")
        db.execute("CREATE TABLE sessions (dc_id integer, server_address text, port integer, auth_key blob, takeout_id integer)")
    validate_session_file(path.read_bytes())


def test_report_image_contains_a_real_png_with_measured_points() -> None:
    now = datetime(2026, 9, 26, tzinfo=UTC)
    report = ViewReport(
        title="Закрытый канал с очень длинным названием и аналитикой",
        subscribers=12345,
        observed_at=now,
        points=(ReportPoint(now, 12000), ReportPoint(now, 12345)),
        views_24h=1200,
        er24=9.72,
    )
    output = render_report_png(report)
    assert output.startswith(b"\x89PNG\r\n\x1a\n")
    assert len(output) > 10_000


def test_report_image_handles_hidden_subscriber_count() -> None:
    now = datetime(2026, 9, 26, tzinfo=UTC)
    report = ViewReport(
        title="Закрытый канал", subscribers=None, observed_at=now,
        points=(), views_24h=None, er24=None,
    )
    assert render_report_png(report).startswith(b"\x89PNG\r\n\x1a\n")


@pytest.mark.asyncio
async def test_pool_blocks_four_accounts_and_opens_at_five(monkeypatch: pytest.MonkeyPatch) -> None:
    async def count_four() -> int:
        return 4
    async def count_five() -> int:
        return 5
    monkeypatch.setattr(view_pool, "get_settings", lambda: Settings(view_mtproto_api_id=1, view_mtproto_api_hash="test"))
    monkeypatch.setattr(view_pool, "healthy_count", count_four)
    assert not await view_pool.analytics_available()
    monkeypatch.setattr(view_pool, "healthy_count", count_five)
    assert await view_pool.analytics_available()


def test_session_blob_is_encrypted_and_roundtrips(monkeypatch: pytest.MonkeyPatch) -> None:
    import json

    key = Fernet.generate_key().decode()
    monkeypatch.setattr(view_pool, "get_settings", lambda: Settings(token_encryption_keys=json.dumps({"1": key})))
    raw = b"SQLite format 3\x00" + b"private credential"
    ciphertext = view_pool.seal_session(raw)
    assert "private credential" not in ciphertext
    assert view_pool.open_session(ciphertext) == raw
