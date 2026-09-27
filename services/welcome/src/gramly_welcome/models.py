from __future__ import annotations

import uuid
from datetime import date, datetime
from decimal import Decimal
from enum import StrEnum
from typing import Any

from sqlalchemy import (
    BigInteger,
    Boolean,
    CheckConstraint,
    Date,
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

from .join_request_policy import OPEN_JOIN_REQUEST_SQL_PREDICATE


class Base(DeclarativeBase):
    pass


class QueueStatus(StrEnum):
    PENDING = "pending"
    PROCESSING = "processing"
    COMPLETED = "completed"
    RETRY = "retry"
    DEAD = "dead"


class OwnerControlState(Base):
    __tablename__ = "owner_control_state"
    actor_id: Mapped[int] = mapped_column(BigInteger, primary_key=True, autoincrement=False)
    revision: Mapped[int] = mapped_column(BigInteger, default=0)
    storage: Mapped[dict[str, Any]] = mapped_column(JSONB, default=dict)


class OwnerAction(Base):
    __tablename__ = "owner_action"
    update_id: Mapped[int] = mapped_column(BigInteger, primary_key=True, autoincrement=False)
    actor_id: Mapped[int] = mapped_column(BigInteger, index=True)
    outcome: Mapped[str] = mapped_column(String(24), default="applied")
    completed_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())


class OwnerReply(Base):
    __tablename__ = "owner_reply"
    update_id: Mapped[int] = mapped_column(ForeignKey("owner_action.update_id"), primary_key=True)
    actor_id: Mapped[int] = mapped_column(BigInteger, index=True)
    revision: Mapped[int] = mapped_column(BigInteger)
    # Telegram payload stays in the protected database, never in logs/metrics.
    payload: Mapped[dict[str, Any]] = mapped_column(JSONB)
    status: Mapped[str] = mapped_column(String(24), default="pending", index=True)
    attempts: Mapped[int] = mapped_column(Integer, default=0)
    available_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())
    lease_owner: Mapped[str | None] = mapped_column(String(64))
    lease_expires_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    error: Mapped[str] = mapped_column(String(64), default="")
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())
    delivered_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))


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


class OwnerServiceActivity(Base):
    __tablename__ = "owner_service_activity"
    __table_args__ = (UniqueConstraint("owner_id", "service", name="uq_owner_service_activity"),)
    id: Mapped[int] = mapped_column(BigInteger, primary_key=True)
    owner_id: Mapped[int] = mapped_column(ForeignKey("owner.id", ondelete="CASCADE"), index=True)
    service: Mapped[str] = mapped_column(String(16))
    first_seen_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())
    last_seen_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())
    last_event: Mapped[str] = mapped_column(String(48), default="start")
    cross_offer_hidden: Mapped[bool] = mapped_column(Boolean, default=False)
    cross_offer_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))


class ViewChannelSnapshot(Base):
    __tablename__ = "view_channel_snapshot"
    id: Mapped[int] = mapped_column(BigInteger, primary_key=True)
    channel_id: Mapped[int] = mapped_column(BigInteger, index=True)
    username: Mapped[str] = mapped_column(String(64), default="")
    title: Mapped[str] = mapped_column(String(255))
    subscriber_count: Mapped[int | None] = mapped_column(BigInteger)
    observed_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now(), index=True)


class ViewPostObservation(Base):
    __tablename__ = "view_post_observation"
    __table_args__ = (
        UniqueConstraint("channel_id", "post_id", "observed_at", name="uq_view_post_observation"),
    )
    id: Mapped[int] = mapped_column(BigInteger, primary_key=True)
    channel_id: Mapped[int] = mapped_column(BigInteger, index=True)
    post_id: Mapped[int] = mapped_column(BigInteger)
    published_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), index=True)
    observed_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), index=True)
    views: Mapped[int] = mapped_column(BigInteger)
    forwards: Mapped[int | None] = mapped_column(BigInteger)
    replies_count: Mapped[int | None] = mapped_column(BigInteger)
    reactions_count: Mapped[int | None] = mapped_column(BigInteger)
    source: Mapped[str] = mapped_column(String(24), default="mtproto")


class ViewCollectionRun(Base):
    __tablename__ = "view_collection_run"
    id: Mapped[int] = mapped_column(BigInteger, primary_key=True)
    channel_id: Mapped[int] = mapped_column(BigInteger, index=True)
    observed_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), index=True)
    coverage_start_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))
    complete: Mapped[bool] = mapped_column(Boolean, default=False)
    post_count: Mapped[int] = mapped_column(Integer, default=0)
    error_code: Mapped[str] = mapped_column(String(48), default="")


