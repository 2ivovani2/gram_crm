from __future__ import annotations

import uuid
from datetime import datetime
from decimal import Decimal
from enum import StrEnum
from typing import Any

from sqlalchemy import (
    BigInteger,
    Boolean,
    CheckConstraint,
    DateTime,
    ForeignKey,
    Index,
    Integer,
    Numeric,
    String,
    Text,
    UniqueConstraint,
    text,
)
from sqlalchemy.dialects.postgresql import JSONB, UUID
from sqlalchemy.orm import DeclarativeBase, Mapped, mapped_column
from sqlalchemy.sql import func


class Base(DeclarativeBase):
    pass


class QueueStatus(StrEnum):
    PENDING = "pending"
    PROCESSING = "processing"
    COMPLETED = "completed"
    RETRY = "retry"
    DEAD = "dead"


class Owner(Base):
    __tablename__ = "owner"
    __table_args__ = (UniqueConstraint("telegram_id", name="owner_telegram_id_key"),)
    id: Mapped[int] = mapped_column(BigInteger, primary_key=True)
    telegram_id: Mapped[int] = mapped_column(BigInteger, index=True)
    username: Mapped[str] = mapped_column(String(64), default="")
    first_name: Mapped[str] = mapped_column(String(128), default="")
    last_name: Mapped[str] = mapped_column(String(128), default="")
    guide_completed: Mapped[bool] = mapped_column(Boolean, default=False)
    guide_step: Mapped[int] = mapped_column(Integer, default=0)
    trial_started_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    trial_ends_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), index=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())
    last_seen_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())


class Plan(Base):
    __tablename__ = "plan"
    __table_args__ = (
        UniqueConstraint("slug", name="plan_slug_key"),
        CheckConstraint(
            "max_bots > 0 AND max_channels > 0 "
            "AND monthly_delivery_operations > 0 AND media_storage_bytes > 0",
            name="ck_plan_positive_quotas",
        ),
        CheckConstraint(
            "NOT crypto_pay_enabled OR price_rub > 0",
            name="ck_plan_crypto_price",
        ),
        CheckConstraint(
            "NOT stars_enabled OR (price_xtr > 0 AND referral_base_rub > 0)",
            name="ck_plan_stars_price",
        ),
    )
    id: Mapped[int] = mapped_column(BigInteger, primary_key=True)
    slug: Mapped[str] = mapped_column(String(32), index=True)
    display_name: Mapped[str] = mapped_column(String(64))
    entitlements: Mapped[dict[str, Any]] = mapped_column(JSONB, default=dict)
    max_bots: Mapped[int] = mapped_column(Integer)
    max_channels: Mapped[int] = mapped_column(Integer)
    monthly_delivery_operations: Mapped[int] = mapped_column(Integer)
    media_storage_bytes: Mapped[int] = mapped_column(BigInteger)
    price_rub: Mapped[Decimal | None] = mapped_column(Numeric(14, 2))
    price_xtr: Mapped[int | None] = mapped_column(Integer)
    referral_base_rub: Mapped[Decimal | None] = mapped_column(Numeric(14, 2))
    crypto_pay_enabled: Mapped[bool] = mapped_column(Boolean, default=False)
    stars_enabled: Mapped[bool] = mapped_column(Boolean, default=False)
    is_sellable: Mapped[bool] = mapped_column(Boolean, default=True)
    is_active: Mapped[bool] = mapped_column(Boolean, default=True, index=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), onupdate=func.now()
    )


class Subscription(Base):
    __tablename__ = "subscription"
    __table_args__ = (
        UniqueConstraint("owner_id", name="subscription_owner_id_key"),
        Index("ix_subscription_access", "status", "ends_at"),
        CheckConstraint(
            "(source = 'free' AND ends_at IS NULL) OR (source <> 'free' AND ends_at > starts_at)",
            name="ck_subscription_period",
        ),
        CheckConstraint(
            "status IN ('active','past_due','expired','canceled')",
            name="ck_subscription_status",
        ),
        CheckConstraint(
            "source IN ('free','crypto_pay','telegram_stars','manual')",
            name="ck_subscription_source",
        ),
    )
    id: Mapped[int] = mapped_column(BigInteger, primary_key=True)
    owner_id: Mapped[int] = mapped_column(ForeignKey("owner.id", ondelete="CASCADE"), index=True)
    plan_id: Mapped[int] = mapped_column(ForeignKey("plan.id", ondelete="RESTRICT"), index=True)
    source: Mapped[str] = mapped_column(String(24))
    status: Mapped[str] = mapped_column(String(24), index=True)
    starts_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))
    ends_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), index=True)
    auto_renew: Mapped[bool] = mapped_column(Boolean, default=False)
    external_reference: Mapped[str] = mapped_column(String(128), default="")
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), onupdate=func.now()
    )


class FeatureFlag(Base):
    __tablename__ = "feature_flag"
    key: Mapped[str] = mapped_column(String(64), primary_key=True)
    enabled: Mapped[bool] = mapped_column(Boolean, default=False)
    config: Mapped[dict[str, Any]] = mapped_column(JSONB, default=dict)
    description: Mapped[str] = mapped_column(String(255), default="")
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), onupdate=func.now()
    )


class WebSession(Base):
    __tablename__ = "web_session"
    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True)
    owner_id: Mapped[int] = mapped_column(ForeignKey("owner.id", ondelete="CASCADE"), index=True)
    token_hash: Mapped[str] = mapped_column(String(64), unique=True)
    csrf_hash: Mapped[str] = mapped_column(String(64))
    telegram_auth_date: Mapped[datetime] = mapped_column(DateTime(timezone=True))
    expires_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), index=True)
    revoked_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())


