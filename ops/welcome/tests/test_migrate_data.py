from ops.welcome.migrate_data import TARGET_TABLES, asyncpg_dsn


def test_asyncpg_dsn_normalizes_sqlalchemy_async_driver() -> None:
    assert (
        asyncpg_dsn("postgresql+asyncpg://user:password@database.example/gramly")
        == "postgresql://user:password@database.example/gramly"
    )


def test_asyncpg_dsn_preserves_native_postgresql_url() -> None:
    value = "postgresql://user:password@database.example/gramly"

    assert asyncpg_dsn(value) == value


def test_final_copy_replaces_active_album_drafts() -> None:
    assert "welcome_draft" in TARGET_TABLES
    assert "welcome_draft_media" in TARGET_TABLES