class ViewFavorite(Base):
    __tablename__ = "view_favorite"
    __table_args__ = (UniqueConstraint("owner_id", "channel_id", name="uq_view_favorite_owner_channel"),)
    id: Mapped[int] = mapped_column(BigInteger, primary_key=True)
    owner_id: Mapped[int] = mapped_column(ForeignKey("owner.id", ondelete="CASCADE"), index=True)
    channel_id: Mapped[int] = mapped_column(BigInteger)
    username: Mapped[str] = mapped_column(String(64), default="")
    title: Mapped[str] = mapped_column(String(255))
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())


class ViewCollection(Base):
    __tablename__ = "view_collection"
    __table_args__ = (UniqueConstraint("owner_id", "name", name="uq_view_collection_owner_name"),)
    id: Mapped[int] = mapped_column(BigInteger, primary_key=True)
    owner_id: Mapped[int] = mapped_column(ForeignKey("owner.id", ondelete="CASCADE"), index=True)
    name: Mapped[str] = mapped_column(String(80))
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())


class ViewCollectionItem(Base):
    __tablename__ = "view_collection_item"
    __table_args__ = (UniqueConstraint("collection_id", "channel_id", name="uq_view_collection_item"),)
    id: Mapped[int] = mapped_column(BigInteger, primary_key=True)
    collection_id: Mapped[int] = mapped_column(ForeignKey("view_collection.id", ondelete="CASCADE"), index=True)
    channel_id: Mapped[int] = mapped_column(BigInteger)
    username: Mapped[str] = mapped_column(String(64))
    title: Mapped[str] = mapped_column(String(255))
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())


class ViewUserState(Base):
    __tablename__ = "view_user_state"
    owner_id: Mapped[int] = mapped_column(ForeignKey("owner.id", ondelete="CASCADE"), primary_key=True)
    pending_query: Mapped[str] = mapped_column(Text, default="")
    pending_action: Mapped[str] = mapped_column(String(64), default="")
    report_channel_ids: Mapped[list[int]] = mapped_column(JSONB, default=list)
    report_key: Mapped[str] = mapped_column(String(16), default="")
    last_analysis_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())


class ViewMonitor(Base):
    __tablename__ = "view_monitor"
    __table_args__ = (UniqueConstraint("owner_id", "channel_id", name="uq_view_monitor_owner_channel"),)
    id: Mapped[int] = mapped_column(BigInteger, primary_key=True)
    owner_id: Mapped[int] = mapped_column(ForeignKey("owner.id", ondelete="CASCADE"), index=True)
    channel_id: Mapped[int] = mapped_column(BigInteger, index=True)
    enabled: Mapped[bool] = mapped_column(Boolean, default=True)
    last_sent_on: Mapped[date | None] = mapped_column(Date)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())


class ViewMtprotoAccount(Base):
    __tablename__ = "view_mtproto_account"
    id: Mapped[int] = mapped_column(BigInteger, primary_key=True)
    telegram_id: Mapped[int] = mapped_column(BigInteger, unique=True)
    label: Mapped[str] = mapped_column(String(128), default="")
    encrypted_session: Mapped[str] = mapped_column(Text)
    status: Mapped[str] = mapped_column(String(24), default="checking")
    last_error: Mapped[str] = mapped_column(String(64), default="")
    checked_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    last_used_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    last_join_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    cooldown_until: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    disabled: Mapped[bool] = mapped_column(Boolean, default=False)
    lease_owner: Mapped[str | None] = mapped_column(String(64))
    lease_until: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())


class ViewPrivateChannel(Base):
    __tablename__ = "view_private_channel"
    __table_args__ = (UniqueConstraint("owner_id", "channel_id", name="uq_view_private_owner_channel"),)
    id: Mapped[int] = mapped_column(BigInteger, primary_key=True)
    owner_id: Mapped[int] = mapped_column(ForeignKey("owner.id", ondelete="CASCADE"), index=True)
    channel_id: Mapped[int] = mapped_column(BigInteger, index=True)
    account_id: Mapped[int] = mapped_column(ForeignKey("view_mtproto_account.id"), index=True)
    invite_ciphertext: Mapped[str] = mapped_column(Text)
    invite_fingerprint: Mapped[str] = mapped_column(String(64), index=True)
    title: Mapped[str] = mapped_column(String(255))
    status: Mapped[str] = mapped_column(String(24), default="active")
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())


class ViewPoolAlert(Base):
    __tablename__ = "view_pool_alert"
    id: Mapped[int] = mapped_column(Integer, primary_key=True, default=1)
    recipient_owner_id: Mapped[int | None] = mapped_column(ForeignKey("owner.id"))
    below_threshold: Mapped[bool] = mapped_column(Boolean, default=True)
    last_notified_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())


class ViewPoolAlertRecipient(Base):
    __tablename__ = "view_pool_alert_recipient"
    owner_id: Mapped[int] = mapped_column(ForeignKey("owner.id", ondelete="CASCADE"), primary_key=True)
    last_notified_event_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    claim_until: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())


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
    calculation_price_rub: Mapped[Decimal | None] = mapped_column(Numeric(14, 2))
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


