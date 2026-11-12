"""The names of a track are read from the library: the aliases kept for tracks go

What those rows knew and the library does not say by itself — that an event which called a track by some other
name (a provider's file, a catalog ref the library has the song under another name of) is about this track — is
written onto the events themselves first.

Revision ID: 20a000000002
Revises: 20a000000001
"""

import json

from alembic import op
from sqlalchemy import text

revision = "20a000000002"
down_revision = "20a000000001"
branch_labels = None
depends_on = None

BIND = text("UPDATE play_events SET track_id = :track WHERE track_id IS NULL AND target_type = 'track' AND ref = :ref")


def upgrade() -> None:
    db = op.get_bind()
    songs = dict(db.execute(text("SELECT nd_song_id, track_id FROM track_files WHERE nd_song_id IS NOT NULL")).all())
    for nd_id, names in db.execute(text("SELECT nd_id, aliases FROM favorite_subjects WHERE target_type = 'track'")).all():
        names = json.loads(names) if isinstance(names, str) else names or []
        track = songs.get(nd_id) or next((songs[a[3:]] for a in names if a.startswith("nd:") and a[3:] in songs), None)
        if track is None:
            continue
        for a in names:
            if a.startswith("ref:"):
                db.execute(BIND, {"track": track, "ref": a[4:]})
            elif a.startswith("nd:"):
                db.execute(text("UPDATE play_events SET track_id = :track WHERE track_id IS NULL "
                                "AND target_type = 'track' AND nd_song_id = :nd"), {"track": track, "nd": a[3:]})
    # a download or an import that was asked for by one name and found the library's track of another
    for sql in ("SELECT json_extract(query_json, '$.meta.track_ref'), track_id FROM download_items "
                "WHERE track_id IS NOT NULL AND json_extract(query_json, '$.meta.track_ref') IS NOT NULL",
                "SELECT ref, track_id FROM import_items WHERE track_id IS NOT NULL AND ref IS NOT NULL"):
        for ref, track in db.execute(text(sql)).all():
            db.execute(BIND, {"track": track, "ref": ref})
    op.execute("DELETE FROM favorite_subjects WHERE target_type = 'track'")


def downgrade() -> None:
    raise NotImplementedError("2.0 does not go back")
