from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime
from decimal import Decimal
from typing import Annotated

from fastapi import APIRouter, Depends, Header, HTTPException, Request, Response, status
from pydantic import BaseModel, Field
from sqlalchemy import func, select, update
from sqlalchemy.ext.asyncio import AsyncSession

from .billing import create_crypto_checkout
from .commercial import access_for_owner, list_sellable_plans
from .config import Settings, get_settings
from .content_service import (
    ContentValidationError,
    add_draft_step,
    copy_step,
    delete_step,
    draft_snapshot,
    open_draft,
    publish_draft,
    set_first_delay,
    set_flow_assignments,
    set_step_delay,
)
from .crypto_pay import CryptoPayClient, CryptoPayError
from .db import session_dependency
from .finance import (
    FinanceError,
    available_balance,
    ensure_referral_code,
    record_first_touch,
    request_withdrawal,
)
from .idempotency import IdempotencyConflictError, claim_request, store_response
from .models import (
    Channel,
    Contact,
    ContentAttachment,
    ContentFlow,
    ContentFlowVersion,
    ContentStep,
    DeliveryOperation,
    FinancialLedgerEntry,
    FlowChannelAssignment,
    FlowDelivery,
    ManagedBot,
    ReferralAttribution,
    RotationChannel,
    RotationConversion,
    RotationImpression,
    Withdrawal,
)
from .rotation import set_priority_channel
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


class WithdrawalRequest(BaseModel):
    amount_rub: Decimal = Field(ge=Decimal("1000"), decimal_places=2)


class DraftReorderRequest(BaseModel):
    step_ids: list[int] = Field(min_length=1, max_length=100)


class FlowAssignmentRequest(BaseModel):
    channel_ids: list[int] | None = Field(default=None, max_length=500)


class RotationPriorityRequest(BaseModel):
    priority: bool


class DraftStepRequest(BaseModel):
    text: str = Field(min_length=1, max_length=4096)
    delay_after_seconds: int = Field(default=1, ge=0, le=86_400)


class DraftDelayRequest(BaseModel):
    delay_seconds: int = Field(ge=0, le=86_400)


class DraftStepContentRequest(BaseModel):
    text: str = Field(min_length=1, max_length=4096)


@router.get("/public-config")
async def public_config(settings: SettingsDep) -> dict[str, str]:
    return {"interface_bot_username": settings.interface_bot_username.lstrip("@")}


@dataclass(frozen=True)
class CurrentWebUser:
    auth: AuthenticatedWebSession


async def current_web_user(
    request: Request,
    session: SessionDep,
    settings: SettingsDep,
) -> CurrentWebUser:
    auth = await authenticate_web_session(session, request.cookies.get(settings.mini_app_cookie_name, ""))
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
    if verified.start_param.startswith("ref_"):
        await record_first_touch(session, created.owner.id, verified.start_param[4:])
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


@router.get("/dashboard")
async def dashboard(user: CurrentUserDep, session: SessionDep) -> dict[str, object]:
    owner_id = user.auth.owner.id
    bots = int(
        await session.scalar(
            select(func.count(ManagedBot.id)).where(
                ManagedBot.owner_id == owner_id, ManagedBot.is_active.is_(True)
            )
        )
        or 0
    )
    channels = int(
        await session.scalar(
            select(func.count(Channel.id))
            .join(ManagedBot, ManagedBot.id == Channel.bot_id)
            .where(
                ManagedBot.owner_id == owner_id,
                ManagedBot.is_active.is_(True),
                Channel.is_active.is_(True),
            )
        )
        or 0
    )
    deliveries = (
        await session.execute(
            select(
                func.count(FlowDelivery.id),
                func.count(FlowDelivery.id).filter(FlowDelivery.status == "completed"),
                func.count(FlowDelivery.id).filter(FlowDelivery.status.in_(("failed", "partial"))),
            )
            .join(ManagedBot, ManagedBot.id == FlowDelivery.bot_id)
            .where(ManagedBot.owner_id == owner_id)
        )
    ).one()
    contacts = int(
        await session.scalar(
            select(func.count(Contact.id))
            .join(ManagedBot, ManagedBot.id == Contact.bot_id)
            .where(ManagedBot.owner_id == owner_id)
        )
        or 0
    )
    return {
        "bots": bots,
        "channels": channels,
        "contacts": contacts,
        "deliveries": int(deliveries[0]),
        "delivered": int(deliveries[1]),
        "delivery_errors": int(deliveries[2]),
    }


