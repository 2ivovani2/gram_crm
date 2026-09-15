"""Optional per-version button style; legacy keyboards remain unchanged."""
import sqlalchemy as sa

from alembic import op

revision = "0025"
down_revision = "0024"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column("content_flow_version", sa.Column("button_style", sa.String(16), nullable=True))
    op.create_check_constraint("ck_flow_button_style", "content_flow_version",
                               "button_style IS NULL OR button_style IN ('default','primary','success','danger')")


def downgrade() -> None:
    op.drop_constraint("ck_flow_button_style", "content_flow_version", type_="check")
    op.drop_column("content_flow_version", "button_style")
