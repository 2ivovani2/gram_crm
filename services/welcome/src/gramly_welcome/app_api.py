from __future__ import annotations

import csv
import io
import logging
import uuid
from copy import deepcopy
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from decimal import Decimal
from typing import Annotated

from aiogram import Bot
from aiogram.exceptions import TelegramAPIError, TelegramForbiddenError
from aiogram.types import LabeledPrice
from fastapi import APIRouter, Depends, Header, HTTPException, Request, Response, status
from pydantic import BaseModel, Field
from sqlalchemy import func, select, update
from sqlalchemy.dialects.postgresql import insert as pg_insert
from sqlalchemy.ext.asyncio import AsyncSession

from .billing import create_crypto_checkout, create_stars_checkout
from .broadcasts import cancel_broadcast, launch_broadcast, save_broadcast
from .campaigns import record_campaign_touch
from .commercial import access_for_owner, list_sellable_plans
from .config import Settings, get_settings
from .content_service import (
    ContentValidationError,
    add_draft_step,
    copy_step,
    delete_step,
    draft_snapshot,
    flow_timeline_seconds,
    open_draft,
    publish_draft,
    set_chain_button_style,
    set_first_delay,
    set_flow_assignments,
    set_step_delay,
)
from .crypto import TokenDecryptionError, TokenKeyring
from .crypto_pay import CryptoPayClient, CryptoPayError
from .db import session_dependency
from .delays import MAX_DELAY_SECONDS
from .finance import (
    FinanceError,
    available_balance,
    ensure_referral_code,
    record_first_touch,
    request_withdrawal,
)
from .idempotency import IdempotencyConflictError, claim_request, store_response
from .join_activation import (
    ActivationContext,
    activation_token_valid,
    grant_activation,
    load_activation_context,
    long_activation_enabled,
)
from .join_request_policy import JOIN_REQUEST_MAX_TIMELINE_SECONDS
from .message_catalog import install_template, template_view
from .metrics import JOIN_REQUEST_ACTIVATIONS, TEMPLATE_INSTALLS
from .models import (
    Broadcast,
    BroadcastRecipient,
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
    LearningVideo,
    LearningVideoProgress,
    ManagedBot,
    MessageTemplate,
    Payment,
    Plan,
    ReferralAttribution,
    RotationChannel,
    RotationConversion,
    RotationImpression,
    Withdrawal,
)
from .required_channel import check_required_membership
from .rotation import set_priority_channel
from .telegram_auth import TelegramInitDataError, verify_init_data
from .telegram_delivery import probe_persistent_write
from .web_sessions import (
    AuthenticatedWebSession,
    authenticate_web_session,
    create_web_session,
    csrf_token_valid,
    revoke_web_session,
)

router = APIRouter(prefix="/api/v1", tags=["mini-app"])
logger = logging.getLogger(__name__)
SessionDep = Annotated[AsyncSession, Depends(session_dependency)]
SettingsDep = Annotated[Settings, Depends(get_settings)]


class TelegramSessionRequest(BaseModel):
    init_data: str = Field(min_length=1, max_length=16_384)


class TelegramSessionResponse(BaseModel):
    csrf_token: str
    expires_at: datetime


class ActivationSessionRequest(BaseModel):
    bot_public_id: uuid.UUID
    token: str = Field(min_length=32, max_length=128)
    init_data: str = Field(min_length=1, max_length=16_384)


class WithdrawalRequest(BaseModel):
    amount_rub: Decimal = Field(ge=Decimal("1000"), decimal_places=2)


class DraftReorderRequest(BaseModel):
    step_ids: list[int] = Field(min_length=1, max_length=100)


class FlowAssignmentRequest(BaseModel):
    channel_ids: list[int] | None = Field(default=None, max_length=500)


class CatalogInstallRequest(BaseModel):
    bot_id: int = Field(gt=0)


class RotationPriorityRequest(BaseModel):
    priority: bool


class DraftStepRequest(BaseModel):
    text: str = Field(min_length=1, max_length=4096)
    delay_after_seconds: int = Field(default=1, ge=0, le=MAX_DELAY_SECONDS)


