"""Store a user's manually queued tracks on the Hub."""

import sqlalchemy as sa
from alembic import op

revision = "20a000000033"
down_revision = "20a000000032"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "playback_queue_items",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column("user_id", sa.Integer(), sa.ForeignKey("users.id", ondelete="CASCADE"), nullable=False),
        sa.Column("track_id", sa.Integer(), sa.ForeignKey("tracks.id", ondelete="CASCADE")),
        sa.Column("track", sa.JSON(), nullable=False),
        sa.Column("source_key", sa.String(64)),
        sa.Column("position", sa.Integer()),
        sa.Column("claim", sa.String(64)),
        sa.Column("claim_until", sa.DateTime()),
        sa.Column("finished_at", sa.DateTime()),
        sa.UniqueConstraint("user_id", "source_key", name="uq_playback_queue_source"),
        sqlite_autoincrement=True,
    )
    op.create_index("ix_playback_queue_pending", "playback_queue_items", ["user_id", "finished_at", "position", "id"])
    op.create_index("ix_playback_queue_items_track_id", "playback_queue_items", ["track_id"])


def downgrade() -> None:
    op.drop_table("playback_queue_items")
