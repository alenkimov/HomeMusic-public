"""2.0: a track is named by its catalog ref (`tracks.ref`), not by a Deezer id."""

import sqlalchemy as sa
from alembic import op

revision = "a6c9e1f3b784"
down_revision = "f5b8d0e2a673"
branch_labels = depends_on = None


def upgrade():
    with op.batch_alter_table("tracks") as b:
        b.add_column(sa.Column("ref", sa.String(64), nullable=True))
    c = op.get_bind()
    taken: set[str] = set()
    # the newest track of a ref keeps it: there is one track per ref from now on
    for track_id, dz in c.execute(sa.text(
            "SELECT id, deezer_track_id FROM tracks WHERE deezer_track_id IS NOT NULL ORDER BY id DESC")).all():
        ref = f"dz:{dz}"
        if ref not in taken:
            taken.add(ref)
            c.execute(sa.text("UPDATE tracks SET ref = :ref WHERE id = :id"), {"ref": ref, "id": track_id})
    # a track brought by a download of another catalog's track is named as that download named it
    for track_id, ref in c.execute(sa.text(
            "SELECT d.track_id, json_extract(d.query_json, '$.meta.track_ref') FROM download_items d "
            "JOIN tracks t ON t.id = d.track_id WHERE t.ref IS NULL ORDER BY d.id DESC")).all():
        if ref and ref not in taken and not str(ref).startswith(("nd:", "file:")):
            taken.add(ref)
            c.execute(sa.text("UPDATE tracks SET ref = :ref WHERE id = :id AND ref IS NULL"), {"ref": ref, "id": track_id})
    with op.batch_alter_table("tracks") as b:
        b.drop_index("ix_tracks_deezer_track_id")
        b.drop_column("deezer_track_id")
        b.create_unique_constraint("uq_tracks_ref", ["ref"])


def downgrade():
    raise NotImplementedError("2.0 does not go back")
