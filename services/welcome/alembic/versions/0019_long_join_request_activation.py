"""durable activation for long join-request chains

Revision ID: 0019
Revises: 0018
"""

from alembic import op

revision = "0019"
down_revision = "0018"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.execute(
        """
        CREATE TABLE join_request_activation (
          id BIGSERIAL PRIMARY KEY,
          public_id UUID NOT NULL,
          join_request_id BIGINT NOT NULL REFERENCES join_request(id) ON DELETE CASCADE,
          bot_id BIGINT NOT NULL REFERENCES managed_bot(id) ON DELETE CASCADE,
          contact_id BIGINT NOT NULL REFERENCES contact(id) ON DELETE CASCADE,
          token_digest VARCHAR(64) NOT NULL,
          status VARCHAR(40) NOT NULL DEFAULT 'pending',
          due_at TIMESTAMPTZ NOT NULL,
          expires_at TIMESTAMPTZ NOT NULL,
          attempts INTEGER NOT NULL DEFAULT 0,
          lease_owner VARCHAR(64),
          lease_expires_at TIMESTAMPTZ,
          sent_at TIMESTAMPTZ,
          granted_at TIMESTAMPTZ,
          delivery_id BIGINT REFERENCES flow_delivery(id) ON DELETE SET NULL,
          error VARCHAR(500) NOT NULL DEFAULT '',
          created_at TIMESTAMPTZ NOT NULL DEFAULT now(),
          CONSTRAINT uq_join_request_activation_request UNIQUE (join_request_id),
          CONSTRAINT uq_join_request_activation_public UNIQUE (public_id),
          CONSTRAINT ck_join_request_activation_status CHECK (
            status IN ('pending','processing','sent','granted','expired','failed','approved_without_activation')
          )
        )
        """
    )
    for name, columns in (
        ("ix_join_request_activation_public_id", ["public_id"]),
        ("ix_join_request_activation_join_request_id", ["join_request_id"]),
        ("ix_join_request_activation_bot_id", ["bot_id"]),
        ("ix_join_request_activation_contact_id", ["contact_id"]),
        ("ix_join_request_activation_status", ["status"]),
        ("ix_join_request_activation_due_at", ["due_at"]),
        ("ix_join_request_activation_expires_at", ["expires_at"]),
        ("ix_join_request_activation_delivery_id", ["delivery_id"]),
    ):
        op.create_index(name, "join_request_activation", columns)
    op.execute(
        """
        INSERT INTO feature_flag (key, enabled, config, description)
        VALUES (
          'join_request_long_flow_activation', false, '{}'::jsonb,
          'Request persistent client-bot write access for join-request chains longer than four minutes'
        )
        ON CONFLICT (key) DO NOTHING
        """
    )


def downgrade() -> None:
    op.execute("DELETE FROM feature_flag WHERE key = 'join_request_long_flow_activation'")
    op.drop_table("join_request_activation")