class DraftDelayRequest(BaseModel):
    delay_seconds: int = Field(ge=0, le=MAX_DELAY_SECONDS)


class DraftButtonStyleRequest(BaseModel):
    style: str = Field(max_length=16)


class DraftStepContentRequest(BaseModel):
    text: str = Field(min_length=1, max_length=4096)


class BroadcastSaveRequest(BaseModel):
    bot_id: int = Field(gt=0)
    source_channel_id: int | None = Field(default=None, gt=0)
    name: str = Field(min_length=1, max_length=128)
    content: dict[str, object]


class BroadcastLaunchRequest(BaseModel):
    scheduled_at: datetime | None = None


class LearningProgressRequest(BaseModel):
    position_seconds: int = Field(ge=0, le=86_400)
    event: str = Field(default="progress", pattern=r"^(progress|ended)$")


LEARNING_CHAPTERS = (
    ("first_launch", "Первый запуск"),
    ("automation", "Автоматизация и рост"),
    ("account", "Аккаунт"),
)


@router.get("/public-config")
async def public_config(settings: SettingsDep) -> dict[str, str]:
    return {"interface_bot_username": settings.interface_bot_username.lstrip("@")}


async def _verified_activation(
    payload: ActivationSessionRequest,
    session: AsyncSession,
    settings: Settings,
) -> ActivationContext:
    try:
        activation_public_id = uuid.UUID(hex=payload.token.split("_", 1)[0])
    except ValueError as exc:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "Активация не найдена") from exc
    context = await load_activation_context(session, public_id=activation_public_id)
    if (
        context is None
        or context.bot.public_id != payload.bot_public_id
        or not activation_token_valid(context.activation, context.bot, payload.token)
        or not await long_activation_enabled(session, context.bot.id)
    ):
        raise HTTPException(status.HTTP_404_NOT_FOUND, "Активация не найдена")
    if context.activation.expires_at <= datetime.now(UTC):
        raise HTTPException(status.HTTP_410_GONE, "Ссылка активации истекла")
    try:
        bot_token = TokenKeyring.parse(settings.token_encryption_keys).decrypt(
            context.bot.token_ciphertext
        )
        verified = verify_init_data(
            payload.init_data,
            bot_token,
            max_age_seconds=settings.mini_app_auth_max_age_seconds,
        )
    except (TelegramInitDataError, TokenDecryptionError) as exc:
        raise HTTPException(status.HTTP_401_UNAUTHORIZED, "Telegram не подтвердил пользователя") from exc
    if verified.user.id != context.contact.telegram_id:
        raise HTTPException(status.HTTP_403_FORBIDDEN, "Эта заявка принадлежит другому пользователю")
    return context


@router.post("/activation/session")
async def activation_session(
    payload: ActivationSessionRequest,
    session: SessionDep,
    settings: SettingsDep,
) -> dict[str, object]:
    context = await _verified_activation(payload, session, settings)
    return {
        "status": context.activation.status,
        "channel_title": context.channel.title,
        "bot_username": context.bot.username,
        "fallback_url": f"https://t.me/{context.bot.username}?start=gramly_{payload.token}",
        "already_granted": context.activation.granted_at is not None,
    }


@router.post("/activation/confirm")
async def confirm_activation(
    payload: ActivationSessionRequest,
    session: SessionDep,
    settings: SettingsDep,
) -> dict[str, object]:
    context = await _verified_activation(payload, session, settings)
    if context.activation.granted_at is not None:
        return {"status": "granted", "delivery_id": context.activation.delivery_id}
    keyring = TokenKeyring.parse(settings.token_encryption_keys)
    try:
        await probe_persistent_write(context, keyring)
    except TelegramForbiddenError as exc:
        raise HTTPException(
            status.HTTP_409_CONFLICT,
            "Telegram ещё не разрешил боту отправлять сообщения",
        ) from exc
    except TelegramAPIError as exc:
        raise HTTPException(
            status.HTTP_503_SERVICE_UNAVAILABLE,
            "Telegram временно недоступен — попробуйте ещё раз",
        ) from exc
    await session.commit()
    locked_context = await load_activation_context(
        session, public_id=context.activation.public_id, for_update=True
    )
    if locked_context is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "Активация не найдена")
    delivery_id = await grant_activation(
        session,
        context=locked_context,
        event_key=f"join-request-activated:{locked_context.request.telegram_update_id}",
    )
    await session.commit()
    JOIN_REQUEST_ACTIVATIONS.labels("granted").inc()
    return {"status": "granted", "delivery_id": delivery_id}


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