class RequiredChannelMembership(Base):
    __tablename__ = "required_channel_membership"
    __table_args__ = (
        UniqueConstraint("owner_id", "channel_id", name="uq_required_channel_owner_channel"),
        CheckConstraint(
            "status IN ('unknown','member','administrator','creator','restricted','left','kicked')",
            name="ck_required_channel_membership_status",
        ),
    )
    id: Mapped[int] = mapped_column(BigInteger, primary_key=True)
    owner_id: Mapped[int] = mapped_column(ForeignKey("owner.id", ondelete="CASCADE"), index=True)
    channel_id: Mapped[int] = mapped_column(BigInteger, index=True)
    status: Mapped[str] = mapped_column(String(24), default="unknown", index=True)
    is_member: Mapped[bool] = mapped_column(Boolean, default=False)
    checked_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), index=True)
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), onupdate=func.now()
    )


class Manual(Base):
    __tablename__ = "manual"
    __table_args__ = (UniqueConstraint("slug", name="manual_slug_key"),)
    id: Mapped[int] = mapped_column(BigInteger, primary_key=True)
    slug: Mapped[str] = mapped_column(String(64), index=True)
    title: Mapped[str] = mapped_column(String(160))
    telegraph_url: Mapped[str] = mapped_column(String(2048))
    description: Mapped[str] = mapped_column(Text, default="")
    is_onboarding: Mapped[bool] = mapped_column(Boolean, default=False, index=True)
    is_active: Mapped[bool] = mapped_column(Boolean, default=True, index=True)
    sort_order: Mapped[int] = mapped_column(Integer, default=0)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), onupdate=func.now()
    )


class LearningVideo(Base):
    __tablename__ = "learning_video"
    __table_args__ = (
        UniqueConstraint("slug", name="learning_video_slug_key"),
        CheckConstraint(
            "chapter_key IN ('first_launch','automation','account')",
            name="ck_learning_video_chapter",
        ),
    )
    id: Mapped[int] = mapped_column(BigInteger, primary_key=True)
    slug: Mapped[str] = mapped_column(String(64), index=True)
    title: Mapped[str] = mapped_column(String(160))
    description: Mapped[str] = mapped_column(Text, default="")
    video_url: Mapped[str] = mapped_column(String(2048))
    poster_url: Mapped[str] = mapped_column(String(2048), default="")
    duration_seconds: Mapped[int] = mapped_column(Integer, default=0)
    feature_key: Mapped[str] = mapped_column(String(64), default="home", index=True)
    chapter_key: Mapped[str] = mapped_column(String(32), default="first_launch", index=True)
    sort_order: Mapped[int] = mapped_column(Integer, default=0)
    is_active: Mapped[bool] = mapped_column(Boolean, default=True, index=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), onupdate=func.now()
    )


class LearningVideoProgress(Base):
    __tablename__ = "learning_video_progress"
    __table_args__ = (
        UniqueConstraint("owner_id", "video_id", name="uq_learning_video_progress_owner_video"),
        CheckConstraint("position_seconds >= 0", name="ck_learning_video_progress_position"),
    )
    id: Mapped[int] = mapped_column(BigInteger, primary_key=True)
    owner_id: Mapped[int] = mapped_column(
        ForeignKey("owner.id", ondelete="CASCADE"), index=True
    )
    video_id: Mapped[int] = mapped_column(
        ForeignKey("learning_video.id", ondelete="CASCADE"), index=True
    )
    position_seconds: Mapped[int] = mapped_column(Integer, default=0)
    completed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), index=True)
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), onupdate=func.now()
    )


class ManualSection(Base):
    __tablename__ = "manual_section"
    __table_args__ = (
        UniqueConstraint("manual_id", "slug", name="uq_manual_section_slug"),
    )
    id: Mapped[int] = mapped_column(BigInteger, primary_key=True)
    manual_id: Mapped[int] = mapped_column(ForeignKey("manual.id", ondelete="CASCADE"), index=True)
    slug: Mapped[str] = mapped_column(String(64))
    title: Mapped[str] = mapped_column(String(160))
    section_url: Mapped[str] = mapped_column(String(2048))
    sort_order: Mapped[int] = mapped_column(Integer, default=0)
    is_active: Mapped[bool] = mapped_column(Boolean, default=True, index=True)


class ContextualHelp(Base):
    __tablename__ = "contextual_help"
    __table_args__ = (UniqueConstraint("feature_key", name="contextual_help_feature_key_key"),)
    id: Mapped[int] = mapped_column(BigInteger, primary_key=True)
    feature_key: Mapped[str] = mapped_column(String(64), index=True)
    title: Mapped[str] = mapped_column(String(160))
    body: Mapped[str] = mapped_column(Text, default="")
    manual_id: Mapped[int | None] = mapped_column(
        ForeignKey("manual.id", ondelete="SET NULL"), index=True
    )
    section_id: Mapped[int | None] = mapped_column(
        ForeignKey("manual_section.id", ondelete="SET NULL"), index=True
    )
    is_active: Mapped[bool] = mapped_column(Boolean, default=True, index=True)
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), onupdate=func.now()
    )


