from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime
from typing import Annotated

from fastapi import APIRouter, Depends, Header, HTTPException, Request, Response, status
from pydantic import BaseModel, Field
from sqlalchemy.ext.asyncio import AsyncSession

from .commercial import access_for_owner, list_sellable_plans
from .config import Settings, get_settings
from .db import session_dependency
from .telegram_auth import TelegramInitDataError, verify_init_data
from .web_sessions import (
    AuthenticatedWebSession,
    authenticate_web_session,
    create_web_session,
    csrf_token_valid,
    revoke_web_session,
)

router = APIRouter(prefix="/api/v1", tags=["mini-app"])
SessionDep = Annotated[AsyncSession, Depends(session_dependency)]
SettingsDep = Annotated[Settings, Depends(get_settings)]


class TelegramSessionRequest(BaseModel):
    init_data: str = Field(min_length=1, max_length=16_384)


class TelegramSessionResponse(BaseModel):
    csrf_token: str
    expires_at: datetime


@dataclass(frozen=True)
class CurrentWebUser:
    auth: AuthenticatedWebSession


async def current_web_user(
    request: Request,
    session: SessionDep,
    settings: SettingsDep,
) -> CurrentWebUser:
    auth = await authenticate_web_session(
        session, request.cookies.get(settings.mini_app_cookie_name, "")
    )
    if auth is None:
        raise HTTPException(status.HTTP_401_UNAUTHORIZED, "Telegram session is required")
    return CurrentWebUser(auth)


CurrentUserDep = Annotated[CurrentWebUser, Depends(current_web_user)]


async def csrf_protected_user(
    session: SessionDep,
    user: CurrentUserDep,
    csrf_token: Annotated[str, Header(alias="X-CSRF-Token")],
) -> CurrentWebUser:
    if not await csrf_token_valid(session, user.auth.session_id, csrf_token):
        raise HTTPException(status.HTTP_403_FORBIDDEN, "Invalid CSRF token")
    return user


CsrfUserDep = Annotated[CurrentWebUser, Depends(csrf_protected_user)]


@router.post("/session/telegram", response_model=TelegramSessionResponse)
async def telegram_session(
    payload: TelegramSessionRequest,
    response: Response,
    session: SessionDep,
    settings: SettingsDep,
) -> TelegramSessionResponse:
    try:
        verified = verify_init_data(
            payload.init_data,
            settings.interface_bot_token,
            max_age_seconds=settings.mini_app_auth_max_age_seconds,
        )
    except TelegramInitDataError as exc:
        raise HTTPException(status.HTTP_401_UNAUTHORIZED, str(exc)) from exc
    created = await create_web_session(
        session,
        verified,
        lifetime_seconds=settings.mini_app_session_seconds,
    )
    response.set_cookie(
        settings.mini_app_cookie_name,
        created.token,
        max_age=settings.mini_app_session_seconds,
        secure=settings.mini_app_cookie_secure,
        httponly=True,
        samesite="strict",
        path="/",
    )
    return TelegramSessionResponse(csrf_token=created.csrf_token, expires_at=created.expires_at)


@router.get("/me")
async def me(user: CurrentUserDep, session: SessionDep) -> dict[str, object]:
    owner = user.auth.owner
    access = await access_for_owner(session, owner.id)
    return {
        "owner": {
            "id": owner.id,
            "telegram_id": owner.telegram_id,
            "username": owner.username,
            "first_name": owner.first_name,
            "last_name": owner.last_name,
        },
        "access": {
            "entitled": access.entitled,
            "status": access.status,
            "plan": access.plan_slug,
            "plan_name": access.plan_name,
            "ends_at": access.ends_at,
            "entitlements": access.entitlements,
            "quotas": {
                "bots": access.max_bots,
                "channels": access.max_channels,
                "monthly_delivery_operations": access.monthly_delivery_operations,
                "media_storage_bytes": access.media_storage_bytes,
            },
        },
    }


@router.get("/plans")
async def plans(session: SessionDep) -> dict[str, object]:
    return {"plans": await list_sellable_plans(session)}


@router.post("/session/logout", status_code=status.HTTP_204_NO_CONTENT)
async def logout(
    response: Response,
    session: SessionDep,
    settings: SettingsDep,
    user: CsrfUserDep,
) -> Response:
    await revoke_web_session(session, user.auth.session_id)
    response.delete_cookie(settings.mini_app_cookie_name, path="/")
    response.status_code = status.HTTP_204_NO_CONTENT
    return response
