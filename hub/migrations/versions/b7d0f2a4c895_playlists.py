"""2.0: playlists of the Hub's own; what the catalog said of a track."""

import sqlalchemy as sa
from alembic import op

revision = "b7d0f2a4c895"
down_revision = "a6c9e1f3b784"
branch_labels = depends_on = None


def upgrade():
    with op.batch_alter_table("tracks") as b:
        b.add_column(sa.Column("catalog", sa.JSON(), nullable=True))
    op.create_table(
        "playlists",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column("user_id", sa.Integer(), sa.ForeignKey("users.id", ondelete="CASCADE"), nullable=False),
        sa.Column("kind", sa.String(8), nullable=False, server_default="user"),
        sa.Column("name", sa.String(200), nullable=False),
        sa.Column("nd_playlist_id", sa.String(64)),
        sa.Column("mirrored", sa.JSON(), nullable=False, server_default="[]"),
        sa.Column("nd_cover", sa.String(128)),
        sa.Column("created_at", sa.DateTime(), nullable=False),
        sa.Column("updated_at", sa.DateTime(), nullable=False),
    )
    op.create_index("ix_playlists_user", "playlists", ["user_id", "kind"])
    op.create_index("ix_playlists_nd_playlist_id", "playlists", ["nd_playlist_id"])
    op.create_table(
        "playlist_items",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column("playlist_id", sa.Integer(), sa.ForeignKey("playlists.id", ondelete="CASCADE"), nullable=False),
        sa.Column("track_id", sa.Integer(), sa.ForeignKey("tracks.id", ondelete="CASCADE"), nullable=False),
        sa.Column("position", sa.Integer(), nullable=False),
        sa.Column("added_at", sa.DateTime(), nullable=False),
    )
    op.create_index("ix_playlist_items_order", "playlist_items", ["playlist_id", "position"])
    op.create_index("ix_playlist_items_track_id", "playlist_items", ["track_id"])
    with op.batch_alter_table("import_collections") as b:
        b.add_column(sa.Column("playlist_id", sa.Integer(), nullable=True))
        b.add_column(sa.Column("given", sa.JSON(), nullable=False, server_default="[]"))
        b.create_foreign_key("fk_import_collections_playlist", "playlists", ["playlist_id"], ["id"], ondelete="SET NULL")
    # the playlists of an import were its own to rewrite: it says nothing of them until it is run again
    op.execute("DELETE FROM pending_playlist_adds")


def downgrade():
    raise NotImplementedError("2.0 does not go back")