class IdempotencyRecord(Base):
    __tablename__ = "idempotency_record"
    __table_args__ = (UniqueConstraint("owner_id", "key", name="uq_idempotency_owner_key"),)
    id: Mapped[int] = mapped_column(BigInteger, primary_key=True)
    owner_id: Mapped[int] = mapped_column(ForeignKey("owner.id", ondelete="CASCADE"), index=True)
    key: Mapped[str] = mapped_column(String(128))
    request_hash: Mapped[str] = mapped_column(String(64))
    response_status: Mapped[int | None] = mapped_column(Integer)
    response_body: Mapped[dict[str, Any] | None] = mapped_column(JSONB)
    expires_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), index=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())


class ReferralCode(Base):
    __tablename__ = "referral_code"
    __table_args__ = (
        UniqueConstraint("owner_id", name="referral_code_owner_id_key"),
        UniqueConstraint("code", name="referral_code_code_key"),
    )
    id: Mapped[int] = mapped_column(BigInteger, primary_key=True)
    owner_id: Mapped[int] = mapped_column(ForeignKey("owner.id", ondelete="CASCADE"), index=True)
    code: Mapped[str] = mapped_column(String(48), index=True)
    is_active: Mapped[bool] = mapped_column(Boolean, default=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())


class ReferralAttribution(Base):
    __tablename__ = "referral_attribution"
    __table_args__ = (
        UniqueConstraint("referred_owner_id", name="referral_attribution_referred_owner_id_key"),
        CheckConstraint("referrer_owner_id <> referred_owner_id", name="ck_referral_not_self"),
        CheckConstraint("status IN ('candidate','active','inactive')", name="ck_referral_status"),
    )
    id: Mapped[int] = mapped_column(BigInteger, primary_key=True)
    referrer_owner_id: Mapped[int] = mapped_column(
        ForeignKey("owner.id", ondelete="CASCADE"), index=True
    )
    referred_owner_id: Mapped[int] = mapped_column(
        ForeignKey("owner.id", ondelete="CASCADE"), index=True
    )
    code_snapshot: Mapped[str] = mapped_column(String(48))
    status: Mapped[str] = mapped_column(String(16), default="candidate", index=True)
    attributed_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())
    first_paid_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    commission_ends_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), index=True)
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), onupdate=func.now()
    )


class Payment(Base):
    __tablename__ = "payment"
    __table_args__ = (
        UniqueConstraint("provider", "provider_invoice_id", name="uq_payment_provider_invoice"),
        UniqueConstraint("checkout_token", name="payment_checkout_token_key"),
        CheckConstraint(
            "provider IN ('crypto_pay','telegram_stars','manual')", name="ck_payment_provider"
        ),
        CheckConstraint(
            "status IN ('created','paid','expired','refunded','failed')", name="ck_payment_status"
        ),
        CheckConstraint("amount_rub > 0", name="ck_payment_amount_rub"),
    )
    id: Mapped[int] = mapped_column(BigInteger, primary_key=True)
    checkout_token: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), default=uuid.uuid4)
    owner_id: Mapped[int] = mapped_column(ForeignKey("owner.id", ondelete="CASCADE"), index=True)
    plan_id: Mapped[int] = mapped_column(ForeignKey("plan.id", ondelete="RESTRICT"), index=True)
    provider: Mapped[str] = mapped_column(String(24), index=True)
    provider_invoice_id: Mapped[str | None] = mapped_column(String(128))
    status: Mapped[str] = mapped_column(String(16), default="created", index=True)
    amount_rub: Mapped[Decimal] = mapped_column(Numeric(14, 2))
    original_amount: Mapped[Decimal | None] = mapped_column(Numeric(28, 10))
    original_currency: Mapped[str] = mapped_column(String(16), default="RUB")
    paid_asset: Mapped[str | None] = mapped_column(String(16))
    exchange_rate_rub: Mapped[Decimal | None] = mapped_column(Numeric(28, 10))
    provider_payload: Mapped[dict[str, Any]] = mapped_column(JSONB, default=dict)
    invoice_url: Mapped[str] = mapped_column(String(1024), default="")
    period_days: Mapped[int] = mapped_column(Integer, default=30)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())
    paid_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), index=True)
    refunded_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))


class PaymentEvent(Base):
    __tablename__ = "payment_event"
    __table_args__ = (
        UniqueConstraint("provider", "event_key", name="uq_payment_event_provider_key"),
    )
    id: Mapped[int] = mapped_column(BigInteger, primary_key=True)
    provider: Mapped[str] = mapped_column(String(24), index=True)
    event_key: Mapped[str] = mapped_column(String(128))
    payload_hash: Mapped[str] = mapped_column(String(64))
    status: Mapped[str] = mapped_column(String(16), default="received")
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())
    processed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))


class SubscriptionReminder(Base):
    __tablename__ = "subscription_reminder"
    __table_args__ = (
        UniqueConstraint(
            "subscription_id", "days_before", name="uq_subscription_reminder_day"
        ),
        CheckConstraint("days_before IN (7,3,1)", name="ck_subscription_reminder_day"),
        CheckConstraint(
            "status IN ('pending','processing','retry','sent','failed')",
            name="ck_subscription_reminder_status",
        ),
        Index("ix_subscription_reminder_claim", "status", "due_at"),
    )
    id: Mapped[int] = mapped_column(BigInteger, primary_key=True)
    subscription_id: Mapped[int] = mapped_column(
        ForeignKey("subscription.id", ondelete="CASCADE"), index=True
    )
    days_before: Mapped[int] = mapped_column(Integer)
    due_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), index=True)
    status: Mapped[str] = mapped_column(String(16), default="pending")
    attempts: Mapped[int] = mapped_column(Integer, default=0)
    last_error: Mapped[str] = mapped_column(String(500), default="")
    sent_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())


