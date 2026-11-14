"""Names and covers put right by listeners

A track renamed by hand says so, may have a cover of its own, and every change is written down (app/library/edits.py).

Revision ID: 20a000000012
Revises: 20a000000011
"""

import sqlalchemy as sa
from alembic import op

revision = "20a000000012"
down_revision = "20a000000011"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column("tracks", sa.Column("renamed_at", sa.DateTime(), nullable=True))
    op.add_column("tracks", sa.Column("cover_key", sa.String(length=16), nullable=True))
    op.create_table(
        "track_covers",
        sa.Column("track_id", sa.Integer(), sa.ForeignKey("tracks.id", ondelete="CASCADE"), primary_key=True),
        sa.Column("image", sa.LargeBinary(), nullable=False),
    )
    op.create_table(
        "edits",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column("target_type", sa.String(length=8), nullable=False),
        sa.Column("target_id", sa.Integer(), nullable=False),
        sa.Column("user_id", sa.Integer(), sa.ForeignKey("users.id", ondelete="SET NULL"), nullable=True),
        sa.Column("changes_json", sa.JSON(), nullable=False),
        sa.Column("created_at", sa.DateTime(), nullable=False),
    )
    op.create_index("ix_edits_target", "edits", ["target_type", "target_id"])


def downgrade() -> None:
    raise NotImplementedError("2.0 does not go back")
