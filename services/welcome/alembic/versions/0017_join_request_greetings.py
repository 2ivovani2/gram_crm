"""Add short-lived join-request greeting targets.

Revision ID: 0017
Revises: 0016
"""

import sqlalchemy as sa

from alembic import op

revision = "0017"
down_revision = "0016"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column("flow_delivery", sa.Column("target_chat_id", sa.BigInteger(), nullable=True))
    op.add_column(
        "flow_delivery",
        sa.Column("target_expires_at", sa.DateTime(timezone=True), nullable=True),
    )
    op.create_index(
        "ix_flow_delivery_target_expires_at",
        "flow_delivery",
        ["target_expires_at"],
        unique=False,
    )
    op.add_column("join_request", sa.Column("user_chat_id", sa.BigInteger(), nullable=True))
    op.add_column(
        "join_request",
        sa.Column("message_window_expires_at", sa.DateTime(timezone=True), nullable=True),
    )
    op.add_column("join_request", sa.Column("welcome_delivery_id", sa.BigInteger(), nullable=True))
    op.create_index(
        "ix_join_request_welcome_delivery_id",
        "join_request",
        ["welcome_delivery_id"],
        unique=False,
    )
    op.create_foreign_key(
        "fk_join_request_welcome_delivery",
        "join_request",
        "flow_delivery",
        ["welcome_delivery_id"],
        ["id"],
        ondelete="SET NULL",
    )
    op.execute(
        """
        INSERT INTO feature_flag (key, enabled, config, description)
        VALUES (
            'join_request_greetings',
            false,
            '{"bot_ids": []}'::jsonb,
            'Send content flows to temporary Telegram join-request chats'
        )
        ON CONFLICT (key) DO NOTHING
        """
    )


def downgrade() -> None:
    op.execute("DELETE FROM feature_flag WHERE key = 'join_request_greetings'")
    op.drop_constraint("fk_join_request_welcome_delivery", "join_request", type_="foreignkey")
    op.drop_index("ix_join_request_welcome_delivery_id", table_name="join_request")
    op.drop_column("join_request", "welcome_delivery_id")
    op.drop_column("join_request", "message_window_expires_at")
    op.drop_column("join_request", "user_chat_id")
    op.drop_index("ix_flow_delivery_target_expires_at", table_name="flow_delivery")
    op.drop_column("flow_delivery", "target_expires_at")
    op.drop_column("flow_delivery", "target_chat_id")