@router.get("/bots")
async def bots(user: CurrentUserDep, session: SessionDep) -> dict[str, object]:
    rows = (
        await session.execute(
            select(
                ManagedBot,
                func.count(Channel.id.distinct()).filter(Channel.is_active.is_(True)).label("channels"),
                func.count(Contact.id.distinct()).label("contacts"),
            )
            .outerjoin(Channel, Channel.bot_id == ManagedBot.id)
            .outerjoin(Contact, Contact.bot_id == ManagedBot.id)
            .where(ManagedBot.owner_id == user.auth.owner.id, ManagedBot.is_active.is_(True))
            .group_by(ManagedBot.id)
            .order_by(ManagedBot.created_at, ManagedBot.id)
        )
    ).all()
    return {
        "bots": [
            {
                "id": bot.id,
                "public_id": str(bot.public_id),
                "username": bot.username,
                "display_name": bot.display_name,
                "webhook_configured": bot.webhook_configured,
                "auto_approve": bot.auto_approve,
                "welcome_delay_seconds": bot.welcome_delay_seconds,
                "approval_delay_seconds": bot.approval_delay_seconds,
                "channels": int(channel_count),
                "contacts": int(contact_count),
            }
            for bot, channel_count, contact_count in rows
        ]
    }


async def _owned_bot_or_404(session: AsyncSession, owner_id: int, bot_id: int) -> ManagedBot:
    bot = await session.scalar(
        select(ManagedBot).where(
            ManagedBot.id == bot_id,
            ManagedBot.owner_id == owner_id,
            ManagedBot.is_active.is_(True),
        )
    )
    if bot is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "Bot not found")
    return bot


@router.get("/bots/{bot_id}")
async def bot_detail(bot_id: int, user: CurrentUserDep, session: SessionDep) -> dict[str, object]:
    bot = await _owned_bot_or_404(session, user.auth.owner.id, bot_id)
    channels = list(
        (
            await session.scalars(
                select(Channel)
                .where(Channel.bot_id == bot.id, Channel.is_active.is_(True))
                .order_by(Channel.title, Channel.id)
            )
        ).all()
    )
    rotation = {
        row.channel_id: row
        for row in await session.scalars(
            select(RotationChannel).where(RotationChannel.owner_id == user.auth.owner.id)
        )
    }
    return {
        "bot": {
            "id": bot.id,
            "username": bot.username,
            "display_name": bot.display_name,
            "webhook_configured": bot.webhook_configured,
            "auto_approve": bot.auto_approve,
            "welcome_delay_seconds": bot.welcome_delay_seconds,
            "approval_delay_seconds": bot.approval_delay_seconds,
        },
        "channels": [
            {
                "id": channel.id,
                "telegram_id": channel.telegram_id,
                "title": channel.title,
                "username": channel.username,
                "can_invite_users": channel.can_invite_users,
                "rotation_enabled": bool(rotation.get(channel.id) and rotation[channel.id].is_enabled),
                "rotation_priority": bool(rotation.get(channel.id) and rotation[channel.id].is_priority),
            }
            for channel in channels
        ],
    }