class Withdrawal(Base):
    __tablename__ = "withdrawal"
    __table_args__ = (
        UniqueConstraint("public_id", name="withdrawal_public_id_key"),
        UniqueConstraint("spend_id", name="withdrawal_spend_id_key"),
        CheckConstraint("requested_rub >= 1000", name="ck_withdrawal_minimum"),
        CheckConstraint(
            "status IN ('requested','processing','retry','paid','rejected','failed')",
            name="ck_withdrawal_status",
        ),
    )
    id: Mapped[int] = mapped_column(BigInteger, primary_key=True)
    public_id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), default=uuid.uuid4)
    owner_id: Mapped[int] = mapped_column(ForeignKey("owner.id", ondelete="CASCADE"), index=True)
    requested_rub: Mapped[Decimal] = mapped_column(Numeric(14, 2))
    payout_asset: Mapped[str] = mapped_column(String(16), default="USDT")
    payout_amount: Mapped[Decimal | None] = mapped_column(Numeric(28, 10))
    exchange_rate_rub: Mapped[Decimal | None] = mapped_column(Numeric(28, 10))
    recipient_telegram_id: Mapped[int] = mapped_column(BigInteger)
    spend_id: Mapped[str] = mapped_column(String(64))
    status: Mapped[str] = mapped_column(String(16), default="requested", index=True)
    provider_transfer_id: Mapped[str] = mapped_column(String(128), default="")
    attempts: Mapped[int] = mapped_column(Integer, default=0)
    available_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())
    last_error: Mapped[str] = mapped_column(String(500), default="")
    rejection_reason: Mapped[str] = mapped_column(String(500), default="")
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())
    processed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))


class FinancialLedgerEntry(Base):
    __tablename__ = "financial_ledger_entry"
    __table_args__ = (
        CheckConstraint(
            "entry_type IN ('commission','reserve','payout','reserve_release','refund','adjustment')",
            name="ck_ledger_entry_type",
        ),
        CheckConstraint("amount_rub <> 0", name="ck_ledger_nonzero"),
        UniqueConstraint(
            "owner_id", "payment_id", "entry_type", name="uq_ledger_owner_payment_type"
        ),
        UniqueConstraint("withdrawal_id", "entry_type", name="uq_ledger_withdrawal_type"),
        Index("ix_ledger_owner_created", "owner_id", "created_at"),
    )
    id: Mapped[int] = mapped_column(BigInteger, primary_key=True)
    owner_id: Mapped[int] = mapped_column(ForeignKey("owner.id", ondelete="RESTRICT"), index=True)
    payment_id: Mapped[int | None] = mapped_column(
        ForeignKey("payment.id", ondelete="RESTRICT"), index=True
    )
    withdrawal_id: Mapped[int | None] = mapped_column(
        ForeignKey("withdrawal.id", ondelete="RESTRICT"), index=True
    )
    entry_type: Mapped[str] = mapped_column(String(24), index=True)
    amount_rub: Mapped[Decimal] = mapped_column(Numeric(14, 2))
    rate_percent: Mapped[Decimal | None] = mapped_column(Numeric(5, 2))
    reason: Mapped[str] = mapped_column(String(500), default="")
    metadata_json: Mapped[dict[str, Any]] = mapped_column("metadata", JSONB, default=dict)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())


class ContentFlow(Base):
    __tablename__ = "content_flow"
    __table_args__ = (
        UniqueConstraint("bot_id", "kind", "name", name="uq_content_flow_bot_kind_name"),
        CheckConstraint("kind IN ('welcome','farewell')", name="ck_content_flow_kind"),
        CheckConstraint(
            "assignment_mode IN ('all','selected')",
            name="ck_content_flow_assignment_mode",
        ),
    )
    id: Mapped[int] = mapped_column(BigInteger, primary_key=True)
    bot_id: Mapped[int] = mapped_column(ForeignKey("managed_bot.id", ondelete="CASCADE"), index=True)
    name: Mapped[str] = mapped_column(String(128))
    kind: Mapped[str] = mapped_column(String(16), index=True)
    assignment_mode: Mapped[str] = mapped_column(String(16), default="all")
    is_active: Mapped[bool] = mapped_column(Boolean, default=True, index=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), onupdate=func.now()
    )


class ContentFlowVersion(Base):
    __tablename__ = "content_flow_version"
    __table_args__ = (
        UniqueConstraint("flow_id", "version", name="uq_content_flow_version"),
        CheckConstraint(
            "status IN ('draft','published','archived')",
            name="ck_content_flow_version_status",
        ),
        CheckConstraint(
            "first_delay_seconds BETWEEN 0 AND 86400",
            name="ck_content_flow_first_delay",
        ),
        Index(
            "uq_content_flow_published",
            "flow_id",
            unique=True,
            postgresql_where=text("status = 'published'"),
        ),
        Index(
            "uq_content_flow_draft",
            "flow_id",
            unique=True,
            postgresql_where=text("status = 'draft'"),
        ),
    )
    id: Mapped[int] = mapped_column(BigInteger, primary_key=True)
    flow_id: Mapped[int] = mapped_column(ForeignKey("content_flow.id", ondelete="CASCADE"), index=True)
    version: Mapped[int] = mapped_column(Integer)
    status: Mapped[str] = mapped_column(String(16), index=True)
    author_telegram_id: Mapped[int] = mapped_column(BigInteger)
    first_delay_seconds: Mapped[int] = mapped_column(Integer, default=0)
    legacy_version_id: Mapped[int | None] = mapped_column(
        ForeignKey("welcome_message_version.id", ondelete="SET NULL"), unique=True
    )
    published_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())