AuthenticatedUserDep = Annotated[CurrentWebUser, Depends(current_web_user)]


async def required_channel_user(
    session: SessionDep,
    settings: SettingsDep,
    user: AuthenticatedUserDep,
) -> CurrentWebUser:
    async with Bot(settings.interface_bot_token) as bot:
        membership = await check_required_membership(session, bot, user.auth.owner)
    if not membership.allowed:
        raise HTTPException(
            status.HTTP_403_FORBIDDEN,
            {
                "code": "news_subscription_required",
                "title": membership.config.title,
                "url": membership.config.url,
                "temporarily_unavailable": membership.temporarily_unavailable,
            },
        )
    return user


CurrentUserDep = Annotated[CurrentWebUser, Depends(required_channel_user)]


async def csrf_protected_user(
    session: SessionDep,
    user: CurrentUserDep,
    csrf_token: Annotated[str, Header(alias="X-CSRF-Token")],
) -> CurrentWebUser:
    if not await csrf_token_valid(session, user.auth.session_id, csrf_token):
        raise HTTPException(status.HTTP_403_FORBIDDEN, "Invalid CSRF token")
    return user


CsrfUserDep = Annotated[CurrentWebUser, Depends(csrf_protected_user)]


@router.post("/news-subscription/check")
async def news_subscription_check(
    session: SessionDep,
    settings: SettingsDep,
    user: AuthenticatedUserDep,
    csrf_token: Annotated[str, Header(alias="X-CSRF-Token")],
) -> dict[str, object]:
    if not await csrf_token_valid(session, user.auth.session_id, csrf_token):
        raise HTTPException(status.HTTP_403_FORBIDDEN, "Invalid CSRF token")
    async with Bot(settings.interface_bot_token) as bot:
        membership = await check_required_membership(session, bot, user.auth.owner, force=True)
    return {
        "allowed": membership.allowed,
        "title": membership.config.title,
        "url": membership.config.url,
        "temporarily_unavailable": membership.temporarily_unavailable,
    }


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
    elif verified.start_param.startswith("cmp_"):
        await record_campaign_touch(session, created.owner.id, verified.start_param[4:])
    response.set_cookie(
        settings.mini_app_cookie_name,
        created.token,
        max_age=settings.mini_app_session_seconds,
        secure=settings.mini_app_cookie_secure,
        httponly=True,
        samesite="strict",
        path="/",
    )
    # Readable by same-origin clients so every open Mini App uses the token
    # paired with the shared session cookie. Authentication stays HttpOnly.
    response.set_cookie(
        "gramly_hello_csrf",
        created.csrf_token,
        max_age=settings.mini_app_session_seconds,
        secure=settings.mini_app_cookie_secure,
        httponly=False,
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


@router.post("/drafts/{version_id}/button-style")
async def draft_button_style(version_id: int, payload: DraftButtonStyleRequest, user: CsrfUserDep, session: SessionDep) -> dict[str, str]:
    try:
        await set_chain_button_style(session, user.auth.owner.id, version_id, payload.style)
    except ContentValidationError as exc:
        await session.rollback()
        raise HTTPException(422, str(exc)) from exc
    return {"style": payload.style}


@router.get("/catalog")
async def catalog(user: CurrentUserDep, session: SessionDep, kind: str = "welcome", tag: str = "", page: int = 1) -> dict[str, object]:
    if kind not in {"welcome", "farewell"} or page < 1 or page > 10000 or len(tag) > 80:
        raise HTTPException(422, "Некорректный фильтр каталога")
    query = select(MessageTemplate).where(MessageTemplate.is_active.is_(True), MessageTemplate.kind == kind)
    all_tags = list(await session.scalars(select(MessageTemplate.tags).where(MessageTemplate.is_active.is_(True))))
    if tag:
        query = query.where(MessageTemplate.tags.contains([tag]))
    total = await session.scalar(select(func.count()).select_from(query.subquery()))
    rows = list(await session.scalars(query.order_by(MessageTemplate.id).offset((page - 1) * 5).limit(5)))
    return {"items": [template_view(row) for row in rows], "page": page, "total": total,
            "tags": sorted({item for tags in all_tags for item in tags})}


@router.get("/catalog/{template_id}")
async def catalog_detail(template_id: int, user: CurrentUserDep, session: SessionDep) -> dict[str, object]:
    template = await session.get(MessageTemplate, template_id)
    if template is None or not template.is_active:
        raise HTTPException(404, "Шаблон недоступен")
    return template_view(template)


@router.post("/catalog/{template_id}/install", status_code=201)
async def catalog_install(
    template_id: int,
    payload: CatalogInstallRequest,
    request: Request,
    user: CsrfUserDep,
    session: SessionDep,
    idempotency_key: Annotated[str, Header(alias="Idempotency-Key")],
) -> dict[str, object]:
    try:
        result = await install_template(session, owner_id=user.auth.owner.id, bot_id=payload.bot_id,
                                        template_id=template_id, key=idempotency_key)
        TEMPLATE_INSTALLS.labels("installed").inc()
        return result
    except IdempotencyConflictError as exc:
        await session.rollback()
        TEMPLATE_INSTALLS.labels("conflict").inc()
        raise HTTPException(409, str(exc)) from exc
    except ValueError as exc:
        await session.rollback()
        TEMPLATE_INSTALLS.labels("invalid").inc()
        raise HTTPException(422, str(exc)) from exc
    except Exception:
        await session.rollback()
        TEMPLATE_INSTALLS.labels("failed").inc()
        logger.exception(
            "catalog_install_failed request_id=%s owner_id=%s bot_id=%s template_id=%s",
            getattr(request.state, "request_id", ""),
            user.auth.owner.id,
            payload.bot_id,
            template_id,
        )
        raise


@router.get("/learning")
async def learning_resources(user: CurrentUserDep, session: SessionDep) -> dict[str, object]:
    videos = list(
        await session.scalars(
            select(LearningVideo)
            .where(LearningVideo.is_active.is_(True))
            .order_by(LearningVideo.sort_order, LearningVideo.id)
        )
    )
    progress_rows = list(
        await session.scalars(
            select(LearningVideoProgress).where(
                LearningVideoProgress.owner_id == user.auth.owner.id
            )
        )
    )
    progress_by_video = {row.video_id: row for row in progress_rows}
    completed = sum(
        1
        for video in videos
        if (row := progress_by_video.get(video.id)) is not None and row.completed_at is not None
    )
    next_video = next(
        (
            video
            for video in videos
            if (row := progress_by_video.get(video.id)) is None or row.completed_at is None
        ),
        videos[0] if videos else None,
    )
    return {
        "guide": {
            "completed": user.auth.owner.guide_completed,
            "step": user.auth.owner.guide_step,
        },
        "progress": {
            "completed": completed,
            "total": len(videos),
            "next_video_id": next_video.id if next_video is not None else None,
        },
        "chapters": [
            {"key": key, "title": title}
            for key, title in LEARNING_CHAPTERS
            if any(video.chapter_key == key for video in videos)
        ],
        "videos": [
            {
                "id": row.id,
                "slug": row.slug,
                "title": row.title,
                "description": row.description,
                "video_url": row.video_url,
                "poster_url": row.poster_url,
                "duration_seconds": row.duration_seconds,
                "feature_key": row.feature_key,
                "chapter_key": row.chapter_key,
                "position_seconds": progress_by_video[row.id].position_seconds
                if row.id in progress_by_video
                else 0,
                "completed_at": progress_by_video[row.id].completed_at
                if row.id in progress_by_video
                else None,
            }
            for row in videos
        ],
    }


@router.patch("/learning/{video_id}/progress")
async def update_learning_progress(
    video_id: int,
    payload: LearningProgressRequest,
    user: CsrfUserDep,
    session: SessionDep,
) -> dict[str, object]:
    video = await session.scalar(
        select(LearningVideo).where(
            LearningVideo.id == video_id,
            LearningVideo.is_active.is_(True),
        )
    )
    if video is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "Урок не найден")
    duration = max(0, int(video.duration_seconds))
    position = min(payload.position_seconds, duration) if duration else payload.position_seconds
    completion_threshold = max(1, (duration * 9 + 9) // 10) if duration else None
    completed_now = payload.event == "ended" or (
        completion_threshold is not None and position >= completion_threshold
    )
    completed_at = datetime.now(UTC) if completed_now else None
    statement = (
        pg_insert(LearningVideoProgress)
        .values(
            owner_id=user.auth.owner.id,
            video_id=video.id,
            position_seconds=position,
            completed_at=completed_at,
        )
        .on_conflict_do_update(
            constraint="uq_learning_video_progress_owner_video",
            set_={
                "position_seconds": func.greatest(
                    LearningVideoProgress.position_seconds,
                    position,
                ),
                "completed_at": func.coalesce(
                    LearningVideoProgress.completed_at,
                    completed_at,
                ),
                "updated_at": func.now(),
            },
        )
        .returning(
            LearningVideoProgress.position_seconds,
            LearningVideoProgress.completed_at,
        )
    )
    saved = (await session.execute(statement)).one()
    await session.commit()
    return {
        "video_id": video.id,
        "position_seconds": saved.position_seconds,
        "completed_at": saved.completed_at,
    }


def _safe_csv(value: object) -> str:
    text_value = str(value or "")
    return f"'{text_value}" if text_value.startswith(("=", "+", "-", "@")) else text_value


@router.get("/audience")
async def audience(
    user: CurrentUserDep,
    session: SessionDep,
    bot_id: int | None = None,
    source_channel_id: int | None = None,
    status_filter: str = "active",
    q: str = "",
    page: int = 1,
) -> dict[str, object]:
    if status_filter not in {"all", "none", "active", "unsubscribed", "blocked"} or page < 1:
        raise HTTPException(422, "Некорректный фильтр базы")
    query = select(Contact).join(ManagedBot, ManagedBot.id == Contact.bot_id).where(
        ManagedBot.owner_id == user.auth.owner.id
    )
    if bot_id:
        query = query.where(Contact.bot_id == bot_id)
    if source_channel_id:
        query = query.where(Contact.source_channel_id == source_channel_id)
    if status_filter != "all":
        query = query.where(Contact.consent_status == status_filter)
    if q.strip():
        term = f"%{q.strip()[:80]}%"
        query = query.where(
            Contact.username.ilike(term)
            | Contact.first_name.ilike(term)
            | Contact.last_name.ilike(term)
        )
    total = int(await session.scalar(select(func.count()).select_from(query.subquery())) or 0)
    rows = list(await session.scalars(query.order_by(Contact.id.desc()).offset((page - 1) * 50).limit(50)))
    return {
        "items": [
            {
                "id": row.id,
                "bot_id": row.bot_id,
                "username": row.username,
                "first_name": row.first_name,
                "last_name": row.last_name,
                "status": row.consent_status,
                "consented_at": row.consented_at,
                "unsubscribed_at": row.unsubscribed_at,
                "source_channel_id": row.source_channel_id,
            }
            for row in rows
        ],
        "total": total,
        "page": page,
    }


@router.get("/audience.csv")
async def audience_export(
    user: CurrentUserDep,
    session: SessionDep,
    bot_id: int | None = None,
    source_channel_id: int | None = None,
) -> Response:
    query = select(Contact).join(ManagedBot, ManagedBot.id == Contact.bot_id).where(
        ManagedBot.owner_id == user.auth.owner.id,
        Contact.consent_status == "active",
    )
    if bot_id:
        query = query.where(Contact.bot_id == bot_id)
    if source_channel_id:
        query = query.where(Contact.source_channel_id == source_channel_id)
    rows = list(await session.scalars(query.order_by(Contact.id)))
    output = io.StringIO()
    writer = csv.writer(output)
    writer.writerow(("telegram_id", "username", "first_name", "last_name", "consented_at", "consent_version", "source_channel_id"))
    for row in rows:
        writer.writerow(
            (
                row.telegram_id,
                _safe_csv(row.username),
                _safe_csv(row.first_name),
                _safe_csv(row.last_name),
                row.consented_at.isoformat() if row.consented_at else "",
                row.consent_version,
                row.source_channel_id or "",
            )
        )
    return Response(
        content="\ufeff" + output.getvalue(),
        media_type="text/csv; charset=utf-8",
        headers={"Content-Disposition": 'attachment; filename="gramly-audience.csv"'},
    )


@router.get("/broadcasts")
async def broadcasts(user: CurrentUserDep, session: SessionDep) -> dict[str, object]:
    rows = list(
        await session.scalars(
            select(Broadcast)
            .where(Broadcast.owner_id == user.auth.owner.id)
            .order_by(Broadcast.id.desc())
            .limit(100)
        )
    )
    result = []
    for row in rows:
        status_rows = (
            await session.execute(
                select(BroadcastRecipient.status, func.count())
                .where(BroadcastRecipient.broadcast_id == row.id)
                .group_by(BroadcastRecipient.status)
            )
        ).all()
        stats: dict[str, int] = {
            recipient_status: int(count) for recipient_status, count in status_rows
        }
        result.append(
            {
                "id": row.id,
                "bot_id": row.bot_id,
                "source_channel_id": row.source_channel_id,
                "name": row.name,
                "content": row.content,
                "status": row.status,
                "scheduled_at": row.scheduled_at,
                "created_at": row.created_at,
                "stats": stats,
            }
        )
    return {"items": result}


@router.post("/broadcasts", status_code=201)
async def create_broadcast(
    payload: BroadcastSaveRequest,
    user: CsrfUserDep,
    session: SessionDep,
    idempotency_key: Annotated[str, Header(alias="Idempotency-Key")],
) -> dict[str, object]:
    try:
        row = await save_broadcast(
            session,
            owner_id=user.auth.owner.id,
            bot_id=payload.bot_id,
            name=payload.name,
            content=payload.content,
            source_channel_id=payload.source_channel_id,
            idempotency_key=idempotency_key,
        )
    except (ContentValidationError, ValueError) as exc:
        await session.rollback()
        raise HTTPException(422, str(exc)) from exc
    return {"id": row.id, "status": row.status}


@router.post("/broadcasts/{broadcast_id}/launch")
async def start_broadcast(
    broadcast_id: int,
    payload: BroadcastLaunchRequest,
    user: CsrfUserDep,
    session: SessionDep,
) -> dict[str, object]:
    if payload.scheduled_at and payload.scheduled_at <= datetime.now(UTC) - timedelta(minutes=1):
        raise HTTPException(422, "Время отправки уже прошло")
    try:
        row, recipients = await launch_broadcast(
            session,
            owner_id=user.auth.owner.id,
            broadcast_id=broadcast_id,
            scheduled_at=payload.scheduled_at,
        )
    except ContentValidationError as exc:
        await session.rollback()
        raise HTTPException(422, str(exc)) from exc
    return {"id": row.id, "status": row.status, "recipients": recipients}


@router.post("/broadcasts/{broadcast_id}/cancel")
async def stop_broadcast(
    broadcast_id: int, user: CsrfUserDep, session: SessionDep
) -> dict[str, object]:
    try:
        await cancel_broadcast(session, owner_id=user.auth.owner.id, broadcast_id=broadcast_id)
    except ContentValidationError as exc:
        await session.rollback()
        raise HTTPException(422, str(exc)) from exc
    return {"id": broadcast_id, "status": "cancelled"}


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
    timeline_seconds = flow_timeline_seconds(snapshot.version, snapshot.steps)
    bot = await session.get(ManagedBot, snapshot.flow.bot_id)
    channel_query = select(Channel).where(
        Channel.bot_id == snapshot.flow.bot_id,
        Channel.is_active.is_(True),
    )
    if snapshot.flow.assignment_mode == "selected":
        channel_query = channel_query.where(
            Channel.id.in_(
                select(FlowChannelAssignment.channel_id).where(
                    FlowChannelAssignment.flow_id == snapshot.flow.id
                )
            )
        )
    assigned_channels = list((await session.scalars(channel_query)).all())
    channels_missing_invite_rights = [
        channel.title for channel in assigned_channels if not channel.can_invite_users
    ]
    return {
        "flow": {"id": snapshot.flow.id, "name": snapshot.flow.name, "kind": snapshot.flow.kind},
        "version": {
            "button_style": snapshot.version.button_style,
            "id": snapshot.version.id,
            "number": snapshot.version.version,
            "first_delay_seconds": snapshot.version.first_delay_seconds,
            "timeline_seconds": timeline_seconds,
            "join_request_compatible": timeline_seconds <= JOIN_REQUEST_MAX_TIMELINE_SECONDS,
            "join_request_strategy": (
                "direct"
                if timeline_seconds <= JOIN_REQUEST_MAX_TIMELINE_SECONDS
                else "activation_required"
            ),
            "join_request_explanation": (
                "Отправим цепочку до принятия заявки."
                if timeline_seconds <= JOIN_REQUEST_MAX_TIMELINE_SECONDS
                else "Сначала попросим пользователя разрешить сообщения, затем запустим полную цепочку."
            ),
            "auto_approve": bool(bot and bot.auto_approve),
            "channels_missing_invite_rights": channels_missing_invite_rights,
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
    step = await session.scalar(
        select(ContentStep)
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
        .with_for_update()
    )
    if step is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "Editable step not found")
    next_payload = deepcopy(step.payload)
    sequence = next_payload.get("sequence")
    if isinstance(sequence, list):
        replacement = {"type": "text", "text": payload.text, "entities": []}
        first_message = next(
            (
                item
                for item in sequence
                if isinstance(item, dict) and item.get("kind") == "message"
            ),
            None,
        )
        if first_message is None:
            sequence.insert(0, {"kind": "message", "payload": replacement})
        else:
            first_message["payload"] = replacement
    else:
        next_payload = {"type": "text", "text": payload.text, "entities": []}
    step.payload = next_payload
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
            "expires_at": payment.expires_at.isoformat(),
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


async def _create_stars_invoice_link(settings: Settings, payment: Payment, plan: Plan) -> str:
    if not settings.interface_bot_token or plan.price_xtr is None:
        raise FinanceError("Telegram Stars checkout is not configured")
    bot = Bot(settings.interface_bot_token)
    try:
        return await bot.create_invoice_link(
            title="GramlyHello Business",
            description="Business на 30 дней: без рекламы и с ротацией каналов.",
            payload=str(payment.checkout_token),
            currency="XTR",
            prices=[LabeledPrice(label="Business · 30 дней", amount=plan.price_xtr)],
            provider_token="",
            subscription_period=2_592_000,
        )
    finally:
        await bot.session.close()


@router.post("/payments/stars")
async def stars_checkout(
    user: CsrfUserDep,
    session: SessionDep,
    settings: SettingsDep,
    idempotency_key: Annotated[str, Header(alias="Idempotency-Key")],
) -> dict[str, object]:
    payload = {"plan": "business", "provider": "telegram_stars", "surface": "mini_app"}
    try:
        stored = await claim_request(
            session, owner_id=user.auth.owner.id, key=idempotency_key, payload=payload
        )
        if stored is not None:
            return stored.body
        payment = await create_stars_checkout(session, user.auth.owner.id, surface="mini_app")
        plan = await session.get(Plan, payment.plan_id)
        if plan is None:
            raise FinanceError("Business plan is unavailable")
        invoice_url = payment.invoice_url
        if not invoice_url:
            invoice_url = await _create_stars_invoice_link(settings, payment, plan)
            payment.invoice_url = invoice_url
            payment.provider_payload = {
                "subscription_period": 2_592_000,
                "surface": "mini_app",
            }
            await session.commit()
        body: dict[str, object] = {
            "payment_id": payment.id,
            "invoice_url": invoice_url,
            "status": payment.status,
            "expires_at": payment.expires_at.isoformat(),
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
    except (FinanceError, TelegramAPIError) as exc:
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
    response.delete_cookie("gramly_hello_csrf", path="/")
    response.status_code = status.HTTP_204_NO_CONTENT
    return response
