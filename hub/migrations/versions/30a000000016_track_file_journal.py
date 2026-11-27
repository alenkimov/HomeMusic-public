"""A track's main file, and the journal of its files (`app.library.files`).

The main file is the one that played: the newest of the track's. Each file there is is written in the journal as
come; a file a replacement put in the trash folder (`upgrades.was_path`) is written as gone there, and the
replacement points at that — it can be brought back from the journal as long as the trash folder keeps it.

Revision ID: 30a000000016
Revises: 30a000000015
"""
from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects.postgresql import JSONB

revision: str = '30a000000016'
down_revision: Union[str, None] = '30a000000015'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.add_column("track_files", sa.Column("main", sa.Boolean, nullable=False, server_default="false"))
    op.execute("""
        UPDATE track_files f SET main = true
        FROM (SELECT DISTINCT ON (track_id) id FROM track_files ORDER BY track_id, id DESC) newest
        WHERE f.id = newest.id""")
    op.create_index("uq_track_files_main", "track_files", ["track_id"], unique=True, postgresql_where=sa.text("main"))

    op.create_table(
        "track_file_events",
        sa.Column("id", sa.Integer, primary_key=True),
        sa.Column("track_id", sa.Integer, sa.ForeignKey("tracks.id", ondelete="CASCADE"), nullable=False, index=True),
        sa.Column("at", sa.DateTime, nullable=False, server_default=sa.func.now()),
        sa.Column("kind", sa.String(12), nullable=False),
        sa.Column("file_id", sa.Integer, sa.ForeignKey("track_files.id", ondelete="SET NULL"), index=True),
        sa.Column("file", JSONB, nullable=False, server_default="{}"),
        sa.Column("why", sa.String(16), nullable=False, server_default=""),
        sa.Column("trash_path", sa.String(2048)),
        sa.Column("star_from", sa.String(64)),
        sa.Column("download_item_id", sa.Integer, sa.ForeignKey("download_items.id", ondelete="SET NULL")),
        sa.Column("user_id", sa.Integer, sa.ForeignKey("users.id", ondelete="SET NULL")),
    )
    # the files there are: come when they were acquired, by what brought them
    op.execute("""
        INSERT INTO track_file_events (track_id, at, kind, file_id, file, why, user_id)
        SELECT f.track_id, f.acquired_at, 'added', f.id,
               jsonb_build_object('uid', f.uid, 'path', f.path, 'codec', f.codec, 'bitrate_kbps', f.bitrate_kbps,
                                  'source', f.source, 'source_url', f.source_url, 'main', f.main),
               CASE WHEN f.source = 'upload' THEN 'upload' ELSE 'download' END, f.added_by
        FROM track_files f""")
    # the main file came by the last download of its track that kept a journal of its steps (as «Как скачан» read it)
    op.execute("""
        UPDATE track_file_events e SET download_item_id = d.id
        FROM track_files f, LATERAL (
            SELECT id FROM download_items d WHERE d.track_id = f.track_id AND jsonb_array_length(d.log_json) > 0
            ORDER BY d.id DESC LIMIT 1) d
        WHERE e.file_id = f.id AND f.main""")

    # the files replacements put in the trash folder: gone there, and the replacement points at that
    op.add_column("upgrades", sa.Column("was_event_id", sa.Integer,
                                        sa.ForeignKey("track_file_events.id", ondelete="SET NULL")))
    op.execute("""
        WITH gone AS (
            INSERT INTO track_file_events (track_id, at, kind, file, why, trash_path, user_id)
            SELECT u.track_id, u.updated_at, 'trashed',
                   jsonb_build_object('codec', u.was_codec, 'bitrate_kbps', u.was_kbps, 'upgrade', u.id),
                   CASE WHEN u.kind = 'picked' THEN 'picked' ELSE 'upgrade' END, u.was_path, u.user_id
            FROM upgrades u WHERE u.was_path IS NOT NULL
            RETURNING id, (file->>'upgrade')::int AS upgrade_id)
        UPDATE upgrades u SET was_event_id = gone.id FROM gone WHERE u.id = gone.upgrade_id""")
    op.execute("UPDATE track_file_events SET file = file - 'upgrade' WHERE kind = 'trashed'")
    op.drop_column("upgrades", "was_path")


def downgrade() -> None:
    op.add_column("upgrades", sa.Column("was_path", sa.String(1024)))
    op.execute("""
        UPDATE upgrades u SET was_path = e.trash_path FROM track_file_events e WHERE e.id = u.was_event_id""")
    op.drop_column("upgrades", "was_event_id")
    op.drop_table("track_file_events")
    op.drop_index("uq_track_files_main", "track_files")
    op.drop_column("track_files", "main")
