"""A playlist holds a track once: the places a track had twice are one — the earliest of them stays.

Revision ID: 30a000000015
Revises: 30a000000014
"""
from typing import Sequence, Union

from alembic import op

revision: str = '30a000000015'
down_revision: Union[str, None] = '30a000000014'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.execute("""
        DELETE FROM playlist_items a USING playlist_items b
        WHERE a.playlist_id = b.playlist_id AND a.track_id = b.track_id
          AND (a.position, a.id) > (b.position, b.id)""")
    op.create_unique_constraint("uq_playlist_items_track", "playlist_items", ["playlist_id", "track_id"])


def downgrade() -> None:
    op.drop_constraint("uq_playlist_items_track", "playlist_items")
