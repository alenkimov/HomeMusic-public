"""A deleted track can come back; a list of «Для вас» knows it was opened

`trashed_tracks`: what a deleted track was, while its file is kept in the trash (app/library/trash.py).
`for_you_seen`: the issue of a list the user opened last — a newer one is marked «новый».

Revision ID: 20a000000013
Revises: 20a000000012
"""

import sqlalchemy as sa
from alembic import op

revision = "20a000000013"
down_revision = "20a000000012"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "trashed_tracks",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column("track_id", sa.Integer(), nullable=False),
        sa.Column("title", sa.String(length=512), nullable=False),
        sa.Column("artist", sa.String(length=512), nullable=False),
        sa.Column("album", sa.String(length=512), nullable=True),
        sa.Column("data_json", sa.JSON(), nullable=False),
        sa.Column("cover", sa.LargeBinary(), nullable=True),
        sa.Column("deleted_by", sa.Integer(), sa.ForeignKey("users.id", ondelete="SET NULL"), nullable=True),
        sa.Column("deleted_at", sa.DateTime(), nullable=False),
    )
    op.create_table(
        "for_you_seen",
        sa.Column("user_id", sa.Integer(), sa.ForeignKey("users.id", ondelete="CASCADE"), primary_key=True),
        sa.Column("kind", sa.String(length=16), primary_key=True),
        sa.Column("issue", sa.String(length=32), nullable=False),
    )


def downgrade() -> None:
    raise NotImplementedError("2.0 does not go back")