@router.get("/bots/{bot_id}/flows")
async def bot_flows(bot_id: int, user: CurrentUserDep, session: SessionDep) -> dict[str, object]:
    await _owned_bot_or_404(session, user.auth.owner.id, bot_id)
    flows = list(
        (
            await session.scalars(
                select(ContentFlow)
                .where(ContentFlow.bot_id == bot_id, ContentFlow.is_active.is_(True))
                .order_by(ContentFlow.kind, ContentFlow.id)
            )
        ).all()
    )
    result: list[dict[str, object]] = []
    for flow in flows:
        versions = list(
            (
                await session.scalars(
                    select(ContentFlowVersion)
                    .where(ContentFlowVersion.flow_id == flow.id)
                    .order_by(ContentFlowVersion.version.desc())
                )
            ).all()
        )
        assignments = list(
            await session.scalars(
                select(FlowChannelAssignment.channel_id).where(FlowChannelAssignment.flow_id == flow.id)
            )
        )
        result.append(
            {
                "id": flow.id,
                "name": flow.name,
                "kind": flow.kind,
                "assignment_mode": flow.assignment_mode,
                "channel_ids": assignments,
                "versions": [
                    {
                        "id": version.id,
                        "number": version.version,
                        "status": version.status,
                        "first_delay_seconds": version.first_delay_seconds,
                        "published_at": version.published_at,
                    }
                    for version in versions
                ],
            }
        )
    return {"flows": result}


@router.get("/flows/{flow_id}/draft")
async def flow_draft(flow_id: int, user: CurrentUserDep, session: SessionDep) -> dict[str, object]:
    try:
        version = await open_draft(session, user.auth.owner.id, flow_id, user.auth.owner.telegram_id)
        snapshot = await draft_snapshot(session, user.auth.owner.id, version.id)
    except ContentValidationError as exc:
        raise HTTPException(status.HTTP_404_NOT_FOUND, str(exc)) from exc
    attachments = (
        list(
            (
                await session.scalars(
                    select(ContentAttachment)
                    .where(ContentAttachment.step_id.in_([step.id for step in snapshot.steps]))
                    .order_by(ContentAttachment.step_id, ContentAttachment.position)
                )
            ).all()
        )
        if snapshot.steps
        else []
    )
    by_step: dict[int, list[dict[str, object]]] = {}
    for attachment in attachments:
        by_step.setdefault(attachment.step_id, []).append(
            {
                "id": attachment.id,
                "type": attachment.media_type,
                "name": attachment.original_name,
                "size": attachment.size,
            }
        )
    return {
        "flow": {"id": snapshot.flow.id, "name": snapshot.flow.name, "kind": snapshot.flow.kind},
        "version": {
            "id": snapshot.version.id,
            "number": snapshot.version.version,
            "first_delay_seconds": snapshot.version.first_delay_seconds,
        },
        "steps": [
            {
                "id": step.id,
                "position": step.position,
                "payload": step.payload,
                "delay_after_seconds": step.delay_after_seconds,
                "attachments": by_step.get(step.id, []),
            }
            for step in snapshot.steps
        ],
    }


@router.post("/drafts/{version_id}/reorder")
async def reorder_draft(
    version_id: int,
    payload: DraftReorderRequest,
    user: CsrfUserDep,
    session: SessionDep,
    idempotency_key: Annotated[str, Header(alias="Idempotency-Key")],
) -> dict[str, object]:
    request_payload = {"version_id": version_id, "step_ids": payload.step_ids}
    try:
        stored = await claim_request(
            session, owner_id=user.auth.owner.id, key=idempotency_key, payload=request_payload
        )
        if stored is not None:
            return stored.body
        snapshot = await draft_snapshot(session, user.auth.owner.id, version_id)
        current_ids = [step.id for step in snapshot.steps]
        if len(set(payload.step_ids)) != len(payload.step_ids) or set(payload.step_ids) != set(current_ids):
            raise ContentValidationError("Step list must contain every draft step exactly once")
        for index, step_id in enumerate(payload.step_ids):
            await session.execute(
                update(ContentStep).where(ContentStep.id == step_id).values(position=1_000_000 + index)
            )
        await session.flush()
        for index, step_id in enumerate(payload.step_ids):
            await session.execute(update(ContentStep).where(ContentStep.id == step_id).values(position=index))
        await session.commit()
        body: dict[str, object] = {"version_id": version_id, "step_ids": payload.step_ids}
        await store_response(session, owner_id=user.auth.owner.id, key=idempotency_key, status=200, body=body)
        return body
    except IdempotencyConflictError as exc:
        raise HTTPException(status.HTTP_409_CONFLICT, str(exc)) from exc
    except ContentValidationError as exc:
        raise HTTPException(status.HTTP_422_UNPROCESSABLE_CONTENT, str(exc)) from exc


