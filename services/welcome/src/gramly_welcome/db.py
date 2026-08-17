from __future__ import annotations

from collections.abc import AsyncIterator

from sqlalchemy.ext.asyncio import AsyncEngine, AsyncSession, async_sessionmaker, create_async_engine

from .config import Settings, get_settings


def build_engine(settings: Settings) -> AsyncEngine:
    return create_async_engine(
        settings.database_url,
        pool_pre_ping=True,
        pool_size=10,
        max_overflow=20,
        pool_recycle=1800,
    )


settings = get_settings()
engine = build_engine(settings)
session_factory = async_sessionmaker(engine, expire_on_commit=False)


async def session_dependency() -> AsyncIterator[AsyncSession]:
    async with session_factory() as session:
        yield session