class ContentStep(Base):
    __tablename__ = "content_step"
    __table_args__ = (
        UniqueConstraint("version_id", "position", name="uq_content_step_position"),
        CheckConstraint("position >= 0", name="ck_content_step_position"),
        CheckConstraint(
            "delay_after_seconds BETWEEN 0 AND 86400",
            name="ck_content_step_delay",
        ),
    )
    id: Mapped[int] = mapped_column(BigInteger, primary_key=True)
    version_id: Mapped[int] = mapped_column(
        ForeignKey("content_flow_version.id", ondelete="CASCADE"), index=True
    )
    position: Mapped[int] = mapped_column(Integer)
    payload: Mapped[dict[str, Any]] = mapped_column(JSONB, default=dict)
    delay_after_seconds: Mapped[int] = mapped_column(Integer, default=0)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())


class ContentAttachment(Base):
    __tablename__ = "content_attachment"
    __table_args__ = (
        UniqueConstraint("step_id", "position", name="uq_content_attachment_position"),
        CheckConstraint("position >= 0", name="ck_content_attachment_position"),
    )
    id: Mapped[int] = mapped_column(BigInteger, primary_key=True)
    step_id: Mapped[int] = mapped_column(ForeignKey("content_step.id", ondelete="CASCADE"), index=True)
    position: Mapped[int] = mapped_column(Integer)
    media_type: Mapped[str] = mapped_column(String(32))
    storage_key: Mapped[str] = mapped_column(String(500))
    original_name: Mapped[str] = mapped_column(String(255), default="")
    mime_type: Mapped[str] = mapped_column(String(128), default="")
    size: Mapped[int] = mapped_column(BigInteger, default=0)
    payload: Mapped[dict[str, Any]] = mapped_column(JSONB, default=dict)


class ContentKeyboard(Base):
    __tablename__ = "content_keyboard"
    __table_args__ = (
        UniqueConstraint("step_id", name="content_keyboard_step_id_key"),
        CheckConstraint("kind IN ('inline','reply')", name="ck_content_keyboard_kind"),
    )
    id: Mapped[int] = mapped_column(BigInteger, primary_key=True)
    step_id: Mapped[int] = mapped_column(ForeignKey("content_step.id", ondelete="CASCADE"), index=True)
    kind: Mapped[str] = mapped_column(String(16))
    settings: Mapped[dict[str, Any]] = mapped_column(JSONB, default=dict)


class ContentKeyboardButton(Base):
    __tablename__ = "content_keyboard_button"
    __table_args__ = (
        UniqueConstraint("keyboard_id", "row", "position", name="uq_content_keyboard_button_position"),
        CheckConstraint("row >= 0 AND position >= 0", name="ck_content_keyboard_button_position"),
        CheckConstraint(
            "action_type IN ('url','callback','text')",
            name="ck_content_keyboard_button_action",
        ),
        CheckConstraint(
            "style IN ('default','primary','success','danger')",
            name="ck_content_keyboard_button_style",
        ),
    )
    id: Mapped[int] = mapped_column(BigInteger, primary_key=True)
    keyboard_id: Mapped[int] = mapped_column(
        ForeignKey("content_keyboard.id", ondelete="CASCADE"), index=True
    )
    row: Mapped[int] = mapped_column(Integer)
    position: Mapped[int] = mapped_column(Integer)
    text: Mapped[str] = mapped_column(String(128))
    action_type: Mapped[str] = mapped_column(String(16))
    value: Mapped[str] = mapped_column(String(1024))
    style: Mapped[str] = mapped_column(String(16), default="default")


class FlowChannelAssignment(Base):
    __tablename__ = "flow_channel_assignment"
    __table_args__ = (UniqueConstraint("flow_id", "channel_id", name="uq_flow_channel_assignment"),)
    id: Mapped[int] = mapped_column(BigInteger, primary_key=True)
    flow_id: Mapped[int] = mapped_column(ForeignKey("content_flow.id", ondelete="CASCADE"), index=True)
    channel_id: Mapped[int] = mapped_column(ForeignKey("channel.id", ondelete="CASCADE"), index=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())


class FlowDelivery(Base):
    __tablename__ = "flow_delivery"
    __table_args__ = (
        UniqueConstraint("bot_id", "event_key", name="uq_flow_delivery_bot_event"),
        CheckConstraint(
            "status IN ('scheduled','processing','completed','partial','failed','cancelled','unreachable')",
            name="ck_flow_delivery_status",
        ),
    )
    id: Mapped[int] = mapped_column(BigInteger, primary_key=True)
    bot_id: Mapped[int] = mapped_column(ForeignKey("managed_bot.id", ondelete="CASCADE"), index=True)
    channel_id: Mapped[int] = mapped_column(ForeignKey("channel.id", ondelete="CASCADE"), index=True)
    contact_id: Mapped[int] = mapped_column(ForeignKey("contact.id", ondelete="CASCADE"), index=True)
    version_id: Mapped[int] = mapped_column(
        ForeignKey("content_flow_version.id", ondelete="RESTRICT"), index=True
    )
    event_key: Mapped[str] = mapped_column(String(128))
    status: Mapped[str] = mapped_column(String(16), default="scheduled", index=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())
    completed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))