@router.post("/drafts/{version_id}/steps", status_code=status.HTTP_201_CREATED)
async def create_draft_step(
    version_id: int,
    payload: DraftStepRequest,
    user: CsrfUserDep,
    session: SessionDep,
    idempotency_key: Annotated[str, Header(alias="Idempotency-Key")],
) -> dict[str, object]:
    request_payload = {"version_id": version_id, **payload.model_dump()}
    try:
        stored = await claim_request(
            session, owner_id=user.auth.owner.id, key=idempotency_key, payload=request_payload
        )
        if stored is not None:
            return stored.body
        step = await add_draft_step(
            session,
            user.auth.owner.id,
            version_id,
            {"text": payload.text, "entities": []},
            [],
            delay_after_seconds=payload.delay_after_seconds,
        )
        body: dict[str, object] = {"id": step.id, "position": step.position}
        await store_response(
            session, owner_id=user.auth.owner.id, key=idempotency_key, status=201, body=body
        )
        return body
    except IdempotencyConflictError as exc:
        raise HTTPException(status.HTTP_409_CONFLICT, str(exc)) from exc
    except ContentValidationError as exc:
        raise HTTPException(status.HTTP_422_UNPROCESSABLE_CONTENT, str(exc)) from exc


@router.post("/drafts/{version_id}/first-delay")
async def update_first_delay(
    version_id: int,
    payload: DraftDelayRequest,
    user: CsrfUserDep,
    session: SessionDep,
    _idempotency_key: Annotated[str, Header(alias="Idempotency-Key")],
) -> dict[str, object]:
    try:
        await set_first_delay(session, user.auth.owner.id, version_id, payload.delay_seconds)
    except ContentValidationError as exc:
        raise HTTPException(status.HTTP_422_UNPROCESSABLE_CONTENT, str(exc)) from exc
    return {"version_id": version_id, "delay_seconds": payload.delay_seconds}


@router.post("/steps/{step_id}/delay")
async def update_step_delay(
    step_id: int,
    payload: DraftDelayRequest,
    user: CsrfUserDep,
    session: SessionDep,
    _idempotency_key: Annotated[str, Header(alias="Idempotency-Key")],
) -> dict[str, object]:
    try:
        await set_step_delay(session, user.auth.owner.id, step_id, payload.delay_seconds)
    except ContentValidationError as exc:
        raise HTTPException(status.HTTP_422_UNPROCESSABLE_CONTENT, str(exc)) from exc
    return {"step_id": step_id, "delay_seconds": payload.delay_seconds}


@router.post("/steps/{step_id}/content")
async def update_step_content(
    step_id: int,
    payload: DraftStepContentRequest,
    user: CsrfUserDep,
    session: SessionDep,
    _idempotency_key: Annotated[str, Header(alias="Idempotency-Key")],
) -> dict[str, object]:
    result = await session.execute(
        update(ContentStep)
        .where(
            ContentStep.id == step_id,
            ContentStep.version_id.in_(
                select(ContentFlowVersion.id)
                .join(ContentFlow, ContentFlow.id == ContentFlowVersion.flow_id)
                .join(ManagedBot, ManagedBot.id == ContentFlow.bot_id)
                .where(
                    ContentFlowVersion.status == "draft",
                    ManagedBot.owner_id == user.auth.owner.id,
                )
            ),
        )
        .values(payload={"text": payload.text, "entities": []})
        .returning(ContentStep.id)
    )
    if result.scalar_one_or_none() is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "Editable step not found")
    await session.commit()
    return {"step_id": step_id, "updated": True}


