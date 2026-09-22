"""Add the durable lane scheduler and hot-table indexes.

Revision ID: 0032
Revises: 0031
"""

import sqlalchemy as sa

from alembic import op

revision = "0032"
down_revision = "0031"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column(
        "payment_event", sa.Column("provider_reference", sa.String(128), server_default="", nullable=False)
    )
    op.add_column("payment_event", sa.Column("attempts", sa.Integer(), server_default="0", nullable=False))
    op.add_column(
        "payment_event",
        sa.Column("available_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
    )
    op.add_column("payment_event", sa.Column("lease_owner", sa.String(64)))
    op.add_column("payment_event", sa.Column("lease_expires_at", sa.DateTime(timezone=True)))
    op.add_column("payment_event", sa.Column("error", sa.String(500), server_default="", nullable=False))
    op.create_index(
        "ix_payment_event_claim",
        "payment_event",
        ["status", "available_at", "lease_expires_at"],
        postgresql_where=sa.text("status IN ('received','retry','processing')"),
    )
    op.create_table(
        "delivery_job",
        sa.Column("id", sa.BigInteger(), primary_key=True),
        sa.Column("source_kind", sa.String(32), nullable=False),
        sa.Column("source_id", sa.BigInteger(), nullable=False),
        sa.Column("owner_id", sa.BigInteger(), sa.ForeignKey("owner.id", ondelete="SET NULL")),
        sa.Column("bot_id", sa.BigInteger(), sa.ForeignKey("managed_bot.id", ondelete="SET NULL")),
        sa.Column("chat_id", sa.BigInteger()),
        sa.Column("lane", sa.String(16), server_default="standard", nullable=False),
        sa.Column("priority", sa.Integer(), server_default="100", nullable=False),
        sa.Column("status", sa.String(16), server_default="pending", nullable=False),
        sa.Column("due_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("next_attempt_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("deadline_at", sa.DateTime(timezone=True)),
        sa.Column("attempts", sa.Integer(), server_default="0", nullable=False),
        sa.Column("lease_owner", sa.String(96)),
        sa.Column("lease_expires_at", sa.DateTime(timezone=True)),
        sa.Column("signaled_at", sa.DateTime(timezone=True)),
        sa.Column("stream_id", sa.String(64)),
        sa.Column("dedupe_key", sa.String(128), nullable=False),
        sa.Column("error_code", sa.String(96), server_default="", nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
        sa.UniqueConstraint("source_kind", "source_id", name="uq_delivery_job_source"),
        sa.UniqueConstraint("dedupe_key", name="uq_delivery_job_dedupe_key"),
        sa.CheckConstraint(
            "lane IN ('urgent','interactive','standard','bulk')", name="ck_delivery_job_lane"
        ),
        sa.CheckConstraint(
            "status IN ('pending','signaled','processing','retry','completed','failed','cancelled')",
            name="ck_delivery_job_status",
        ),
    )
    op.create_index("ix_delivery_job_owner_id", "delivery_job", ["owner_id"])
    op.create_index("ix_delivery_job_bot_id", "delivery_job", ["bot_id"])
    op.create_index("ix_delivery_job_status", "delivery_job", ["status"])
    op.create_index("ix_delivery_job_due_at", "delivery_job", ["due_at"])
    op.create_index("ix_delivery_job_next_attempt_at", "delivery_job", ["next_attempt_at"])
    op.create_index("ix_delivery_job_deadline_at", "delivery_job", ["deadline_at"])
    op.create_index(
        "ix_delivery_job_ready",
        "delivery_job",
        ["lane", "status", "next_attempt_at", "deadline_at"],
        postgresql_where=sa.text("status IN ('pending','retry')"),
    )
    op.create_index(
        "ix_delivery_job_relay",
        "delivery_job",
        ["status", "signaled_at", "next_attempt_at"],
        postgresql_where=sa.text("status IN ('pending','retry','signaled')"),
    )
    # Existing worker queries immediately benefit before Streams are enabled.
    op.create_index(
        "ix_delivery_operation_active_due",
        "delivery_operation",
        ["due_at", "lease_expires_at", "flow_delivery_id"],
        postgresql_where=sa.text("status IN ('scheduled','retry','processing')"),
    )
    op.execute(
        "ALTER TABLE delivery_operation SET (autovacuum_vacuum_scale_factor=0.02, "
        "autovacuum_analyze_scale_factor=0.01, autovacuum_vacuum_threshold=500)"
    )
    op.execute(
        """
        CREATE TABLE delivery_job_history (
          id BIGINT NOT NULL,
          source_kind VARCHAR(32) NOT NULL,
          source_id BIGINT NOT NULL,
          bot_id BIGINT,
          lane VARCHAR(16) NOT NULL,
          status VARCHAR(16) NOT NULL,
          due_at TIMESTAMPTZ NOT NULL,
          deadline_at TIMESTAMPTZ,
          attempts INTEGER NOT NULL,
          error_code VARCHAR(96) NOT NULL,
          created_at TIMESTAMPTZ NOT NULL,
          finished_at TIMESTAMPTZ NOT NULL,
          PRIMARY KEY (id, finished_at)
        ) PARTITION BY RANGE (finished_at)
        """
    )
    op.execute(
        """
        DO $$
        DECLARE previous_month date := (date_trunc('month', now()) - interval '1 month')::date;
        DECLARE start_month date := date_trunc('month', now())::date;
        DECLARE next_month date := (start_month + interval '1 month')::date;
        DECLARE after_next date := (start_month + interval '2 months')::date;
        BEGIN
          EXECUTE format(
            'CREATE TABLE %I PARTITION OF delivery_job_history FOR VALUES FROM (%L) TO (%L)',
            'delivery_job_history_' || to_char(previous_month, 'YYYYMM'), previous_month, start_month
          );
          EXECUTE format(
            'CREATE TABLE %I PARTITION OF delivery_job_history FOR VALUES FROM (%L) TO (%L)',
            'delivery_job_history_' || to_char(start_month, 'YYYYMM'), start_month, next_month
          );
          EXECUTE format(
            'CREATE TABLE %I PARTITION OF delivery_job_history FOR VALUES FROM (%L) TO (%L)',
            'delivery_job_history_' || to_char(next_month, 'YYYYMM'), next_month, after_next
          );
        END $$
        """
    )
    op.execute(
        """
        CREATE FUNCTION gramly_sync_delivery_job() RETURNS trigger AS $$
        DECLARE
          job_lane varchar(16);
          job_status varchar(16);
          job_owner bigint;
          job_bot bigint;
          job_chat bigint;
          job_deadline timestamptz;
          job_due timestamptz;
          job_priority integer;
        BEGIN
          IF TG_OP = 'DELETE' THEN
            UPDATE delivery_job SET status='cancelled', updated_at=now()
            WHERE source_kind=TG_TABLE_NAME AND source_id=OLD.id;
            RETURN OLD;
          END IF;
          job_lane := CASE
            WHEN TG_TABLE_NAME IN ('broadcast_recipient','rotation_recommendation') THEN 'bulk'
            WHEN TG_TABLE_NAME = 'client_message' THEN 'interactive'
            ELSE 'urgent'
          END;
          job_priority := CASE job_lane WHEN 'urgent' THEN 10 WHEN 'interactive' THEN 50 ELSE 200 END;
          job_status := CASE
            WHEN NEW.status IN ('scheduled','pending','retry') THEN
              CASE WHEN NEW.status='retry' THEN 'retry' ELSE 'pending' END
            WHEN NEW.status='processing' THEN 'processing'
            WHEN NEW.status IN ('sent','completed','granted','approved','joined') THEN 'completed'
            WHEN NEW.status IN ('cancelled','blocked','unsubscribed','approved_without_activation') THEN 'cancelled'
            ELSE 'failed'
          END;
          job_due := COALESCE(NEW.due_at, now());
          IF TG_TABLE_NAME = 'delivery_operation' THEN
            SELECT b.owner_id, d.bot_id, COALESCE(d.target_chat_id, c.telegram_id), d.target_expires_at
              INTO job_owner, job_bot, job_chat, job_deadline
            FROM flow_delivery d JOIN managed_bot b ON b.id=d.bot_id
                 JOIN contact c ON c.id=d.contact_id WHERE d.id=NEW.flow_delivery_id;
          ELSIF TG_TABLE_NAME = 'greeting_delivery' THEN
            SELECT b.owner_id, NEW.bot_id, c.telegram_id, NULL
              INTO job_owner, job_bot, job_chat, job_deadline
            FROM managed_bot b JOIN contact c ON c.id=NEW.contact_id WHERE b.id=NEW.bot_id;
          ELSIF TG_TABLE_NAME = 'join_request' THEN
            SELECT b.owner_id, NEW.bot_id, COALESCE(NEW.user_chat_id,c.telegram_id), NEW.message_window_expires_at
              INTO job_owner, job_bot, job_chat, job_deadline
            FROM managed_bot b JOIN contact c ON c.id=NEW.contact_id WHERE b.id=NEW.bot_id;
          ELSIF TG_TABLE_NAME = 'join_request_activation' THEN
            SELECT b.owner_id, NEW.bot_id, c.telegram_id, NEW.expires_at
              INTO job_owner, job_bot, job_chat, job_deadline
            FROM managed_bot b JOIN contact c ON c.id=NEW.contact_id WHERE b.id=NEW.bot_id;
          ELSIF TG_TABLE_NAME = 'client_message' THEN
            SELECT b.owner_id, NEW.bot_id, c.telegram_id, NULL
              INTO job_owner, job_bot, job_chat, job_deadline
            FROM managed_bot b JOIN contact c ON c.id=NEW.contact_id WHERE b.id=NEW.bot_id;
          ELSIF TG_TABLE_NAME = 'broadcast_recipient' THEN
            SELECT b.owner_id, b.bot_id, c.telegram_id, NULL
              INTO job_owner, job_bot, job_chat, job_deadline
            FROM broadcast b JOIN contact c ON c.id=NEW.contact_id WHERE b.id=NEW.broadcast_id;
          ELSIF TG_TABLE_NAME = 'rotation_recommendation' THEN
            SELECT d.owner_id, d.bot_id, c.telegram_id, NULL
              INTO job_owner, job_bot, job_chat, job_deadline
            FROM departure_event d JOIN contact c ON c.id=d.contact_id WHERE d.id=NEW.departure_id;
          END IF;
          INSERT INTO delivery_job
            (source_kind,source_id,owner_id,bot_id,chat_id,lane,priority,status,due_at,
             next_attempt_at,deadline_at,attempts,dedupe_key,error_code,updated_at)
          VALUES
            (TG_TABLE_NAME,NEW.id,job_owner,job_bot,job_chat,job_lane,job_priority,job_status,
             job_due,job_due,job_deadline,NEW.attempts,TG_TABLE_NAME || ':' || NEW.id,
             left(COALESCE(NEW.error,''),96),now())
          ON CONFLICT (source_kind,source_id) DO UPDATE SET
            owner_id=EXCLUDED.owner_id, bot_id=EXCLUDED.bot_id, chat_id=EXCLUDED.chat_id,
            lane=EXCLUDED.lane, priority=EXCLUDED.priority, status=EXCLUDED.status,
            due_at=EXCLUDED.due_at, next_attempt_at=EXCLUDED.next_attempt_at,
            deadline_at=EXCLUDED.deadline_at, attempts=EXCLUDED.attempts,
            error_code=EXCLUDED.error_code, updated_at=now();
          RETURN NEW;
        END;
        $$ LANGUAGE plpgsql
        """
    )
    for table in (
        "delivery_operation",
        "greeting_delivery",
        "join_request",
        "join_request_activation",
        "client_message",
        "broadcast_recipient",
        "rotation_recommendation",
    ):
        op.execute(
            f"CREATE TRIGGER trg_{table}_delivery_job AFTER INSERT OR UPDATE OF status, due_at "
            f"OR DELETE ON {table} FOR EACH ROW EXECUTE FUNCTION gramly_sync_delivery_job()"
        )
    active_statuses = {
        "delivery_operation": "'scheduled','retry','processing'",
        "greeting_delivery": "'scheduled','retry','processing'",
        "join_request": "'scheduled','processing'",
        "join_request_activation": "'pending','processing'",
        "client_message": "'scheduled','retry','processing'",
        "broadcast_recipient": "'scheduled','retry','processing'",
        "rotation_recommendation": "'scheduled','retry','processing'",
    }
    for table, statuses in active_statuses.items():
        # Only active rows are touched; terminal history remains lock-free.
        op.execute(f"UPDATE {table} SET due_at=due_at WHERE status IN ({statuses})")


def downgrade() -> None:
    for table in (
        "delivery_operation",
        "greeting_delivery",
        "join_request",
        "join_request_activation",
        "client_message",
        "broadcast_recipient",
        "rotation_recommendation",
    ):
        op.execute(f"DROP TRIGGER IF EXISTS trg_{table}_delivery_job ON {table}")
    op.execute("DROP FUNCTION gramly_sync_delivery_job()")
    op.drop_index("ix_payment_event_claim", table_name="payment_event")
    for column in (
        "error", "lease_expires_at", "lease_owner", "available_at", "attempts", "provider_reference"
    ):
        op.drop_column("payment_event", column)
    op.execute("ALTER TABLE delivery_operation RESET (autovacuum_vacuum_scale_factor, "
               "autovacuum_analyze_scale_factor, autovacuum_vacuum_threshold)")
    op.drop_index("ix_delivery_operation_active_due", table_name="delivery_operation")
    op.execute("DROP TABLE delivery_job_history")
    op.drop_table("delivery_job")