class DeliveryOperation(Base):
    __tablename__ = "delivery_operation"
    __table_args__ = (
        UniqueConstraint("flow_delivery_id", "position", name="uq_delivery_operation_position"),
        Index("ix_delivery_operation_claim", "status", "due_at", "lease_expires_at"),
        CheckConstraint("position >= 0", name="ck_delivery_operation_position"),
        CheckConstraint(
            "status IN ('scheduled','processing','retry','sent','failed','cancelled')",
            name="ck_delivery_operation_status",
        ),
    )
    id: Mapped[int] = mapped_column(BigInteger, primary_key=True)
    flow_delivery_id: Mapped[int] = mapped_column(
        ForeignKey("flow_delivery.id", ondelete="CASCADE"), index=True
    )
    step_id: Mapped[int] = mapped_column(ForeignKey("content_step.id", ondelete="RESTRICT"), index=True)
    depends_on_operation_id: Mapped[int | None] = mapped_column(
        ForeignKey("delivery_operation.id", ondelete="RESTRICT"), index=True
    )
    position: Mapped[int] = mapped_column(Integer)
    operation_type: Mapped[str] = mapped_column(String(32))
    payload: Mapped[dict[str, Any]] = mapped_column(JSONB, default=dict)
    media: Mapped[list[dict[str, Any]]] = mapped_column(JSONB, default=list)
    status: Mapped[str] = mapped_column(String(16), default="scheduled", index=True)
    attempts: Mapped[int] = mapped_column(Integer, default=0)
    due_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), index=True)
    lease_owner: Mapped[str | None] = mapped_column(String(64))
    lease_expires_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    sent_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    error: Mapped[str] = mapped_column(String(500), default="")
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())


class AdCreative(Base):
    __tablename__ = "ad_creative"
    __table_args__ = (
        CheckConstraint("weight BETWEEN 1 AND 1000", name="ck_ad_creative_weight"),
        CheckConstraint(
            "(cta_text = '' AND cta_url = '') OR (cta_text <> '' AND cta_url <> '')",
            name="ck_ad_creative_cta_pair",
        ),
    )
    id: Mapped[int] = mapped_column(BigInteger, primary_key=True)
    name: Mapped[str] = mapped_column(String(128))
    text: Mapped[str] = mapped_column(Text)
    entities: Mapped[list[dict[str, Any]]] = mapped_column(JSONB, default=list)
    cta_text: Mapped[str] = mapped_column(String(64), default="")
    cta_url: Mapped[str] = mapped_column(String(2048), default="")
    weight: Mapped[int] = mapped_column(Integer, default=1)
    is_active: Mapped[bool] = mapped_column(Boolean, default=True, index=True)
    created_by_telegram_id: Mapped[int | None] = mapped_column(BigInteger)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), onupdate=func.now()
    )


class AdImpression(Base):
    __tablename__ = "ad_impression"
    __table_args__ = (
        UniqueConstraint("flow_delivery_id", name="ad_impression_flow_delivery_id_key"),
        UniqueConstraint("operation_id", name="ad_impression_operation_id_key"),
        CheckConstraint(
            "status IN ('scheduled','sent','failed','cancelled')",
            name="ck_ad_impression_status",
        ),
        CheckConstraint("click_count >= 0", name="ck_ad_impression_click_count"),
    )
    id: Mapped[int] = mapped_column(BigInteger, primary_key=True)
    public_token: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), unique=True, index=True)
    creative_id: Mapped[int] = mapped_column(ForeignKey("ad_creative.id", ondelete="RESTRICT"), index=True)
    owner_id: Mapped[int] = mapped_column(ForeignKey("owner.id", ondelete="CASCADE"), index=True)
    bot_id: Mapped[int] = mapped_column(ForeignKey("managed_bot.id", ondelete="CASCADE"), index=True)
    channel_id: Mapped[int] = mapped_column(ForeignKey("channel.id", ondelete="CASCADE"), index=True)
    contact_id: Mapped[int] = mapped_column(ForeignKey("contact.id", ondelete="CASCADE"), index=True)
    flow_delivery_id: Mapped[int] = mapped_column(
        ForeignKey("flow_delivery.id", ondelete="CASCADE"), index=True
    )
    operation_id: Mapped[int | None] = mapped_column(
        ForeignKey("delivery_operation.id", ondelete="SET NULL"), index=True
    )
    status: Mapped[str] = mapped_column(String(16), default="scheduled", index=True)
    destination_url: Mapped[str] = mapped_column(String(2048), default="")
    click_count: Mapped[int] = mapped_column(Integer, default=0)
    selected_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())
    shown_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), index=True)
    first_clicked_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), index=True)
    last_clicked_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    error: Mapped[str] = mapped_column(String(500), default="")


class ManagedBot(Base):
    __tablename__ = "managed_bot"
    __table_args__ = (
        UniqueConstraint("public_id", name="managed_bot_public_id_key"),
        UniqueConstraint("telegram_id", name="managed_bot_telegram_id_key"),
    )
    id: Mapped[int] = mapped_column(BigInteger, primary_key=True)
    owner_id: Mapped[int] = mapped_column(ForeignKey("owner.id", ondelete="CASCADE"), index=True)
    public_id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), index=True)
    telegram_id: Mapped[int] = mapped_column(BigInteger, index=True)
    username: Mapped[str] = mapped_column(String(64), default="")
    display_name: Mapped[str] = mapped_column(String(128))
    token_ciphertext: Mapped[str] = mapped_column(Text)
    key_version: Mapped[int] = mapped_column(Integer, default=1)
    webhook_secret: Mapped[str] = mapped_column(String(64))
    path_secret: Mapped[str] = mapped_column(String(64))
    is_active: Mapped[bool] = mapped_column(Boolean, default=True, index=True)
    webhook_configured: Mapped[bool] = mapped_column(Boolean, default=False)
    welcome_delay_seconds: Mapped[int] = mapped_column(Integer, default=0)
    approval_delay_seconds: Mapped[int] = mapped_column(Integer, default=0)
    auto_approve: Mapped[bool] = mapped_column(Boolean, default=False)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), onupdate=func.now()
    )


