"""2.0: an event names the Hub's track it is about

Revision ID: d9f2b4c6e017
Revises: c8e1a3b5d906
"""

import sqlalchemy as sa
from alembic import op

revision = "d9f2b4c6e017"
down_revision = "c8e1a3b5d906"
branch_labels = None
depends_on = None


def upgrade() -> None:
    with op.batch_alter_table("play_events") as batch:
        batch.add_column(sa.Column("track_id", sa.Integer(), nullable=True))
        batch.create_foreign_key("fk_play_events_track", "tracks", ["track_id"], ["id"], ondelete="SET NULL")
        batch.create_index("ix_play_events_track", ["track_id"])
    op.execute("""
        UPDATE play_events SET track_id = COALESCE(
            (SELECT f.track_id FROM track_files f WHERE f.nd_song_id = play_events.nd_song_id ORDER BY f.id DESC LIMIT 1),
            (SELECT t.id FROM tracks t WHERE t.ref = play_events.ref))
        WHERE target_type = 'track'
    """)


def downgrade() -> None:
    raise NotImplementedError("2.0 does not go back")