class Announcement(Base):
    __tablename__ = "announcement"
    __table_args__ = (
        CheckConstraint("audience IN ('all','free','business')", name="ck_announcement_audience"),
        CheckConstraint("ends_at IS NULL OR ends_at > starts_at", name="ck_announcement_period"),
    )
    id: Mapped[int] = mapped_column(BigInteger, primary_key=True)
    title: Mapped[str] = mapped_column(String(160))
    body: Mapped[str] = mapped_column(Text)
    entities: Mapped[list[dict[str, Any]]] = mapped_column(JSONB, default=list)
    audience: Mapped[str] = mapped_column(String(16), default="all", index=True)
    starts_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), index=True)
    ends_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), index=True)
    is_active: Mapped[bool] = mapped_column(Boolean, default=True, index=True)
    priority: Mapped[int] = mapped_column(Integer, default=0)
    button_text: Mapped[str] = mapped_column(String(64), default="")
    button_url: Mapped[str] = mapped_column(String(2048), default="")
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), onupdate=func.now()
    )


class OwnerNotification(Base):
    __tablename__ = "owner_notification"
    __table_args__ = (
        UniqueConstraint("owner_id", "dedupe_key", name="uq_owner_notification_dedupe"),
        CheckConstraint(
            "kind IN ('onboarding','announcement','subscription_change','delivery_incident','bot_transfer')",
            name="ck_owner_notification_kind",
        ),
        CheckConstraint(
            "status IN ('pending','processing','retry','sent','failed')",
            name="ck_owner_notification_status",
        ),
        Index("ix_owner_notification_claim", "status", "due_at", "lease_expires_at"),
    )
    id: Mapped[int] = mapped_column(BigInteger, primary_key=True)
    owner_id: Mapped[int] = mapped_column(ForeignKey("owner.id", ondelete="CASCADE"), index=True)
    announcement_id: Mapped[int | None] = mapped_column(
        ForeignKey("announcement.id", ondelete="CASCADE"), index=True
    )
    kind: Mapped[str] = mapped_column(String(24), index=True)
    dedupe_key: Mapped[str] = mapped_column(String(128))
    sequence: Mapped[int] = mapped_column(Integer, default=0)
    payload: Mapped[dict[str, Any]] = mapped_column(JSONB, default=dict)
    status: Mapped[str] = mapped_column(String(16), default="pending", index=True)
    attempts: Mapped[int] = mapped_column(Integer, default=0)
    due_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), index=True)
    lease_owner: Mapped[str | None] = mapped_column(String(64))
    lease_expires_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    last_error: Mapped[str] = mapped_column(String(500), default="")
    sent_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())


class Tip(Base):
    __tablename__ = "tip"
    __table_args__ = (UniqueConstraint("feature_key", "sort_order", name="uq_tip_feature_order"),)
    id: Mapped[int] = mapped_column(BigInteger, primary_key=True)
    feature_key: Mapped[str] = mapped_column(String(64), index=True)
    text: Mapped[str] = mapped_column(Text)
    entities: Mapped[list[dict[str, Any]]] = mapped_column(JSONB, default=list)
    sort_order: Mapped[int] = mapped_column(Integer, default=0)
    is_active: Mapped[bool] = mapped_column(Boolean, default=True, index=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), onupdate=func.now()
    )


class TipViewState(Base):
    __tablename__ = "tip_view_state"
    __table_args__ = (UniqueConstraint("owner_id", "feature_key", name="uq_tip_view_owner_feature"),)
    id: Mapped[int] = mapped_column(BigInteger, primary_key=True)
    owner_id: Mapped[int] = mapped_column(ForeignKey("owner.id", ondelete="CASCADE"), index=True)
    feature_key: Mapped[str] = mapped_column(String(64))
    last_tip_id: Mapped[int | None] = mapped_column(ForeignKey("tip.id", ondelete="SET NULL"))
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), onupdate=func.now()
    )


class TipSession(Base):
    __tablename__ = "tip_session"
    __table_args__ = (UniqueConstraint("public_id", name="tip_session_public_id_key"),)
    id: Mapped[int] = mapped_column(BigInteger, primary_key=True)
    public_id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), default=uuid.uuid4)
    owner_id: Mapped[int] = mapped_column(ForeignKey("owner.id", ondelete="CASCADE"), index=True)
    feature_key: Mapped[str] = mapped_column(String(64), index=True)
    tip_ids: Mapped[list[int]] = mapped_column(JSONB, default=list)
    current_index: Mapped[int] = mapped_column(Integer, default=0)
    expires_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), index=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())


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
        Index("ix_payment_refunded_at", "refunded_at", postgresql_where=text("status = 'refunded'")),
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
    expires_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=text("(now() + interval '15 minutes')"), index=True
    )
    reconcile_after: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), index=True)
    reconcile_attempts: Mapped[int] = mapped_column(Integer, default=0)
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
    provider_reference: Mapped[str] = mapped_column(String(128), default="")
    status: Mapped[str] = mapped_column(String(16), default="received")
    attempts: Mapped[int] = mapped_column(Integer, default=0)
    available_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())
    lease_owner: Mapped[str | None] = mapped_column(String(64))
    lease_expires_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    error: Mapped[str] = mapped_column(String(500), default="")
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())
    processed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))