@router.post("/steps/{step_id}/copy", status_code=status.HTTP_201_CREATED)
async def copy_draft_step(
    step_id: int,
    user: CsrfUserDep,
    session: SessionDep,
    idempotency_key: Annotated[str, Header(alias="Idempotency-Key")],
) -> dict[str, object]:
    try:
        stored = await claim_request(
            session,
            owner_id=user.auth.owner.id,
            key=idempotency_key,
            payload={"step_id": step_id, "action": "copy"},
        )
        if stored is not None:
            return stored.body
        row = await copy_step(session, user.auth.owner.id, step_id)
        body: dict[str, object] = {"id": row.id, "position": row.position}
        await store_response(
            session, owner_id=user.auth.owner.id, key=idempotency_key, status=201, body=body
        )
    except ContentValidationError as exc:
        raise HTTPException(status.HTTP_422_UNPROCESSABLE_CONTENT, str(exc)) from exc
    except IdempotencyConflictError as exc:
        raise HTTPException(status.HTTP_409_CONFLICT, str(exc)) from exc
    return body


@router.delete("/steps/{step_id}", status_code=status.HTTP_204_NO_CONTENT)
async def delete_draft_step(
    step_id: int,
    user: CsrfUserDep,
    session: SessionDep,
    idempotency_key: Annotated[str, Header(alias="Idempotency-Key")],
) -> Response:
    try:
        stored = await claim_request(
            session,
            owner_id=user.auth.owner.id,
            key=idempotency_key,
            payload={"step_id": step_id, "action": "delete"},
        )
        if stored is not None:
            return Response(status_code=stored.status)
        await delete_step(session, user.auth.owner.id, step_id)
        await store_response(
            session,
            owner_id=user.auth.owner.id,
            key=idempotency_key,
            status=204,
            body={},
        )
    except ContentValidationError as exc:
        raise HTTPException(status.HTTP_422_UNPROCESSABLE_CONTENT, str(exc)) from exc
    except IdempotencyConflictError as exc:
        raise HTTPException(status.HTTP_409_CONFLICT, str(exc)) from exc
    return Response(status_code=status.HTTP_204_NO_CONTENT)


@router.post("/drafts/{version_id}/publish")
async def publish_flow_draft(
    version_id: int,
    user: CsrfUserDep,
    session: SessionDep,
    idempotency_key: Annotated[str, Header(alias="Idempotency-Key")],
) -> dict[str, object]:
    request_payload = {"version_id": version_id, "action": "publish"}
    try:
        stored = await claim_request(
            session, owner_id=user.auth.owner.id, key=idempotency_key, payload=request_payload
        )
        if stored is not None:
            return stored.body
        version = await publish_draft(session, user.auth.owner.id, version_id)
        body: dict[str, object] = {"version_id": version.id, "status": version.status}
        await store_response(session, owner_id=user.auth.owner.id, key=idempotency_key, status=200, body=body)
        return body
    except IdempotencyConflictError as exc:
        raise HTTPException(status.HTTP_409_CONFLICT, str(exc)) from exc
    except ContentValidationError as exc:
        raise HTTPException(status.HTTP_422_UNPROCESSABLE_CONTENT, str(exc)) from exc


