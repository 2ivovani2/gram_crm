"""Versioned public message catalog; existing flows remain unchanged."""
import sqlalchemy as sa
from sqlalchemy.dialects import postgresql

from alembic import op

revision = "0024"
down_revision = "0023"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "message_template",
        sa.Column("id", sa.BigInteger(), primary_key=True),
        sa.Column("slug", sa.String(80), nullable=False),
        sa.Column("revision", sa.Integer(), nullable=False),
        sa.Column("kind", sa.String(16), nullable=False),
        sa.Column("title", sa.String(128), nullable=False),
        sa.Column("author", sa.String(80), nullable=False),
        sa.Column("description", sa.Text(), nullable=False),
        sa.Column("tags", postgresql.JSONB(), nullable=False),
        sa.Column("screenshots", postgresql.JSONB(), nullable=False),
        sa.Column("conversion", sa.String(80)),
        sa.Column("requires_business", sa.Boolean(), nullable=False),
        sa.Column("spec", postgresql.JSONB(), nullable=False),
        sa.Column("is_active", sa.Boolean(), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
        sa.UniqueConstraint("slug", "revision", name="uq_message_template_revision"),
        sa.CheckConstraint("kind IN ('welcome','farewell')", name="ck_message_template_kind"),
    )


def downgrade() -> None:
    op.drop_table("message_template")
