"""Durable owner action receipts, screens and actor-level scheduling."""

from alembic import op

revision = "0022"
down_revision = "0021"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.execute("ALTER TABLE inbox_event ADD COLUMN actor_id BIGINT")
    op.execute("""
        UPDATE inbox_event SET actor_id = COALESCE(
          (payload->'message'->'from'->>'id')::bigint,
          (payload->'callback_query'->'from'->>'id')::bigint,
          (payload->'pre_checkout_query'->'from'->>'id')::bigint,
          (payload->'chat_member'->'from'->>'id')::bigint,
          (payload->'my_chat_member'->'from'->>'id')::bigint, -update_id)
        WHERE bot_id IS NULL AND status IN ('pending','retry','processing')
    """)
    op.execute("""CREATE INDEX ix_inbox_owner_actor_open ON inbox_event(actor_id, update_id)
        WHERE bot_id IS NULL AND status IN ('pending','retry','processing')""")
    op.execute("""CREATE TABLE owner_control_state (
        actor_id BIGINT PRIMARY KEY, revision BIGINT NOT NULL DEFAULT 0,
        storage JSONB NOT NULL DEFAULT '{}'::jsonb)""")
    op.execute("""CREATE TABLE owner_action (
        update_id BIGINT PRIMARY KEY, actor_id BIGINT NOT NULL,
        outcome VARCHAR(24) NOT NULL DEFAULT 'applied', completed_at TIMESTAMPTZ NOT NULL DEFAULT now())""")
    op.execute("CREATE INDEX ix_owner_action_actor_id ON owner_action(actor_id)")
    op.execute("""CREATE TABLE owner_reply (
        update_id BIGINT PRIMARY KEY REFERENCES owner_action(update_id),
        actor_id BIGINT NOT NULL, revision BIGINT NOT NULL, payload JSONB NOT NULL,
        status VARCHAR(24) NOT NULL DEFAULT 'pending', attempts INTEGER NOT NULL DEFAULT 0,
        available_at TIMESTAMPTZ NOT NULL DEFAULT now(), lease_owner VARCHAR(64), lease_expires_at TIMESTAMPTZ,
        error VARCHAR(64) NOT NULL DEFAULT '', created_at TIMESTAMPTZ NOT NULL DEFAULT now(),
        delivered_at TIMESTAMPTZ)""")
    op.execute("CREATE INDEX ix_owner_reply_actor_id ON owner_reply(actor_id)")
    op.execute("CREATE INDEX ix_owner_reply_status ON owner_reply(status)")


def downgrade() -> None:
    # Receipts prevent replay of already committed business actions.
    raise RuntimeError("Keep additive owner reliability data when rolling back images")