@router.post("/flows/{flow_id}/assignments")
async def update_flow_assignments(
    flow_id: int,
    payload: FlowAssignmentRequest,
    user: CsrfUserDep,
    session: SessionDep,
    idempotency_key: Annotated[str, Header(alias="Idempotency-Key")],
) -> dict[str, object]:
    request_payload = {"flow_id": flow_id, "channel_ids": payload.channel_ids}
    try:
        stored = await claim_request(
            session, owner_id=user.auth.owner.id, key=idempotency_key, payload=request_payload
        )
        if stored is not None:
            return stored.body
        await set_flow_assignments(session, user.auth.owner.id, flow_id, payload.channel_ids)
        body: dict[str, object] = {
            "flow_id": flow_id,
            "assignment_mode": "all" if payload.channel_ids is None else "selected",
            "channel_ids": payload.channel_ids or [],
        }
        await store_response(session, owner_id=user.auth.owner.id, key=idempotency_key, status=200, body=body)
        return body
    except IdempotencyConflictError as exc:
        raise HTTPException(status.HTTP_409_CONFLICT, str(exc)) from exc
    except ContentValidationError as exc:
        raise HTTPException(status.HTTP_422_UNPROCESSABLE_CONTENT, str(exc)) from exc


@router.post("/channels/{channel_id}/rotation-priority")
async def rotation_priority(
    channel_id: int,
    payload: RotationPriorityRequest,
    user: CsrfUserDep,
    session: SessionDep,
    idempotency_key: Annotated[str, Header(alias="Idempotency-Key")],
) -> dict[str, object]:
    request_payload = {"channel_id": channel_id, "priority": payload.priority}
    try:
        stored = await claim_request(
            session, owner_id=user.auth.owner.id, key=idempotency_key, payload=request_payload
        )
        if stored is not None:
            return stored.body
        await set_priority_channel(
            session, owner_id=user.auth.owner.id, channel_id=channel_id, priority=payload.priority
        )
        body: dict[str, object] = {"channel_id": channel_id, "priority": payload.priority}
        await store_response(session, owner_id=user.auth.owner.id, key=idempotency_key, status=200, body=body)
        return body
    except IdempotencyConflictError as exc:
        raise HTTPException(status.HTTP_409_CONFLICT, str(exc)) from exc
    except ValueError as exc:
        raise HTTPException(status.HTTP_422_UNPROCESSABLE_CONTENT, str(exc)) from exc


@router.get("/analytics")
async def analytics(user: CurrentUserDep, session: SessionDep) -> dict[str, object]:
    owner_id = user.auth.owner.id
    deliveries = (
        await session.execute(
            select(FlowDelivery.status, func.count(FlowDelivery.id))
            .join(ManagedBot, ManagedBot.id == FlowDelivery.bot_id)
            .where(ManagedBot.owner_id == owner_id)
            .group_by(FlowDelivery.status)
        )
    ).all()
    operations = (
        await session.execute(
            select(DeliveryOperation.status, func.count(DeliveryOperation.id))
            .join(FlowDelivery, FlowDelivery.id == DeliveryOperation.flow_delivery_id)
            .join(ManagedBot, ManagedBot.id == FlowDelivery.bot_id)
            .where(ManagedBot.owner_id == owner_id)
            .group_by(DeliveryOperation.status)
        )
    ).all()
    impressions = int(
        await session.scalar(
            select(func.count(RotationImpression.id)).where(
                RotationImpression.destination_owner_id == owner_id
            )
        )
        or 0
    )
    conversions = int(
        await session.scalar(
            select(func.count(RotationConversion.id))
            .join(
                RotationImpression,
                RotationImpression.id == RotationConversion.impression_id,
            )
            .where(RotationImpression.destination_owner_id == owner_id)
        )
        or 0
    )
    return {
        "deliveries": {status_value: int(count) for status_value, count in deliveries},
        "operations": {status_value: int(count) for status_value, count in operations},
        "rotation": {"impressions": impressions, "conversions": conversions},
    }


