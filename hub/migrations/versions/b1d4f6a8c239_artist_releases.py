"""«Релизы»: what the artists a user follows have put out."""

import sqlalchemy as sa
from alembic import op

revision = "b1d4f6a8c239"
down_revision = "a0c3e5f7b128"
branch_labels = depends_on = None


def upgrade():
    op.create_table(
        "artist_releases",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column("user_id", sa.Integer(), sa.ForeignKey("users.id", ondelete="CASCADE"), nullable=False),
        sa.Column("artist_ref", sa.String(64), nullable=False),
        sa.Column("artist", sa.String(512), nullable=False),
        sa.Column("album_ref", sa.String(64), nullable=False),
        sa.Column("title", sa.String(512), nullable=False),
        sa.Column("record_type", sa.String(16), nullable=False, server_default="album"),
        sa.Column("release_date", sa.Date(), nullable=False),
        sa.Column("cover_url", sa.String(512)),
        sa.Column("nb_tracks", sa.Integer()),
        sa.Column("found_at", sa.DateTime(), nullable=False),
        sa.Column("seen_at", sa.DateTime()),
        sa.UniqueConstraint("user_id", "album_ref", name="uq_artist_releases"),
    )
    op.create_index("ix_artist_releases_user_date", "artist_releases", ["user_id", "release_date"])
    op.create_table(
        "artist_release_checks",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column("user_id", sa.Integer(), sa.ForeignKey("users.id", ondelete="CASCADE"), nullable=False),
        sa.Column("artist_ref", sa.String(64), nullable=False),
        sa.Column("checked_at", sa.DateTime(), nullable=False),
        sa.UniqueConstraint("user_id", "artist_ref", name="uq_artist_release_checks"),
    )


def downgrade():
    op.drop_table("artist_release_checks")
    op.drop_index("ix_artist_releases_user_date", table_name="artist_releases")
    op.drop_table("artist_releases")
