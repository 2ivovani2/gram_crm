"""Audience consent, broadcasts, learning videos and per-button styles."""

import sqlalchemy as sa
from sqlalchemy.dialects import postgresql

from alembic import op

revision = "0027"
down_revision = "0026"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column("contact", sa.Column("consent_status", sa.String(16), server_default="none", nullable=False))
    op.add_column("contact", sa.Column("consented_at", sa.DateTime(timezone=True)))
    op.add_column("contact", sa.Column("consent_version", sa.String(32), server_default="", nullable=False))
    op.add_column("contact", sa.Column("consent_source", sa.String(128), server_default="", nullable=False))
    op.add_column("contact", sa.Column("source_channel_id", sa.BigInteger(), sa.ForeignKey("channel.id", ondelete="SET NULL")))
    op.add_column("contact", sa.Column("unsubscribed_at", sa.DateTime(timezone=True)))
    op.create_check_constraint(
        "ck_contact_consent_status", "contact",
        "consent_status IN ('none','active','unsubscribed','blocked')",
    )
    for column in ("consent_status", "consented_at", "source_channel_id", "unsubscribed_at"):
        op.create_index(f"ix_contact_{column}", "contact", [column])

    op.create_table(
        "learning_video",
        sa.Column("id", sa.BigInteger(), primary_key=True),
        sa.Column("slug", sa.String(64), nullable=False),
        sa.Column("title", sa.String(160), nullable=False),
        sa.Column("description", sa.Text(), server_default="", nullable=False),
        sa.Column("video_url", sa.String(2048), nullable=False),
        sa.Column("poster_url", sa.String(2048), server_default="", nullable=False),
        sa.Column("duration_seconds", sa.Integer(), server_default="0", nullable=False),
        sa.Column("feature_key", sa.String(64), server_default="home", nullable=False),
        sa.Column("sort_order", sa.Integer(), server_default="0", nullable=False),
        sa.Column("is_active", sa.Boolean(), server_default=sa.true(), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
        sa.UniqueConstraint("slug", name="learning_video_slug_key"),
    )
    op.create_index("ix_learning_video_slug", "learning_video", ["slug"])
    op.create_index("ix_learning_video_feature_key", "learning_video", ["feature_key"])
    op.create_index("ix_learning_video_is_active", "learning_video", ["is_active"])

    op.create_table(
        "subscriber_challenge",
        sa.Column("id", sa.BigInteger(), primary_key=True),
        sa.Column("public_token", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("bot_id", sa.BigInteger(), sa.ForeignKey("managed_bot.id", ondelete="CASCADE"), nullable=False),
        sa.Column("contact_id", sa.BigInteger(), sa.ForeignKey("contact.id", ondelete="CASCADE"), nullable=False),
        sa.Column("step_id", sa.BigInteger(), sa.ForeignKey("content_step.id", ondelete="SET NULL")),
        sa.Column("source_channel_id", sa.BigInteger(), sa.ForeignKey("channel.id", ondelete="SET NULL")),
        sa.Column("answer", sa.String(16), nullable=False),
        sa.Column("options", postgresql.JSONB(), nullable=False),
        sa.Column("status", sa.String(16), server_default="pending", nullable=False),
        sa.Column("attempts", sa.Integer(), server_default="0", nullable=False),
        sa.Column("expires_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
        sa.Column("completed_at", sa.DateTime(timezone=True)),
        sa.UniqueConstraint("public_token", name="subscriber_challenge_public_token_key"),
        sa.CheckConstraint("status IN ('pending','passed','expired','failed')", name="ck_subscriber_challenge_status"),
    )
    for column in ("public_token", "bot_id", "contact_id", "step_id", "source_channel_id", "status", "expires_at"):
        op.create_index(f"ix_subscriber_challenge_{column}", "subscriber_challenge", [column])

    op.create_table(
        "client_message",
        sa.Column("id", sa.BigInteger(), primary_key=True),
        sa.Column("bot_id", sa.BigInteger(), sa.ForeignKey("managed_bot.id", ondelete="CASCADE"), nullable=False),
        sa.Column("contact_id", sa.BigInteger(), sa.ForeignKey("contact.id", ondelete="CASCADE"), nullable=False),
        sa.Column("payload", postgresql.JSONB(), nullable=False),
        sa.Column("status", sa.String(16), server_default="scheduled", nullable=False),
        sa.Column("attempts", sa.Integer(), server_default="0", nullable=False),
        sa.Column("due_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
        sa.Column("lease_owner", sa.String(64)),
        sa.Column("lease_expires_at", sa.DateTime(timezone=True)),
        sa.Column("sent_at", sa.DateTime(timezone=True)),
        sa.Column("error", sa.String(64), server_default="", nullable=False),
        sa.CheckConstraint("status IN ('scheduled','processing','retry','sent','failed','cancelled')", name="ck_client_message_status"),
    )
    op.create_index("ix_client_message_bot_id", "client_message", ["bot_id"])
    op.create_index("ix_client_message_contact_id", "client_message", ["contact_id"])
    op.create_index("ix_client_message_status", "client_message", ["status"])
    op.create_index("ix_client_message_due_at", "client_message", ["due_at"])
    op.create_index("ix_client_message_claim", "client_message", ["status", "due_at", "lease_expires_at"])

    op.create_table(
        "broadcast",
        sa.Column("id", sa.BigInteger(), primary_key=True),
        sa.Column("owner_id", sa.BigInteger(), sa.ForeignKey("owner.id", ondelete="CASCADE"), nullable=False),
        sa.Column("bot_id", sa.BigInteger(), sa.ForeignKey("managed_bot.id", ondelete="CASCADE"), nullable=False),
        sa.Column("source_channel_id", sa.BigInteger(), sa.ForeignKey("channel.id", ondelete="SET NULL")),
        sa.Column("name", sa.String(128), nullable=False),
        sa.Column("content", postgresql.JSONB(), nullable=False),
        sa.Column("status", sa.String(16), server_default="draft", nullable=False),
        sa.Column("idempotency_key", sa.String(128), nullable=False),
        sa.Column("scheduled_at", sa.DateTime(timezone=True)),
        sa.Column("started_at", sa.DateTime(timezone=True)),
        sa.Column("completed_at", sa.DateTime(timezone=True)),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
        sa.UniqueConstraint("owner_id", "idempotency_key", name="uq_broadcast_owner_key"),
        sa.CheckConstraint("status IN ('draft','scheduled','running','completed','cancelled','failed')", name="ck_broadcast_status"),
    )
    for column in ("owner_id", "bot_id", "source_channel_id", "status", "scheduled_at"):
        op.create_index(f"ix_broadcast_{column}", "broadcast", [column])

    op.create_table(
        "broadcast_recipient",
        sa.Column("id", sa.BigInteger(), primary_key=True),
        sa.Column("broadcast_id", sa.BigInteger(), sa.ForeignKey("broadcast.id", ondelete="CASCADE"), nullable=False),
        sa.Column("contact_id", sa.BigInteger(), sa.ForeignKey("contact.id", ondelete="CASCADE"), nullable=False),
        sa.Column("status", sa.String(16), server_default="scheduled", nullable=False),
        sa.Column("attempts", sa.Integer(), server_default="0", nullable=False),
        sa.Column("due_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("lease_owner", sa.String(64)),
        sa.Column("lease_expires_at", sa.DateTime(timezone=True)),
        sa.Column("sent_at", sa.DateTime(timezone=True)),
        sa.Column("error", sa.String(64), server_default="", nullable=False),
        sa.UniqueConstraint("broadcast_id", "contact_id", name="uq_broadcast_recipient_contact"),
        sa.CheckConstraint("status IN ('scheduled','processing','retry','sent','failed','blocked','unsubscribed','cancelled')", name="ck_broadcast_recipient_status"),
    )
    op.create_index("ix_broadcast_recipient_broadcast_id", "broadcast_recipient", ["broadcast_id"])
    op.create_index("ix_broadcast_recipient_contact_id", "broadcast_recipient", ["contact_id"])
    op.create_index("ix_broadcast_recipient_status", "broadcast_recipient", ["status"])
    op.create_index("ix_broadcast_recipient_due_at", "broadcast_recipient", ["due_at"])
    op.create_index("ix_broadcast_recipient_claim", "broadcast_recipient", ["status", "due_at", "lease_expires_at"])

    op.drop_constraint("ck_content_keyboard_button_action", "content_keyboard_button", type_="check")
    op.create_check_constraint(
        "ck_content_keyboard_button_action", "content_keyboard_button",
        "action_type IN ('url','callback','text','subscribe')",
    )
    op.execute("""
        UPDATE content_keyboard_button AS button
        SET style = version.button_style
        FROM content_keyboard AS keyboard, content_step AS step, content_flow_version AS version
        WHERE button.keyboard_id = keyboard.id
          AND keyboard.step_id = step.id
          AND step.version_id = version.id
          AND version.button_style IS NOT NULL
    """)
    op.execute("UPDATE content_flow_version SET button_style = NULL WHERE button_style IS NOT NULL")


def downgrade() -> None:
    op.execute("DELETE FROM content_keyboard_button WHERE action_type = 'subscribe'")
    op.drop_constraint("ck_content_keyboard_button_action", "content_keyboard_button", type_="check")
    op.create_check_constraint(
        "ck_content_keyboard_button_action", "content_keyboard_button",
        "action_type IN ('url','callback','text')",
    )
    for table in ("broadcast_recipient", "broadcast", "client_message", "subscriber_challenge", "learning_video"):
        op.drop_table(table)
    for column in ("unsubscribed_at", "source_channel_id", "consent_source", "consent_version", "consented_at", "consent_status"):
        op.drop_column("contact", column)