class Channel(Base):
    __tablename__ = "channel"
    __table_args__ = (UniqueConstraint("bot_id", "telegram_id", name="uq_channel_bot_telegram"),)
    id: Mapped[int] = mapped_column(BigInteger, primary_key=True)
    bot_id: Mapped[int] = mapped_column(ForeignKey("managed_bot.id", ondelete="CASCADE"), index=True)
    telegram_id: Mapped[int] = mapped_column(BigInteger)
    title: Mapped[str] = mapped_column(String(255))
    username: Mapped[str] = mapped_column(String(64), default="")
    is_active: Mapped[bool] = mapped_column(Boolean, default=True, index=True)
    can_invite_users: Mapped[bool] = mapped_column(Boolean, default=False)
    connected_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())
    disconnected_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))


class WelcomeMessageVersion(Base):
    __tablename__ = "welcome_message_version"
    __table_args__ = (UniqueConstraint("bot_id", "version", name="uq_welcome_version"),)
    id: Mapped[int] = mapped_column(BigInteger, primary_key=True)
    bot_id: Mapped[int] = mapped_column(ForeignKey("managed_bot.id", ondelete="CASCADE"), index=True)
    version: Mapped[int] = mapped_column(Integer)
    author_telegram_id: Mapped[int] = mapped_column(BigInteger)
    payload: Mapped[dict[str, Any]] = mapped_column(JSONB)
    is_active: Mapped[bool] = mapped_column(Boolean, default=False, index=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())


class WelcomeMedia(Base):
    __tablename__ = "welcome_media"
    id: Mapped[int] = mapped_column(BigInteger, primary_key=True)
    version_id: Mapped[int] = mapped_column(
        ForeignKey("welcome_message_version.id", ondelete="CASCADE"), index=True
    )
    position: Mapped[int] = mapped_column(Integer, default=0)
    media_type: Mapped[str] = mapped_column(String(32))
    storage_key: Mapped[str] = mapped_column(String(500))
    original_name: Mapped[str] = mapped_column(String(255), default="")
    mime_type: Mapped[str] = mapped_column(String(128), default="")
    size: Mapped[int] = mapped_column(BigInteger, default=0)


class WelcomeDraft(Base):
    __tablename__ = "welcome_draft"
    __table_args__ = (
        UniqueConstraint("bot_id", "media_group_id", name="uq_welcome_draft_group"),
        Index("ix_welcome_draft_finalize", "finalize_at"),
    )
    id: Mapped[int] = mapped_column(BigInteger, primary_key=True)
    bot_id: Mapped[int] = mapped_column(ForeignKey("managed_bot.id", ondelete="CASCADE"), index=True)
    owner_id: Mapped[int] = mapped_column(ForeignKey("owner.id", ondelete="CASCADE"), index=True)
    media_group_id: Mapped[str] = mapped_column(String(128))
    payload: Mapped[dict[str, Any]] = mapped_column(JSONB, default=dict)
    finalize_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))
    finalized_version_id: Mapped[int | None] = mapped_column(
        ForeignKey("welcome_message_version.id", ondelete="SET NULL"), index=True
    )
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), onupdate=func.now()
    )


class WelcomeDraftMedia(Base):
    __tablename__ = "welcome_draft_media"
    __table_args__ = (UniqueConstraint("draft_id", "telegram_message_id", name="uq_welcome_draft_message"),)
    id: Mapped[int] = mapped_column(BigInteger, primary_key=True)
    draft_id: Mapped[int] = mapped_column(ForeignKey("welcome_draft.id", ondelete="CASCADE"), index=True)
    telegram_message_id: Mapped[int] = mapped_column(BigInteger)
    media_type: Mapped[str] = mapped_column(String(32))
    storage_key: Mapped[str] = mapped_column(String(500))
    original_name: Mapped[str] = mapped_column(String(255), default="")
    mime_type: Mapped[str] = mapped_column(String(128), default="")
    size: Mapped[int] = mapped_column(BigInteger, default=0)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())


class Contact(Base):
    __tablename__ = "contact"
    __table_args__ = (UniqueConstraint("bot_id", "telegram_id", name="uq_contact_bot_telegram"),)
    id: Mapped[int] = mapped_column(BigInteger, primary_key=True)
    bot_id: Mapped[int] = mapped_column(ForeignKey("managed_bot.id", ondelete="CASCADE"), index=True)
    telegram_id: Mapped[int] = mapped_column(BigInteger)
    username: Mapped[str] = mapped_column(String(64), default="")
    first_name: Mapped[str] = mapped_column(String(128), default="")
    last_name: Mapped[str] = mapped_column(String(128), default="")
    language_code: Mapped[str] = mapped_column(String(16), default="unknown")
    gender: Mapped[str] = mapped_column(String(16), default="unknown", index=True)
    delivery_status: Mapped[str] = mapped_column(String(16), default="unknown", index=True)
    bot_started: Mapped[bool] = mapped_column(Boolean, default=False)
    first_seen_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())
    last_seen_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), onupdate=func.now()
    )
    last_delivery_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    last_error: Mapped[str] = mapped_column(String(500), default="")


