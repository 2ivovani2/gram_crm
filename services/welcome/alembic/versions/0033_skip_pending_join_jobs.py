"""Keep join requests awaiting an external event out of the delivery queue.

Revision ID: 0033
Revises: 0032
"""

from alembic import op

revision = "0033"
down_revision = "0032"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.execute("DROP TRIGGER trg_join_request_delivery_job ON join_request")
    op.execute(
        """
        CREATE FUNCTION gramly_drop_pending_join_job() RETURNS trigger AS $$
        BEGIN
          DELETE FROM delivery_job
          WHERE source_kind = 'join_request' AND source_id = NEW.id;
          RETURN NEW;
        END;
        $$ LANGUAGE plpgsql
        """
    )
    op.execute(
        """
        CREATE TRIGGER trg_join_request_delivery_job
        AFTER INSERT OR UPDATE OF status, due_at ON join_request
        FOR EACH ROW WHEN (NEW.status <> 'pending')
        EXECUTE FUNCTION gramly_sync_delivery_job()
        """
    )
    op.execute(
        """
        CREATE TRIGGER trg_join_request_pending_job
        AFTER INSERT OR UPDATE OF status, due_at ON join_request
        FOR EACH ROW WHEN (NEW.status = 'pending')
        EXECUTE FUNCTION gramly_drop_pending_join_job()
        """
    )
    op.execute(
        """
        CREATE TRIGGER trg_join_request_deleted_job
        AFTER DELETE ON join_request FOR EACH ROW
        EXECUTE FUNCTION gramly_sync_delivery_job()
        """
    )
    op.execute(
        """
        DELETE FROM delivery_job AS job USING join_request AS request
        WHERE job.source_kind = 'join_request'
          AND job.source_id = request.id
          AND request.status = 'pending'
        """
    )


def downgrade() -> None:
    op.execute("DROP TRIGGER trg_join_request_delivery_job ON join_request")
    op.execute("DROP TRIGGER trg_join_request_pending_job ON join_request")
    op.execute("DROP TRIGGER trg_join_request_deleted_job ON join_request")
    op.execute("DROP FUNCTION gramly_drop_pending_join_job()")
    op.execute(
        """
        CREATE TRIGGER trg_join_request_delivery_job
        AFTER INSERT OR UPDATE OF status, due_at OR DELETE ON join_request
        FOR EACH ROW EXECUTE FUNCTION gramly_sync_delivery_job()
        """
    )
    op.execute("UPDATE join_request SET due_at = due_at WHERE status = 'pending'")
