"""When a track was added to a playlist."""

import sqlalchemy as sa
from alembic import op

revision = "a4c7e9f1b562"
down_revision = "f3b6d8e0a451"
branch_labels = depends_on = None


def upgrade():
    op.create_table(
        "playlist_dates",
        sa.Column("nd_playlist_id", sa.String(64), primary_key=True),
        sa.Column("track_id", sa.Integer(), sa.ForeignKey("tracks.id", ondelete="CASCADE"), primary_key=True),
        sa.Column("added_at", sa.DateTime(), nullable=False),
        sa.Column("source", sa.String(16), nullable=False),
    )


def downgrade():
    op.drop_table("playlist_dates")
