"""Tags of tracks: languages, genres, the users' own."""

import sqlalchemy as sa
from alembic import op

revision = "d7f0b2c4e895"
down_revision = "c6e9a1b3d784"
branch_labels = depends_on = None


def upgrade():
    op.create_table(
        "tags",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column("group", sa.String(8), nullable=False),
        sa.Column("name", sa.String(64), nullable=False),
        sa.Column("code", sa.String(16), nullable=True),
        sa.Column("user_id", sa.Integer(), sa.ForeignKey("users.id", ondelete="CASCADE"), nullable=True),
        sa.Column("created_at", sa.DateTime(), nullable=False),
    )
    op.create_index("ix_tags_group_user", "tags", ["group", "user_id"])
    op.create_table(
        "track_tags",
        sa.Column("track_id", sa.Integer(), sa.ForeignKey("tracks.id", ondelete="CASCADE"), primary_key=True),
        sa.Column("tag_id", sa.Integer(), sa.ForeignKey("tags.id", ondelete="CASCADE"), primary_key=True),
        sa.Column("auto", sa.Boolean(), nullable=False, server_default=sa.false()),
    )
    op.create_index("ix_track_tags_tag_id", "track_tags", ["tag_id"])
    op.create_table(
        "tags_removed",
        sa.Column("track_id", sa.Integer(), sa.ForeignKey("tracks.id", ondelete="CASCADE"), primary_key=True),
        sa.Column("tag_id", sa.Integer(), sa.ForeignKey("tags.id", ondelete="CASCADE"), primary_key=True),
    )


def downgrade():
    op.drop_table("tags_removed")
    op.drop_index("ix_track_tags_tag_id", "track_tags")
    op.drop_table("track_tags")
    op.drop_index("ix_tags_group_user", "tags")
    op.drop_table("tags")