class SubscriptionReminder(Base):
    __tablename__ = "subscription_reminder"
    __table_args__ = (
        UniqueConstraint(
            "subscription_id", "days_before", name="uq_subscription_reminder_day"
        ),
        CheckConstraint("days_before IN (5,3,1)", name="ck_subscription_reminder_day"),
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


class MarketingCampaign(Base):
    __tablename__ = "marketing_campaign"
    __table_args__ = (
        UniqueConstraint("public_token", name="marketing_campaign_public_token_key"),
        CheckConstraint("attribution_window_days BETWEEN 1 AND 365", name="ck_campaign_window"),
        CheckConstraint("ends_at IS NULL OR ends_at > starts_at", name="ck_campaign_period"),
    )
    id: Mapped[int] = mapped_column(BigInteger, primary_key=True)
    public_token: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), default=uuid.uuid4, index=True)
    name: Mapped[str] = mapped_column(String(128))
    starts_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), index=True)
    ends_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), index=True)
    attribution_window_days: Mapped[int] = mapped_column(Integer, default=30)
    is_active: Mapped[bool] = mapped_column(Boolean, default=True, index=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), onupdate=func.now()
    )


class CampaignTouch(Base):
    __tablename__ = "campaign_touch"
    __table_args__ = (
        UniqueConstraint("campaign_id", "owner_id", name="uq_campaign_touch_owner"),
        CheckConstraint("click_count > 0", name="ck_campaign_touch_click_count"),
    )
    id: Mapped[int] = mapped_column(BigInteger, primary_key=True)
    campaign_id: Mapped[int] = mapped_column(
        ForeignKey("marketing_campaign.id", ondelete="CASCADE"), index=True
    )
    owner_id: Mapped[int] = mapped_column(ForeignKey("owner.id", ondelete="CASCADE"), index=True)
    click_count: Mapped[int] = mapped_column(Integer, default=1)
    is_new_free: Mapped[bool] = mapped_column(Boolean, default=False)
    first_clicked_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), index=True)
    last_clicked_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), index=True)


class CampaignAttribution(Base):
    __tablename__ = "campaign_attribution"
    __table_args__ = (UniqueConstraint("owner_id", name="campaign_attribution_owner_id_key"),)
    id: Mapped[int] = mapped_column(BigInteger, primary_key=True)
    campaign_id: Mapped[int] = mapped_column(
        ForeignKey("marketing_campaign.id", ondelete="RESTRICT"), index=True
    )
    owner_id: Mapped[int] = mapped_column(ForeignKey("owner.id", ondelete="CASCADE"), index=True)
    attributed_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), index=True)


class CampaignPayment(Base):
    __tablename__ = "campaign_payment"
    __table_args__ = (UniqueConstraint("payment_id", name="campaign_payment_payment_id_key"),)
    id: Mapped[int] = mapped_column(BigInteger, primary_key=True)
    campaign_id: Mapped[int] = mapped_column(
        ForeignKey("marketing_campaign.id", ondelete="RESTRICT"), index=True
    )
    owner_id: Mapped[int] = mapped_column(ForeignKey("owner.id", ondelete="CASCADE"), index=True)
    payment_id: Mapped[int] = mapped_column(ForeignKey("payment.id", ondelete="RESTRICT"), index=True)
    amount_rub: Mapped[Decimal] = mapped_column(Numeric(14, 2))
    paid_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), index=True)


class WeeklyBusinessSnapshot(Base):
    __tablename__ = "weekly_business_snapshot"
    __table_args__ = (UniqueConstraint("week_start", name="weekly_business_snapshot_week_key"),)
    id: Mapped[int] = mapped_column(BigInteger, primary_key=True)
    week_start: Mapped[date] = mapped_column(Date, index=True)
    calculation_price_rub: Mapped[Decimal] = mapped_column(Numeric(14, 2))
    active_paid_clients: Mapped[int] = mapped_column(Integer)
    first_paid_30d: Mapped[int] = mapped_column(Integer)
    churned_7d: Mapped[int] = mapped_column(Integer)
    weekly_revenue_run_rate_rub: Mapped[Decimal] = mapped_column(Numeric(14, 2))
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


