"""Lyrics of the library's tracks (kept in the database) and the server's own settings."""

import sqlalchemy as sa
from alembic import op

revision = "c9e3a5b7d120"
down_revision = "b7d2e4f6a918"
branch_labels = depends_on = None


def upgrade():
    op.create_table(
        "lyrics",
        sa.Column("track_id", sa.Integer(), sa.ForeignKey("tracks.id", ondelete="CASCADE"), primary_key=True),
        sa.Column("kind", sa.String(8), nullable=False),
        sa.Column("source", sa.String(16), nullable=True),
        sa.Column("lines_json", sa.JSON(), nullable=True),
        sa.Column("plain", sa.Text(), nullable=True),
        sa.Column("offset_ms", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("pinned", sa.Boolean(), nullable=False, server_default=sa.false()),
        sa.Column("fetched_at", sa.DateTime(), nullable=False),
    )
    op.create_index("ix_lyrics_kind", "lyrics", ["kind"])
    op.create_table(
        "app_settings",
        sa.Column("key", sa.String(64), primary_key=True),
        sa.Column("value_json", sa.JSON(), nullable=False),
    )
    with op.batch_alter_table("users") as t:
        t.add_column(sa.Column("tg_with_lyrics", sa.Boolean(), nullable=False, server_default=sa.false()))


def downgrade():
    with op.batch_alter_table("users") as t:
        t.drop_column("tg_with_lyrics")
    op.drop_table("app_settings")
    op.drop_index("ix_lyrics_kind", table_name="lyrics")
    op.drop_table("lyrics")