@router.get("/referrals")
async def referrals(user: CurrentUserDep, session: SessionDep, settings: SettingsDep) -> dict[str, object]:
    owner = user.auth.owner
    code = await ensure_referral_code(session, owner.id)
    active = int(
        await session.scalar(
            select(func.count(ReferralAttribution.id)).where(
                ReferralAttribution.referrer_owner_id == owner.id,
                ReferralAttribution.status == "active",
            )
        )
        or 0
    )
    ledger = list(
        (
            await session.scalars(
                select(FinancialLedgerEntry)
                .where(FinancialLedgerEntry.owner_id == owner.id)
                .order_by(FinancialLedgerEntry.created_at.desc())
                .limit(50)
            )
        ).all()
    )
    return {
        "code": code.code,
        "url": f"https://t.me/{settings.interface_bot_username}?start=ref_{code.code}",
        "active_referrals": active,
        "balance_rub": str(await available_balance(session, owner.id)),
        "ledger": [
            {
                "id": row.id,
                "type": row.entry_type,
                "amount_rub": str(row.amount_rub),
                "rate_percent": str(row.rate_percent) if row.rate_percent is not None else None,
                "created_at": row.created_at,
            }
            for row in ledger
        ],
    }


@router.get("/withdrawals")
async def withdrawals(user: CurrentUserDep, session: SessionDep) -> dict[str, object]:
    rows = list(
        (
            await session.scalars(
                select(Withdrawal)
                .where(Withdrawal.owner_id == user.auth.owner.id)
                .order_by(Withdrawal.created_at.desc())
                .limit(50)
            )
        ).all()
    )
    return {
        "withdrawals": [
            {
                "id": str(row.public_id),
                "amount_rub": str(row.requested_rub),
                "status": row.status,
                "created_at": row.created_at,
            }
            for row in rows
        ]
    }


@router.post("/payments/crypto")
async def crypto_checkout(
    user: CsrfUserDep,
    session: SessionDep,
    settings: SettingsDep,
    idempotency_key: Annotated[str, Header(alias="Idempotency-Key")],
) -> dict[str, object]:
    payload = {"plan": "business", "provider": "crypto_pay", "surface": "mini_app"}
    try:
        stored = await claim_request(
            session, owner_id=user.auth.owner.id, key=idempotency_key, payload=payload
        )
        if stored is not None:
            return stored.body
        client = CryptoPayClient(settings.crypto_pay_api_token, settings.crypto_pay_api_base_url)
        payment = await create_crypto_checkout(session, user.auth.owner.id, client, surface="mini_app")
        body: dict[str, object] = {
            "payment_id": payment.id,
            "invoice_url": payment.invoice_url,
            "status": payment.status,
        }
        await store_response(
            session,
            owner_id=user.auth.owner.id,
            key=idempotency_key,
            status=200,
            body=body,
        )
        return body
    except IdempotencyConflictError as exc:
        raise HTTPException(status.HTTP_409_CONFLICT, str(exc)) from exc
    except (FinanceError, CryptoPayError) as exc:
        raise HTTPException(status.HTTP_503_SERVICE_UNAVAILABLE, str(exc)) from exc


@router.post("/withdrawals", status_code=status.HTTP_201_CREATED)
async def create_withdrawal(
    payload: WithdrawalRequest,
    user: CsrfUserDep,
    session: SessionDep,
    idempotency_key: Annotated[str, Header(alias="Idempotency-Key")],
) -> dict[str, object]:
    request_payload = {"amount_rub": str(payload.amount_rub)}
    try:
        stored = await claim_request(
            session,
            owner_id=user.auth.owner.id,
            key=idempotency_key,
            payload=request_payload,
        )
        if stored is not None:
            return stored.body
        withdrawal = await request_withdrawal(
            session,
            user.auth.owner.id,
            payload.amount_rub,
            user.auth.owner.telegram_id,
        )
        body: dict[str, object] = {
            "id": str(withdrawal.public_id),
            "amount_rub": str(withdrawal.requested_rub),
            "status": withdrawal.status,
        }
        await store_response(
            session,
            owner_id=user.auth.owner.id,
            key=idempotency_key,
            status=201,
            body=body,
        )
        return body
    except IdempotencyConflictError as exc:
        raise HTTPException(status.HTTP_409_CONFLICT, str(exc)) from exc
    except FinanceError as exc:
        raise HTTPException(status.HTTP_422_UNPROCESSABLE_CONTENT, str(exc)) from exc


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
