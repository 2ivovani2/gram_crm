"""Group learning videos and persist owner progress.

Revision ID: 0031
Revises: 0030
"""

import sqlalchemy as sa

from alembic import op

revision = "0031"
down_revision = "0030"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column(
        "learning_video",
        sa.Column("chapter_key", sa.String(32), server_default="first_launch", nullable=False),
    )
    op.create_check_constraint(
        "ck_learning_video_chapter",
        "learning_video",
        "chapter_key IN ('first_launch','automation','account')",
    )
    op.create_index("ix_learning_video_chapter_key", "learning_video", ["chapter_key"])
    op.execute(
        """
        UPDATE learning_video
        SET chapter_key = CASE
            WHEN sort_order <= 60 THEN 'first_launch'
            WHEN sort_order <= 120 THEN 'automation'
            ELSE 'account'
        END
        """
    )
    op.create_table(
        "learning_video_progress",
        sa.Column("id", sa.BigInteger(), primary_key=True),
        sa.Column(
            "owner_id",
            sa.BigInteger(),
            sa.ForeignKey("owner.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column(
            "video_id",
            sa.BigInteger(),
            sa.ForeignKey("learning_video.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column("position_seconds", sa.Integer(), server_default="0", nullable=False),
        sa.Column("completed_at", sa.DateTime(timezone=True)),
        sa.Column(
            "updated_at",
            sa.DateTime(timezone=True),
            server_default=sa.func.now(),
            nullable=False,
        ),
        sa.UniqueConstraint(
            "owner_id", "video_id", name="uq_learning_video_progress_owner_video"
        ),
        sa.CheckConstraint(
            "position_seconds >= 0", name="ck_learning_video_progress_position"
        ),
    )
    op.create_index(
        "ix_learning_video_progress_owner_id", "learning_video_progress", ["owner_id"]
    )
    op.create_index(
        "ix_learning_video_progress_video_id", "learning_video_progress", ["video_id"]
    )
    op.create_index(
        "ix_learning_video_progress_completed_at",
        "learning_video_progress",
        ["completed_at"],
    )


def downgrade() -> None:
    op.drop_table("learning_video_progress")
    op.drop_index("ix_learning_video_chapter_key", table_name="learning_video")
    op.drop_constraint("ck_learning_video_chapter", "learning_video", type_="check")
    op.drop_column("learning_video", "chapter_key")
