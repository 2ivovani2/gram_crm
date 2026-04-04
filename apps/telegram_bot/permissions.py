"""
aiogram Filters for role/status permission checks.

Usage in handlers:
    @router.message(Command("admin"), IsAdmin())
    @router.callback_query(SomeCallback.filter(), IsActivatedWorker())

Filters receive `db_user` from UserMiddleware via handler data injection.
If db_user is None (e.g. anonymous channel post), all filters return False.
"""
from __future__ import annotations
from aiogram.filters import BaseFilter
from aiogram.types import Message, CallbackQuery
from apps.users.models import User, UserRole, UserStatus


class IsAdmin(BaseFilter):
    async def __call__(self, event: Message | CallbackQuery, db_user: User | None = None) -> bool:
        return db_user is not None and db_user.role == UserRole.ADMIN


class IsActivatedWorker(BaseFilter):
    async def __call__(self, event: Message | CallbackQuery, db_user: User | None = None) -> bool:
        return (
            db_user is not None
            and db_user.is_activated
            and db_user.status not in (UserStatus.INACTIVE, UserStatus.BANNED)
        )


class IsNotActivated(BaseFilter):
    """Matches users who have NOT yet been activated (need invite key)."""
    async def __call__(self, event: Message | CallbackQuery, db_user: User | None = None) -> bool:
        if db_user is None:
            return True
        return not db_user.is_activated


class IsNotBanned(BaseFilter):
    async def __call__(self, event: Message | CallbackQuery, db_user: User | None = None) -> bool:
        if db_user is None:
            return True
        return db_user.status != UserStatus.BANNED
