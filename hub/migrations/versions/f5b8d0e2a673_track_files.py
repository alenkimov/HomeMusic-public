"""2.0: a track is not a file — the file fields of `tracks` move to `track_files`."""

import sqlalchemy as sa
from alembic import op

revision = "f5b8d0e2a673"
down_revision = "e4a7c9d1f562"
branch_labels = depends_on = None

FILE_COLUMNS = ("uid", "path", "nd_song_id", "bitrate_kbps", "codec", "source", "source_url", "source_upload_date",
                "acquired_at", "locked", "warnings", "added_by")


def upgrade():
    op.create_table(
        "track_files",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column("track_id", sa.Integer(), sa.ForeignKey("tracks.id", ondelete="CASCADE"), nullable=False),
        sa.Column("uid", sa.String(36), nullable=False, unique=True),
        sa.Column("path", sa.String(1024), nullable=False, unique=True),
        sa.Column("nd_song_id", sa.String(64)),
        sa.Column("bitrate_kbps", sa.Integer()),
        sa.Column("codec", sa.String(16)),
        sa.Column("source", sa.String(32)),
        sa.Column("source_url", sa.String(2048)),
        sa.Column("source_upload_date", sa.Date()),
        sa.Column("acquired_at", sa.DateTime(), nullable=False),
        sa.Column("locked", sa.Boolean(), nullable=False, server_default="0"),
        sa.Column("warnings", sa.JSON(), nullable=False, server_default="[]"),
        sa.Column("added_by", sa.Integer(), sa.ForeignKey("users.id", ondelete="SET NULL")),
    )
    op.create_index("ix_track_files_track_id", "track_files", ["track_id"])
    op.create_index("ix_track_files_nd_song_id", "track_files", ["nd_song_id"])
    cols = ", ".join(FILE_COLUMNS)
    op.execute(f"INSERT INTO track_files (track_id, {cols}) SELECT id, {cols} FROM tracks")
    with op.batch_alter_table("tracks") as b:
        b.add_column(sa.Column("created_at", sa.DateTime(), nullable=True))
        b.add_column(sa.Column("created_by", sa.Integer(), nullable=True))
    op.execute("UPDATE tracks SET created_at = acquired_at, created_by = added_by")
    with op.batch_alter_table("tracks") as b:
        b.alter_column("created_at", existing_type=sa.DateTime(), nullable=False)
        b.create_foreign_key("fk_tracks_created_by", "users", ["created_by"], ["id"], ondelete="SET NULL")
        b.drop_index("ix_tracks_nd_song_id")
        for c in FILE_COLUMNS:
            b.drop_column(c)


def downgrade():
    raise NotImplementedError("2.0 does not go back")
