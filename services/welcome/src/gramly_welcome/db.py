from __future__ import annotations

from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
from contextvars import ContextVar

from sqlalchemy.ext.asyncio import AsyncEngine, AsyncSession, async_sessionmaker, create_async_engine

from .config import Settings, get_settings


def build_engine(settings: Settings) -> AsyncEngine:
    return create_async_engine(
        settings.database_url,
        pool_pre_ping=True,
        hide_parameters=True,
        pool_size=settings.database_pool_size,
        max_overflow=settings.database_max_overflow,
        pool_recycle=1800,
        pool_timeout=5,
        connect_args={"timeout": 5, "command_timeout": 30},
    )


settings = get_settings()
engine = build_engine(settings)
owner_transaction: ContextVar[AsyncSession | None] = ContextVar("owner_transaction", default=None)


class ApplicationSession(AsyncSession):
    async def commit(self) -> None:
        if owner_transaction.get() is self:
            # Owner services share one bounded unit of work. Their existing
            # commit boundaries flush; only the action runner commits effects
            # together with its response intent and receipt.
            await self.flush()
        else:
            await super().commit()

    async def rollback(self) -> None:
        if owner_transaction.get() is self:
            raise RuntimeError("Owner action requested rollback")
        await super().rollback()


independent_session = async_sessionmaker(engine, class_=ApplicationSession, expire_on_commit=False)


@asynccontextmanager
async def session_factory() -> AsyncIterator[AsyncSession]:
    shared = owner_transaction.get()
    if shared is not None:
        # A caught service failure must not poison the action's outer transaction.
        async with shared.begin_nested():
            yield shared
    else:
        async with independent_session() as session:
            yield session


async def session_dependency() -> AsyncIterator[AsyncSession]:
    async with session_factory() as session:
        yield session
