from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime
from decimal import Decimal
from typing import Annotated

from fastapi import APIRouter, Depends, Header, HTTPException, Request, Response, status
from pydantic import BaseModel, Field
from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from .billing import create_crypto_checkout
from .commercial import access_for_owner, list_sellable_plans
from .config import Settings, get_settings
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
from .models import FinancialLedgerEntry, ReferralAttribution, Withdrawal
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
        payment = await create_crypto_checkout(
            session, user.auth.owner.id, client, surface="mini_app"
        )
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