class MessageTemplate(Base):
    __tablename__ = "message_template"
    __table_args__ = (
        UniqueConstraint("slug", "revision", name="uq_message_template_revision"),
        CheckConstraint("kind IN ('welcome','farewell')", name="ck_message_template_kind"),
    )
    id: Mapped[int] = mapped_column(BigInteger, primary_key=True)
    slug: Mapped[str] = mapped_column(String(80))
    revision: Mapped[int] = mapped_column(Integer)
    kind: Mapped[str] = mapped_column(String(16))
    title: Mapped[str] = mapped_column(String(128))
    author: Mapped[str] = mapped_column(String(80), default="Gramly")
    description: Mapped[str] = mapped_column(Text)
    tags: Mapped[list[str]] = mapped_column(JSONB, default=list)
    screenshots: Mapped[list[str]] = mapped_column(JSONB, default=list)
    conversion: Mapped[str | None] = mapped_column(String(80))
    requires_business: Mapped[bool] = mapped_column(Boolean, default=False)
    spec: Mapped[dict[str, Any]] = mapped_column(JSONB)
    is_active: Mapped[bool] = mapped_column(Boolean, default=True)
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
        CheckConstraint("button_style IS NULL OR button_style IN ('default','primary','success','danger')", name="ck_flow_button_style"),
        UniqueConstraint("flow_id", "version", name="uq_content_flow_version"),
        CheckConstraint(
            "status IN ('draft','published','archived')",
            name="ck_content_flow_version_status",
        ),
        CheckConstraint(
            "first_delay_seconds BETWEEN 0 AND 15552000",
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
    edit_revision: Mapped[int] = mapped_column(BigInteger, server_default=text("1"), default=1)
    status: Mapped[str] = mapped_column(String(16), index=True)
    author_telegram_id: Mapped[int] = mapped_column(BigInteger)
    first_delay_seconds: Mapped[int] = mapped_column(Integer, default=0)
    button_style: Mapped[str | None] = mapped_column(String(16))
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
            "delay_after_seconds BETWEEN 0 AND 15552000",
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


class EditorMedia(Base):
    __tablename__ = "editor_media"
    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    owner_id: Mapped[int] = mapped_column(ForeignKey("owner.id", ondelete="CASCADE"), index=True)
    descriptor: Mapped[dict[str, Any]] = mapped_column(JSONB)
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
            "action_type IN ('url','callback','text','subscribe')",
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
    # chat_join_request exposes a short-lived private chat identifier which is
    # deliberately kept separate from the stable Telegram user identifier.
    target_chat_id: Mapped[int | None] = mapped_column(BigInteger)
    target_expires_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), index=True)
    status: Mapped[str] = mapped_column(String(16), default="scheduled", index=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())
    completed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))


class DeliveryOperation(Base):
    __tablename__ = "delivery_operation"
    __table_args__ = (
        Index(
            "ix_delivery_operation_expired_window", "flow_delivery_id",
            postgresql_where=text("error = 'join_request_window_expired'"),
        ),
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


class DeliveryJob(Base):
    """Durable scheduling record; Streams only carry this row's identifier."""

    __tablename__ = "delivery_job"
    __table_args__ = (
        UniqueConstraint("source_kind", "source_id", name="uq_delivery_job_source"),
        UniqueConstraint("dedupe_key", name="uq_delivery_job_dedupe_key"),
        Index(
            "ix_delivery_job_ready",
            "lane",
            "status",
            "next_attempt_at",
            "deadline_at",
            postgresql_where=text("status IN ('pending','retry')"),
        ),
        Index(
            "ix_delivery_job_relay",
            "status",
            "signaled_at",
            "next_attempt_at",
            postgresql_where=text("status IN ('pending','retry','signaled')"),
        ),
        CheckConstraint(
            "lane IN ('urgent','interactive','standard','bulk')",
            name="ck_delivery_job_lane",
        ),
        CheckConstraint(
            "status IN ('pending','signaled','processing','retry','completed','failed','cancelled')",
            name="ck_delivery_job_status",
        ),
    )
    id: Mapped[int] = mapped_column(BigInteger, primary_key=True)
    source_kind: Mapped[str] = mapped_column(String(32))
    source_id: Mapped[int] = mapped_column(BigInteger)
    owner_id: Mapped[int | None] = mapped_column(ForeignKey("owner.id", ondelete="SET NULL"), index=True)
    bot_id: Mapped[int | None] = mapped_column(
        ForeignKey("managed_bot.id", ondelete="SET NULL"), index=True
    )
    chat_id: Mapped[int | None] = mapped_column(BigInteger)
    lane: Mapped[str] = mapped_column(String(16), default="standard")
    priority: Mapped[int] = mapped_column(Integer, default=100)
    status: Mapped[str] = mapped_column(String(16), default="pending", index=True)
    due_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), index=True)
    next_attempt_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), index=True)
    deadline_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), index=True)
    attempts: Mapped[int] = mapped_column(Integer, default=0)
    lease_owner: Mapped[str | None] = mapped_column(String(96))
    lease_expires_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    signaled_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    stream_id: Mapped[str | None] = mapped_column(String(64))
    dedupe_key: Mapped[str] = mapped_column(String(128))
    error_code: Mapped[str] = mapped_column(String(96), default="")
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), onupdate=func.now()
    )


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
    delivery_issues: Mapped[dict[str, Any]] = mapped_column(JSONB, default=dict)
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
    __table_args__ = (
        Index(
            "ix_contact_active_consent_dashboard", "consented_at", "bot_id", "telegram_id",
            postgresql_where=text("consent_status = 'active' AND bot_started AND delivery_status <> 'dead'"),
        ),
        UniqueConstraint("bot_id", "telegram_id", name="uq_contact_bot_telegram"),
        CheckConstraint(
            "consent_status IN ('none','active','unsubscribed','blocked')",
            name="ck_contact_consent_status",
        ),
    )
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
    consent_status: Mapped[str] = mapped_column(String(16), default="none", index=True)
    consented_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), index=True)
    consent_version: Mapped[str] = mapped_column(String(32), default="")
    consent_source: Mapped[str] = mapped_column(String(128), default="")
    source_channel_id: Mapped[int | None] = mapped_column(
        ForeignKey("channel.id", ondelete="SET NULL"), index=True
    )
    unsubscribed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), index=True)
    first_seen_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())
    last_seen_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), onupdate=func.now()
    )
    last_delivery_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    last_error: Mapped[str] = mapped_column(String(500), default="")


