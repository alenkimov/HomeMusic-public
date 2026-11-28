"""A song of Navidrome is one file: `track_files.nd_song_id` is unique.

A file not yet scanned was given a song of the same title, album and length — another artist's «Run Rabbit» — and
was drawn, played and liked as that one. Which of the files sharing a song is really its is told by Navidrome's
path alone: all of them are unlinked here, and the next scan links each by its path (`link_unlinked`).

Revision ID: 30a000000017
Revises: 30a000000016
"""
from typing import Sequence, Union

from alembic import op

revision: str = '30a000000017'
down_revision: Union[str, None] = '30a000000016'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.execute("""
        UPDATE track_files SET nd_song_id = NULL
        WHERE nd_song_id IN (SELECT nd_song_id FROM track_files WHERE nd_song_id IS NOT NULL
                             GROUP BY nd_song_id HAVING count(*) > 1)""")
    op.drop_index("ix_track_files_nd_song_id", table_name="track_files")
    op.create_unique_constraint("uq_track_files_nd_song_id", "track_files", ["nd_song_id"])


def downgrade() -> None:
    op.drop_constraint("uq_track_files_nd_song_id", "track_files", type_="unique")
    op.create_index("ix_track_files_nd_song_id", "track_files", ["nd_song_id"])
