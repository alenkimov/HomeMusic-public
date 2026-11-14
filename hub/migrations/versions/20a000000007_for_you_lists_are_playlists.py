"""The lists of «Для вас» are playlists of Navidrome

«Открытия недели» is kept as a mix of a week (`mixes.kind`), and every list the Hub makes by itself has a playlist
of its own in Navidrome (`for_you_lists`).

Revision ID: 20a000000007
Revises: 20a000000006
"""

import sqlalchemy as sa
from alembic import op

revision = "20a000000007"
down_revision = "20a000000006"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column("mixes", sa.Column("kind", sa.String(length=16), nullable=False, server_default="daily"))
    op.create_table(
        "for_you_lists",
        sa.Column("user_id", sa.Integer(), sa.ForeignKey("users.id", ondelete="CASCADE"), primary_key=True),
        sa.Column("kind", sa.String(length=16), primary_key=True),
        sa.Column("nd_playlist_id", sa.String(length=64), nullable=True),
        sa.Column("songs_json", sa.JSON(), nullable=False, server_default="[]"),
        sa.Column("updated_at", sa.DateTime(), nullable=False, server_default=sa.func.now()),
    )


def downgrade() -> None:
    raise NotImplementedError("2.0 does not go back")