class SubscriberChallenge(Base):
    __tablename__ = "subscriber_challenge"
    __table_args__ = (
        Index(
            "ix_subscriber_challenge_passed_at", "completed_at", "contact_id",
            postgresql_where=text("status = 'passed'"),
        ),
        UniqueConstraint("public_token", name="subscriber_challenge_public_token_key"),
        CheckConstraint(
            "status IN ('pending','passed','expired','failed')",
            name="ck_subscriber_challenge_status",
        ),
    )
    id: Mapped[int] = mapped_column(BigInteger, primary_key=True)
    public_token: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), default=uuid.uuid4, index=True)
    bot_id: Mapped[int] = mapped_column(ForeignKey("managed_bot.id", ondelete="CASCADE"), index=True)
    contact_id: Mapped[int] = mapped_column(ForeignKey("contact.id", ondelete="CASCADE"), index=True)
    step_id: Mapped[int | None] = mapped_column(
        ForeignKey("content_step.id", ondelete="SET NULL"), index=True
    )
    source_channel_id: Mapped[int | None] = mapped_column(
        ForeignKey("channel.id", ondelete="SET NULL"), index=True
    )
    answer: Mapped[str] = mapped_column(String(16))
    options: Mapped[list[str]] = mapped_column(JSONB, default=list)
    status: Mapped[str] = mapped_column(String(16), default="pending", index=True)
    attempts: Mapped[int] = mapped_column(Integer, default=0)
    expires_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), index=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())
    completed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))


class ClientMessage(Base):
    __tablename__ = "client_message"
    __table_args__ = (
        CheckConstraint(
            "status IN ('scheduled','processing','retry','sent','failed','cancelled')",
            name="ck_client_message_status",
        ),
        Index("ix_client_message_claim", "status", "due_at", "lease_expires_at"),
    )
    id: Mapped[int] = mapped_column(BigInteger, primary_key=True)
    bot_id: Mapped[int] = mapped_column(ForeignKey("managed_bot.id", ondelete="CASCADE"), index=True)
    contact_id: Mapped[int] = mapped_column(ForeignKey("contact.id", ondelete="CASCADE"), index=True)
    payload: Mapped[dict[str, Any]] = mapped_column(JSONB, default=dict)
    status: Mapped[str] = mapped_column(String(16), default="scheduled", index=True)
    attempts: Mapped[int] = mapped_column(Integer, default=0)
    due_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now(), index=True)
    lease_owner: Mapped[str | None] = mapped_column(String(64))
    lease_expires_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    sent_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    error: Mapped[str] = mapped_column(String(64), default="")


class Broadcast(Base):
    __tablename__ = "broadcast"
    __table_args__ = (
        UniqueConstraint("owner_id", "idempotency_key", name="uq_broadcast_owner_key"),
        CheckConstraint(
            "status IN ('draft','scheduled','running','completed','cancelled','failed')",
            name="ck_broadcast_status",
        ),
    )
    id: Mapped[int] = mapped_column(BigInteger, primary_key=True)
    owner_id: Mapped[int] = mapped_column(ForeignKey("owner.id", ondelete="CASCADE"), index=True)
    bot_id: Mapped[int] = mapped_column(ForeignKey("managed_bot.id", ondelete="CASCADE"), index=True)
    source_channel_id: Mapped[int | None] = mapped_column(
        ForeignKey("channel.id", ondelete="SET NULL"), index=True
    )
    name: Mapped[str] = mapped_column(String(128))
    content: Mapped[dict[str, Any]] = mapped_column(JSONB, default=dict)
    status: Mapped[str] = mapped_column(String(16), default="draft", index=True)
    idempotency_key: Mapped[str] = mapped_column(String(128))
    scheduled_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), index=True)
    started_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    completed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), onupdate=func.now()
    )


