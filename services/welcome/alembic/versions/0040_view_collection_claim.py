"""Coalesce simultaneous public GramlyView scans.

Revision ID: 0040
Revises: 0039
"""

from alembic import op

revision = "0040"
down_revision = "0039"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.execute("""
        CREATE TABLE view_collection_claim (
            channel_id bigint PRIMARY KEY,
            claim_key varchar(32) NOT NULL,
            claimed_until timestamptz NOT NULL
        )
    """)


def downgrade() -> None:
    op.drop_table("view_collection_claim")