class ChannelMembership(Base):
    __tablename__ = "channel_membership"
    __table_args__ = (
        UniqueConstraint("channel_id", "contact_id", name="uq_channel_membership_contact"),
        CheckConstraint("status IN ('active','left')", name="ck_channel_membership_status"),
    )
    id: Mapped[int] = mapped_column(BigInteger, primary_key=True)
    channel_id: Mapped[int] = mapped_column(ForeignKey("channel.id", ondelete="CASCADE"), index=True)
    contact_id: Mapped[int] = mapped_column(ForeignKey("contact.id", ondelete="CASCADE"), index=True)
    status: Mapped[str] = mapped_column(String(16), index=True)
    last_update_id: Mapped[int] = mapped_column(BigInteger)
    joined_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    left_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())


class DepartureEvent(Base):
    __tablename__ = "departure_event"
    __table_args__ = (
        UniqueConstraint("bot_id", "telegram_update_id", name="uq_departure_bot_update"),
        CheckConstraint("reason IN ('left','kicked')", name="ck_departure_reason"),
    )
    id: Mapped[int] = mapped_column(BigInteger, primary_key=True)
    bot_id: Mapped[int] = mapped_column(ForeignKey("managed_bot.id", ondelete="CASCADE"), index=True)
    owner_id: Mapped[int] = mapped_column(ForeignKey("owner.id", ondelete="CASCADE"), index=True)
    channel_id: Mapped[int] = mapped_column(ForeignKey("channel.id", ondelete="CASCADE"), index=True)
    contact_id: Mapped[int] = mapped_column(ForeignKey("contact.id", ondelete="CASCADE"), index=True)
    telegram_update_id: Mapped[int] = mapped_column(BigInteger)
    reason: Mapped[str] = mapped_column(String(16))
    farewell_delivery_id: Mapped[int | None] = mapped_column(
        ForeignKey("flow_delivery.id", ondelete="SET NULL"), unique=True
    )
    occurred_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())


class RotationChannel(Base):
    __tablename__ = "rotation_channel"
    __table_args__ = (
        UniqueConstraint("channel_id", name="rotation_channel_channel_id_key"),
        Index(
            "uq_rotation_channel_invite_link",
            "invite_link",
            unique=True,
            postgresql_where=text("invite_link <> ''"),
        ),
    )
    id: Mapped[int] = mapped_column(BigInteger, primary_key=True)
    owner_id: Mapped[int] = mapped_column(ForeignKey("owner.id", ondelete="CASCADE"), index=True)
    bot_id: Mapped[int] = mapped_column(ForeignKey("managed_bot.id", ondelete="CASCADE"), index=True)
    channel_id: Mapped[int] = mapped_column(ForeignKey("channel.id", ondelete="CASCADE"), index=True)
    is_enabled: Mapped[bool] = mapped_column(Boolean, default=True, index=True)
    is_priority: Mapped[bool] = mapped_column(Boolean, default=False, index=True)
    invite_link: Mapped[str] = mapped_column(String(512), default="")
    invite_link_name: Mapped[str] = mapped_column(String(64), default="Gramly rotation")
    last_verified_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    last_error: Mapped[str] = mapped_column(String(500), default="")
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), onupdate=func.now()
    )


class RotationRecommendation(Base):
    __tablename__ = "rotation_recommendation"
    __table_args__ = (
        UniqueConstraint("departure_id", name="rotation_recommendation_departure_id_key"),
        Index("ix_rotation_recommendation_claim", "status", "due_at", "lease_expires_at"),
        CheckConstraint(
            "status IN ('scheduled','processing','retry','sent','failed','unreachable','ineligible')",
            name="ck_rotation_recommendation_status",
        ),
    )
    id: Mapped[int] = mapped_column(BigInteger, primary_key=True)
    departure_id: Mapped[int] = mapped_column(
        ForeignKey("departure_event.id", ondelete="CASCADE"), index=True
    )
    status: Mapped[str] = mapped_column(String(16), default="scheduled", index=True)
    attempts: Mapped[int] = mapped_column(Integer, default=0)
    due_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())
    lease_owner: Mapped[str | None] = mapped_column(String(64))
    lease_expires_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    sent_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    error: Mapped[str] = mapped_column(String(500), default="")
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())


class RotationImpression(Base):
    __tablename__ = "rotation_impression"
    __table_args__ = (
        UniqueConstraint(
            "recommendation_id", "destination_channel_id", name="uq_rotation_impression_destination"
        ),
    )
    id: Mapped[int] = mapped_column(BigInteger, primary_key=True)
    recommendation_id: Mapped[int] = mapped_column(
        ForeignKey("rotation_recommendation.id", ondelete="CASCADE"), index=True
    )
    source_owner_id: Mapped[int] = mapped_column(ForeignKey("owner.id", ondelete="CASCADE"), index=True)
    destination_owner_id: Mapped[int] = mapped_column(
        ForeignKey("owner.id", ondelete="CASCADE"), index=True
    )
    destination_channel_id: Mapped[int] = mapped_column(
        ForeignKey("channel.id", ondelete="CASCADE"), index=True
    )
    telegram_user_id: Mapped[int] = mapped_column(BigInteger, index=True)
    invite_link_snapshot: Mapped[str] = mapped_column(String(512))
    shown_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())


class RotationConversion(Base):
    __tablename__ = "rotation_conversion"
    __table_args__ = (
        UniqueConstraint(
            "telegram_user_id", "destination_channel_id", name="uq_rotation_conversion_user_channel"
        ),
    )
    id: Mapped[int] = mapped_column(BigInteger, primary_key=True)
    impression_id: Mapped[int] = mapped_column(
        ForeignKey("rotation_impression.id", ondelete="CASCADE"), unique=True
    )
    destination_channel_id: Mapped[int] = mapped_column(
        ForeignKey("channel.id", ondelete="CASCADE"), index=True
    )
    telegram_user_id: Mapped[int] = mapped_column(BigInteger, index=True)
    telegram_update_id: Mapped[int] = mapped_column(BigInteger)
    converted_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())


