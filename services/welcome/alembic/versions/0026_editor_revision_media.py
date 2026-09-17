"""Draft edit revisions cover all writers, including existing bot handlers."""

import sqlalchemy as sa
from sqlalchemy.dialects import postgresql

from alembic import op

revision = "0026"
down_revision = "0025"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column(
        "content_flow_version",
        sa.Column("edit_revision", sa.BigInteger(), server_default="1", nullable=False),
    )
    op.create_table(
        "editor_media",
        sa.Column("id", postgresql.UUID(as_uuid=True), primary_key=True),
        sa.Column("owner_id", sa.BigInteger(), sa.ForeignKey("owner.id", ondelete="CASCADE"), nullable=False),
        sa.Column("descriptor", postgresql.JSONB(), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
    )
    op.create_index("ix_editor_media_owner_id", "editor_media", ["owner_id"])
    op.execute("""
    CREATE FUNCTION welcome_edit_revision() RETURNS trigger LANGUAGE plpgsql AS $$
    DECLARE row_data jsonb; version_id bigint; step_id bigint; keyboard_id bigint;
    BEGIN
      IF TG_TABLE_NAME = 'content_flow_version' THEN
        IF (to_jsonb(NEW) - 'edit_revision') IS DISTINCT FROM (to_jsonb(OLD) - 'edit_revision') THEN
          NEW.edit_revision := OLD.edit_revision + 1;
        END IF;
        RETURN NEW;
      END IF;
      IF TG_OP = 'DELETE' THEN row_data := to_jsonb(OLD); ELSE row_data := to_jsonb(NEW); END IF;
      IF TG_TABLE_NAME = 'content_flow' THEN
        IF NEW IS DISTINCT FROM OLD THEN
          UPDATE content_flow_version SET edit_revision = edit_revision + 1 WHERE flow_id = NEW.id AND status = 'draft';
        END IF;
        RETURN NEW;
      ELSIF TG_TABLE_NAME = 'flow_channel_assignment' THEN
        UPDATE content_flow_version SET edit_revision = edit_revision + 1 WHERE flow_id = (row_data->>'flow_id')::bigint AND status = 'draft';
        IF TG_OP = 'DELETE' THEN RETURN OLD; END IF;
        RETURN NEW;
      END IF;
      IF TG_TABLE_NAME = 'content_step' THEN version_id := (row_data->>'version_id')::bigint;
      ELSE
        IF TG_TABLE_NAME = 'content_keyboard_button' THEN
          keyboard_id := (row_data->>'keyboard_id')::bigint;
          SELECT k.step_id INTO step_id FROM content_keyboard k WHERE k.id = keyboard_id;
        ELSE step_id := (row_data->>'step_id')::bigint;
        END IF;
        SELECT s.version_id INTO version_id FROM content_step s WHERE s.id = step_id;
      END IF;
      UPDATE content_flow_version SET edit_revision = edit_revision + 1 WHERE id = version_id;
      IF TG_OP = 'DELETE' THEN RETURN OLD; END IF;
      RETURN NEW;
    END $$
    """)
    for table in (
        "content_step",
        "content_attachment",
        "content_keyboard",
        "content_keyboard_button",
        "flow_channel_assignment",
    ):
        op.execute(
            f"CREATE TRIGGER editor_revision BEFORE INSERT OR UPDATE OR DELETE ON {table} "
            "FOR EACH ROW EXECUTE FUNCTION welcome_edit_revision()"
        )
    op.execute(
        "CREATE TRIGGER editor_revision BEFORE UPDATE ON content_flow FOR EACH ROW EXECUTE FUNCTION welcome_edit_revision()"
    )
    op.execute(
        "CREATE TRIGGER editor_revision BEFORE UPDATE ON content_flow_version "
        "FOR EACH ROW EXECUTE FUNCTION welcome_edit_revision()"
    )


def downgrade() -> None:
    for table in (
        "content_step",
        "content_attachment",
        "content_keyboard",
        "content_keyboard_button",
        "content_flow_version",
        "content_flow",
        "flow_channel_assignment",
    ):
        op.execute(f"DROP TRIGGER IF EXISTS editor_revision ON {table}")
    op.execute("DROP FUNCTION welcome_edit_revision()")
    op.drop_table("editor_media")
    op.drop_column("content_flow_version", "edit_revision")
