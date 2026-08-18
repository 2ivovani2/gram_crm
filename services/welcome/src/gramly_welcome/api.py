from __future__ import annotations

import hmac
import json
import uuid
from time import monotonic
from typing import Annotated

from fastapi import APIRouter, Depends, HTTPException, Request, Response, status
from pydantic import ValidationError
from sqlalchemy import text
from sqlalchemy.exc import SQLAlchemyError
from sqlalchemy.ext.asyncio import AsyncSession

from .config import Settings, get_settings
from .db import session_dependency
from .metrics import WEBHOOK_LATENCY, WEBHOOK_REQUESTS
from .repository import find_active_bot, insert_inbox_event
from .schemas import AcceptedResponse, TelegramUpdate

router = APIRouter()
SessionDep = Annotated[AsyncSession, Depends(session_dependency)]
SettingsDep = Annotated[Settings, Depends(get_settings)]


async def _bounded_json(request: Request, limit: int) -> dict[str, object]:
    declared = request.headers.get("content-length")
    if declared:
        try:
            if int(declared) > limit:
                raise HTTPException(status.HTTP_413_CONTENT_TOO_LARGE, "Webhook body is too large")
        except ValueError as exc:
            raise HTTPException(status.HTTP_400_BAD_REQUEST, "Invalid Content-Length") from exc
    body = bytearray()
    async for chunk in request.stream():
        body.extend(chunk)
        if len(body) > limit:
            raise HTTPException(status.HTTP_413_CONTENT_TOO_LARGE, "Webhook body is too large")
    try:
        value = json.loads(body)
    except (UnicodeDecodeError, json.JSONDecodeError) as exc:
        raise HTTPException(status.HTTP_400_BAD_REQUEST, "Invalid JSON") from exc
    if not isinstance(value, dict):
        raise HTTPException(status.HTTP_400_BAD_REQUEST, "Telegram update must be an object")
    return value


def _secret_valid(request: Request, expected: str) -> bool:
    supplied = request.headers.get("x-telegram-bot-api-secret-token", "")
    return bool(expected) and hmac.compare_digest(supplied, expected)


async def _accept(
    request: Request,
    session: AsyncSession,
    settings: Settings,
    *,
    source_key: str,
    bot_id: int | None,
) -> AcceptedResponse:
    try:
        update = TelegramUpdate.model_validate(await _bounded_json(request, settings.max_webhook_body_bytes))
    except ValidationError as exc:
        raise HTTPException(status.HTTP_400_BAD_REQUEST, "Invalid Telegram update") from exc
    try:
        inserted = await insert_inbox_event(
            session,
            source_key=source_key,
            update_id=update.update_id,
            payload=update.as_payload(),
            bot_id=bot_id,
        )
        await session.commit()
    except SQLAlchemyError as exc:
        await session.rollback()
        raise HTTPException(status.HTTP_503_SERVICE_UNAVAILABLE, "Event store unavailable") from exc
    return AcceptedResponse(duplicate=not inserted)


@router.post("/welcome/webhook/", response_model=AcceptedResponse)
async def interface_webhook(
    request: Request,
    session: SessionDep,
    settings: SettingsDep,
) -> AcceptedResponse:
    started = monotonic()
    result = "rejected"
    try:
        if not settings.accept_webhooks:
            raise HTTPException(status.HTTP_503_SERVICE_UNAVAILABLE, "Webhook ingestion is paused")
        if not _secret_valid(request, settings.interface_webhook_secret):
            raise HTTPException(status.HTTP_403_FORBIDDEN, "Invalid secret token")
        response = await _accept(request, session, settings, source_key="interface", bot_id=None)
        result = "duplicate" if response.duplicate else "accepted"
        return response
    except HTTPException as exc:
        result = "error" if exc.status_code >= 500 else "rejected"
        raise
    except Exception:
        result = "error"
        raise
    finally:
        WEBHOOK_REQUESTS.labels("interface", result).inc()
        WEBHOOK_LATENCY.labels("interface").observe(monotonic() - started)


@router.post("/welcome/client/{public_id}/{path_secret}/", response_model=AcceptedResponse)
async def client_webhook(
    public_id: uuid.UUID,
    path_secret: str,
    request: Request,
    session: SessionDep,
    settings: SettingsDep,
) -> AcceptedResponse:
    started = monotonic()
    result = "rejected"
    try:
        if not settings.accept_webhooks:
            raise HTTPException(status.HTTP_503_SERVICE_UNAVAILABLE, "Webhook ingestion is paused")
        try:
            bot = await find_active_bot(session, public_id)
        except SQLAlchemyError as exc:
            raise HTTPException(status.HTTP_503_SERVICE_UNAVAILABLE, "Event store unavailable") from exc
        if bot is None or not hmac.compare_digest(path_secret, bot.path_secret):
            raise HTTPException(status.HTTP_404_NOT_FOUND)
        if not _secret_valid(request, bot.webhook_secret):
            raise HTTPException(status.HTTP_403_FORBIDDEN, "Invalid secret token")
        response = await _accept(
            request,
            session,
            settings,
            source_key=f"bot:{bot.public_id}",
            bot_id=bot.id,
        )
        result = "duplicate" if response.duplicate else "accepted"
        return response
    except HTTPException as exc:
        result = "error" if exc.status_code >= 500 else "rejected"
        raise
    except Exception:
        result = "error"
        raise
    finally:
        WEBHOOK_REQUESTS.labels("client", result).inc()
        WEBHOOK_LATENCY.labels("client").observe(monotonic() - started)


@router.get("/health/live", include_in_schema=False)
async def live() -> dict[str, str]:
    return {"status": "ok"}


@router.get("/health/ready", include_in_schema=False)
async def ready(session: SessionDep) -> Response:
    try:
        await session.execute(text("SELECT 1"))
    except SQLAlchemyError:
        return Response(status_code=status.HTTP_503_SERVICE_UNAVAILABLE)
    return Response(content='{"status":"ok"}', media_type="application/json")
