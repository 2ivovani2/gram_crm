"""personalization delays and required channel membership

Revision ID: 0018
Revises: 0017
"""

from alembic import op

revision = "0018"
down_revision = "0017"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.drop_constraint("ck_content_flow_first_delay", "content_flow_version", type_="check")
    op.create_check_constraint(
        "ck_content_flow_first_delay",
        "content_flow_version",
        "first_delay_seconds BETWEEN 0 AND 15552000",
    )
    op.drop_constraint("ck_content_step_delay", "content_step", type_="check")
    op.create_check_constraint(
        "ck_content_step_delay",
        "content_step",
        "delay_after_seconds BETWEEN 0 AND 15552000",
    )
    op.execute(
        """
        CREATE TABLE required_channel_membership (
          id BIGSERIAL PRIMARY KEY,
          owner_id BIGINT NOT NULL REFERENCES owner(id) ON DELETE CASCADE,
          channel_id BIGINT NOT NULL,
          status VARCHAR(24) NOT NULL DEFAULT 'unknown',
          is_member BOOLEAN NOT NULL DEFAULT false,
          checked_at TIMESTAMPTZ,
          updated_at TIMESTAMPTZ NOT NULL DEFAULT now(),
          CONSTRAINT uq_required_channel_owner_channel UNIQUE (owner_id, channel_id),
          CONSTRAINT ck_required_channel_membership_status CHECK (
            status IN ('unknown','member','administrator','creator','restricted','left','kicked')
          )
        )
        """
    )
    op.create_index(
        "ix_required_channel_membership_owner_id", "required_channel_membership", ["owner_id"]
    )
    op.create_index(
        "ix_required_channel_membership_channel_id", "required_channel_membership", ["channel_id"]
    )
    op.create_index(
        "ix_required_channel_membership_status", "required_channel_membership", ["status"]
    )
    op.create_index(
        "ix_required_channel_membership_checked_at", "required_channel_membership", ["checked_at"]
    )
    op.execute(
        """
        INSERT INTO feature_flag (key, enabled, config, description)
        VALUES (
          'required_news_channel', false,
          '{"channel_id": -1004404255750, "title": "GRAMLY | Новости", "url": "https://t.me/+gSu2vmXMeWBmZjI6"}'::jsonb,
          'Require GramlyHello owners to subscribe to the configured Telegram channel'
        )
        ON CONFLICT (key) DO NOTHING
        """
    )


def downgrade() -> None:
    op.execute("DELETE FROM feature_flag WHERE key = 'required_news_channel'")
    op.drop_table("required_channel_membership")
    op.drop_constraint("ck_content_step_delay", "content_step", type_="check")
    op.create_check_constraint(
        "ck_content_step_delay",
        "content_step",
        "delay_after_seconds BETWEEN 0 AND 86400",
    )
    op.drop_constraint("ck_content_flow_first_delay", "content_flow_version", type_="check")
    op.create_check_constraint(
        "ck_content_flow_first_delay",
        "content_flow_version",
        "first_delay_seconds BETWEEN 0 AND 86400",
    )