class InboxSource(Base):
    __tablename__ = "inbox_source"
    __table_args__ = (CheckConstraint("pending_count >= 0", name="ck_inbox_source_pending_count"),)
    source_key: Mapped[str] = mapped_column(String(96), primary_key=True)
    bot_id: Mapped[int | None] = mapped_column(ForeignKey("managed_bot.id", ondelete="CASCADE"), index=True)
    last_claimed_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        server_default=text("'1970-01-01 00:00:00+00'::timestamptz"),
    )
    pending_count: Mapped[int] = mapped_column(BigInteger, default=0, server_default=text("0"))
    next_available_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())


class InboxEvent(Base):
    __tablename__ = "inbox_event"
    __table_args__ = (
        UniqueConstraint("source_key", "update_id", name="uq_inbox_source_update"),
        Index(
            "ix_inbox_fair_pending",
            "source_key",
            "available_at",
            "id",
            postgresql_where=text("status IN ('pending', 'retry')"),
        ),
        Index(
            "ix_inbox_expired_lease",
            "lease_expires_at",
            "available_at",
            "id",
            postgresql_where=text("status = 'processing'"),
        ),
        Index(
            "ix_inbox_dead",
            "id",
            postgresql_where=text("status = 'dead'"),
        ),
    )
    id: Mapped[int] = mapped_column(BigInteger, primary_key=True)
    source_key: Mapped[str] = mapped_column(String(96))
    bot_id: Mapped[int | None] = mapped_column(ForeignKey("managed_bot.id", ondelete="CASCADE"), index=True)
    update_id: Mapped[int] = mapped_column(BigInteger)
    payload: Mapped[dict[str, Any]] = mapped_column(JSONB)
    status: Mapped[str] = mapped_column(String(16), default=QueueStatus.PENDING.value)
    attempts: Mapped[int] = mapped_column(Integer, default=0)
    available_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())
    lease_owner: Mapped[str | None] = mapped_column(String(64))
    lease_expires_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    last_error: Mapped[str] = mapped_column(String(500), default="")
    received_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), index=True
    )
    processed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))


class GreetingDelivery(Base):
    __tablename__ = "greeting_delivery"
    __table_args__ = (
        UniqueConstraint("bot_id", "event_key", name="uq_delivery_bot_event"),
        Index("ix_delivery_claim", "status", "due_at", "lease_expires_at"),
    )
    id: Mapped[int] = mapped_column(BigInteger, primary_key=True)
    bot_id: Mapped[int] = mapped_column(ForeignKey("managed_bot.id", ondelete="CASCADE"), index=True)
    channel_id: Mapped[int] = mapped_column(ForeignKey("channel.id", ondelete="CASCADE"))
    contact_id: Mapped[int] = mapped_column(ForeignKey("contact.id", ondelete="CASCADE"))
    version_id: Mapped[int | None] = mapped_column(
        ForeignKey("welcome_message_version.id", ondelete="SET NULL")
    )
    event_key: Mapped[str] = mapped_column(String(128))
    status: Mapped[str] = mapped_column(String(16), default="scheduled", index=True)
    delay_snapshot_seconds: Mapped[int] = mapped_column(Integer, default=0)
    attempts: Mapped[int] = mapped_column(Integer, default=0)
    due_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), index=True)
    lease_owner: Mapped[str | None] = mapped_column(String(64))
    lease_expires_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    sent_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    error: Mapped[str] = mapped_column(String(500), default="")
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())


class JoinRequest(Base):
    __tablename__ = "join_request"
    __table_args__ = (
        UniqueConstraint("bot_id", "telegram_update_id", name="uq_join_bot_update"),
        Index(
            "uq_join_open_contact",
            "channel_id",
            "contact_id",
            unique=True,
            postgresql_where=text("status IN ('pending', 'scheduled', 'processing')"),
        ),
    )
    id: Mapped[int] = mapped_column(BigInteger, primary_key=True)
    bot_id: Mapped[int] = mapped_column(ForeignKey("managed_bot.id", ondelete="CASCADE"), index=True)
    channel_id: Mapped[int] = mapped_column(ForeignKey("channel.id", ondelete="CASCADE"))
    contact_id: Mapped[int] = mapped_column(ForeignKey("contact.id", ondelete="CASCADE"))
    telegram_update_id: Mapped[int] = mapped_column(BigInteger)
    status: Mapped[str] = mapped_column(String(16), default="pending", index=True)
    delay_snapshot_seconds: Mapped[int] = mapped_column(Integer, default=0)
    due_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), index=True)
    attempts: Mapped[int] = mapped_column(Integer, default=0)
    lease_owner: Mapped[str | None] = mapped_column(String(64))
    lease_expires_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    error: Mapped[str] = mapped_column(String(500), default="")
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())
    processed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))


class EventLog(Base):
    __tablename__ = "event_log"
    id: Mapped[int] = mapped_column(BigInteger, primary_key=True)
    bot_id: Mapped[int | None] = mapped_column(ForeignKey("managed_bot.id", ondelete="CASCADE"), index=True)
    owner_id: Mapped[int | None] = mapped_column(ForeignKey("owner.id", ondelete="SET NULL"), index=True)
    event_type: Mapped[str] = mapped_column(String(64), index=True)
    level: Mapped[str] = mapped_column(String(16), default="info")
    message: Mapped[str] = mapped_column(String(500))
    context: Mapped[dict[str, Any]] = mapped_column(JSONB, default=dict)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), index=True
    )
