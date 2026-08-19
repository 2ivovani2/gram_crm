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
        CheckConstraint("ends_at > starts_at", name="ck_subscription_period"),
        CheckConstraint(
            "status IN ('trialing','active','past_due','expired','canceled')",
            name="ck_subscription_status",
        ),
        CheckConstraint(
            "source IN ('trial','crypto_pay','telegram_stars','manual')",
            name="ck_subscription_source",
        ),
    )
    id: Mapped[int] = mapped_column(BigInteger, primary_key=True)
    owner_id: Mapped[int] = mapped_column(
        ForeignKey("owner.id", ondelete="CASCADE"), index=True
    )
    plan_id: Mapped[int] = mapped_column(ForeignKey("plan.id", ondelete="RESTRICT"), index=True)
    source: Mapped[str] = mapped_column(String(24))
    status: Mapped[str] = mapped_column(String(24), index=True)
    starts_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))
    ends_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), index=True)
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
    owner_id: Mapped[int] = mapped_column(
        ForeignKey("owner.id", ondelete="CASCADE"), index=True
    )
    token_hash: Mapped[str] = mapped_column(String(64), unique=True)
    csrf_hash: Mapped[str] = mapped_column(String(64))
    telegram_auth_date: Mapped[datetime] = mapped_column(DateTime(timezone=True))
    expires_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), index=True)
    revoked_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())


class IdempotencyRecord(Base):
    __tablename__ = "idempotency_record"
    __table_args__ = (
        UniqueConstraint("owner_id", "key", name="uq_idempotency_owner_key"),
    )
    id: Mapped[int] = mapped_column(BigInteger, primary_key=True)
    owner_id: Mapped[int] = mapped_column(
        ForeignKey("owner.id", ondelete="CASCADE"), index=True
    )
    key: Mapped[str] = mapped_column(String(128))
    request_hash: Mapped[str] = mapped_column(String(64))
    response_status: Mapped[int | None] = mapped_column(Integer)
    response_body: Mapped[dict[str, Any] | None] = mapped_column(JSONB)
    expires_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), index=True)
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
    bot_id: Mapped[int] = mapped_column(
        ForeignKey("managed_bot.id", ondelete="CASCADE"), index=True
    )
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
    flow_id: Mapped[int] = mapped_column(
        ForeignKey("content_flow.id", ondelete="CASCADE"), index=True
    )
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
    step_id: Mapped[int] = mapped_column(
        ForeignKey("content_step.id", ondelete="CASCADE"), index=True
    )
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
    step_id: Mapped[int] = mapped_column(
        ForeignKey("content_step.id", ondelete="CASCADE"), index=True
    )
    kind: Mapped[str] = mapped_column(String(16))
    settings: Mapped[dict[str, Any]] = mapped_column(JSONB, default=dict)


class ContentKeyboardButton(Base):
    __tablename__ = "content_keyboard_button"
    __table_args__ = (
        UniqueConstraint(
            "keyboard_id", "row", "position", name="uq_content_keyboard_button_position"
        ),
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
    __table_args__ = (
        UniqueConstraint("flow_id", "channel_id", name="uq_flow_channel_assignment"),
    )
    id: Mapped[int] = mapped_column(BigInteger, primary_key=True)
    flow_id: Mapped[int] = mapped_column(
        ForeignKey("content_flow.id", ondelete="CASCADE"), index=True
    )
    channel_id: Mapped[int] = mapped_column(
        ForeignKey("channel.id", ondelete="CASCADE"), index=True
    )
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())


class FlowDelivery(Base):
    __tablename__ = "flow_delivery"
    __table_args__ = (
        UniqueConstraint("bot_id", "event_key", name="uq_flow_delivery_bot_event"),
        CheckConstraint(
            "status IN ('scheduled','processing','completed','partial','failed','cancelled')",
            name="ck_flow_delivery_status",
        ),
    )
    id: Mapped[int] = mapped_column(BigInteger, primary_key=True)
    bot_id: Mapped[int] = mapped_column(
        ForeignKey("managed_bot.id", ondelete="CASCADE"), index=True
    )
    channel_id: Mapped[int] = mapped_column(
        ForeignKey("channel.id", ondelete="CASCADE"), index=True
    )
    contact_id: Mapped[int] = mapped_column(
        ForeignKey("contact.id", ondelete="CASCADE"), index=True
    )
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
    step_id: Mapped[int] = mapped_column(
        ForeignKey("content_step.id", ondelete="RESTRICT"), index=True
    )
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
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now(), onupdate=func.now())


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
    version_id: Mapped[int] = mapped_column(ForeignKey("welcome_message_version.id", ondelete="CASCADE"), index=True)
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
    bot_id: Mapped[int] = mapped_column(
        ForeignKey("managed_bot.id", ondelete="CASCADE"), index=True
    )
    owner_id: Mapped[int] = mapped_column(
        ForeignKey("owner.id", ondelete="CASCADE"), index=True
    )
    media_group_id: Mapped[str] = mapped_column(String(128))
    payload: Mapped[dict[str, Any]] = mapped_column(JSONB, default=dict)
    finalize_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))
    finalized_version_id: Mapped[int | None] = mapped_column(
        ForeignKey("welcome_message_version.id", ondelete="SET NULL"), index=True
    )
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now()
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), onupdate=func.now()
    )


class WelcomeDraftMedia(Base):
    __tablename__ = "welcome_draft_media"
    __table_args__ = (
        UniqueConstraint("draft_id", "telegram_message_id", name="uq_welcome_draft_message"),
    )
    id: Mapped[int] = mapped_column(BigInteger, primary_key=True)
    draft_id: Mapped[int] = mapped_column(
        ForeignKey("welcome_draft.id", ondelete="CASCADE"), index=True
    )
    telegram_message_id: Mapped[int] = mapped_column(BigInteger)
    media_type: Mapped[str] = mapped_column(String(32))
    storage_key: Mapped[str] = mapped_column(String(500))
    original_name: Mapped[str] = mapped_column(String(255), default="")
    mime_type: Mapped[str] = mapped_column(String(128), default="")
    size: Mapped[int] = mapped_column(BigInteger, default=0)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now()
    )


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
    last_seen_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now(), onupdate=func.now())
    last_delivery_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    last_error: Mapped[str] = mapped_column(String(500), default="")


class InboxSource(Base):
    __tablename__ = "inbox_source"
    __table_args__ = (
        CheckConstraint("pending_count >= 0", name="ck_inbox_source_pending_count"),
    )
    source_key: Mapped[str] = mapped_column(String(96), primary_key=True)
    bot_id: Mapped[int | None] = mapped_column(
        ForeignKey("managed_bot.id", ondelete="CASCADE"), index=True
    )
    last_claimed_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        server_default=text("'1970-01-01 00:00:00+00'::timestamptz"),
    )
    pending_count: Mapped[int] = mapped_column(BigInteger, default=0, server_default=text("0"))
    next_available_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now()
    )


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
    received_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now(), index=True)
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
    version_id: Mapped[int | None] = mapped_column(ForeignKey("welcome_message_version.id", ondelete="SET NULL"))
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
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now(), index=True)