class BroadcastRecipient(Base):
    __tablename__ = "broadcast_recipient"
    __table_args__ = (
        UniqueConstraint("broadcast_id", "contact_id", name="uq_broadcast_recipient_contact"),
        CheckConstraint(
            "status IN ('scheduled','processing','retry','sent','failed','blocked','unsubscribed','cancelled')",
            name="ck_broadcast_recipient_status",
        ),
        Index("ix_broadcast_recipient_claim", "status", "due_at", "lease_expires_at"),
    )
    id: Mapped[int] = mapped_column(BigInteger, primary_key=True)
    broadcast_id: Mapped[int] = mapped_column(
        ForeignKey("broadcast.id", ondelete="CASCADE"), index=True
    )
    contact_id: Mapped[int] = mapped_column(ForeignKey("contact.id", ondelete="CASCADE"), index=True)
    status: Mapped[str] = mapped_column(String(16), default="scheduled", index=True)
    attempts: Mapped[int] = mapped_column(Integer, default=0)
    due_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), index=True)
    lease_owner: Mapped[str | None] = mapped_column(String(64))
    lease_expires_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    sent_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    error: Mapped[str] = mapped_column(String(64), default="")


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
    actor_id: Mapped[int | None] = mapped_column(BigInteger)
    __table_args__ = (
        UniqueConstraint("source_key", "update_id", name="uq_inbox_source_update"),
        Index("ix_inbox_owner_actor_open", "actor_id", "update_id",
              postgresql_where=text("bot_id IS NULL AND status IN ('pending','retry','processing')")),
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
            postgresql_where=text(OPEN_JOIN_REQUEST_SQL_PREDICATE),
        ),
    )
    id: Mapped[int] = mapped_column(BigInteger, primary_key=True)
    bot_id: Mapped[int] = mapped_column(ForeignKey("managed_bot.id", ondelete="CASCADE"), index=True)
    channel_id: Mapped[int] = mapped_column(ForeignKey("channel.id", ondelete="CASCADE"))
    contact_id: Mapped[int] = mapped_column(ForeignKey("contact.id", ondelete="CASCADE"))
    telegram_update_id: Mapped[int] = mapped_column(BigInteger)
    user_chat_id: Mapped[int | None] = mapped_column(BigInteger)
    message_window_expires_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    welcome_delivery_id: Mapped[int | None] = mapped_column(
        ForeignKey("flow_delivery.id", ondelete="SET NULL"), index=True
    )
    status: Mapped[str] = mapped_column(String(16), default="pending", index=True)
    delay_snapshot_seconds: Mapped[int] = mapped_column(Integer, default=0)
    due_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), index=True)
    attempts: Mapped[int] = mapped_column(Integer, default=0)
    lease_owner: Mapped[str | None] = mapped_column(String(64))
    lease_expires_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    error: Mapped[str] = mapped_column(String(500), default="")
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())
    processed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))


class JoinRequestActivation(Base):
    __tablename__ = "join_request_activation"
    __table_args__ = (
        UniqueConstraint("join_request_id", name="uq_join_request_activation_request"),
        UniqueConstraint("public_id", name="uq_join_request_activation_public"),
        CheckConstraint(
            "status IN ('pending','processing','sent','granted','expired','failed','approved_without_activation')",
            name="ck_join_request_activation_status",
        ),
    )
    id: Mapped[int] = mapped_column(BigInteger, primary_key=True)
    public_id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), index=True)
    join_request_id: Mapped[int] = mapped_column(
        ForeignKey("join_request.id", ondelete="CASCADE"), index=True
    )
    bot_id: Mapped[int] = mapped_column(ForeignKey("managed_bot.id", ondelete="CASCADE"), index=True)
    contact_id: Mapped[int] = mapped_column(ForeignKey("contact.id", ondelete="CASCADE"), index=True)
    token_digest: Mapped[str] = mapped_column(String(64))
    status: Mapped[str] = mapped_column(String(40), default="pending", index=True)
    due_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), index=True)
    expires_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), index=True)
    attempts: Mapped[int] = mapped_column(Integer, default=0)
    lease_owner: Mapped[str | None] = mapped_column(String(64))
    lease_expires_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    sent_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    granted_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    delivery_id: Mapped[int | None] = mapped_column(
        ForeignKey("flow_delivery.id", ondelete="SET NULL"), index=True
    )
    error: Mapped[str] = mapped_column(String(500), default="")
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())


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
