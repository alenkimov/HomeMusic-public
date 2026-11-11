"""Saved filters by tags."""

import sqlalchemy as sa
from alembic import op

revision = "e8a1c3d5f906"
down_revision = "d7f0b2c4e895"
branch_labels = depends_on = None


def upgrade():
    op.create_table(
        "tag_selections",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column("user_id", sa.Integer(), sa.ForeignKey("users.id", ondelete="CASCADE"), nullable=False),
        sa.Column("name", sa.String(128), nullable=False),
        sa.Column("scope", sa.String(80), nullable=False),
        sa.Column("filter_json", sa.JSON(), nullable=False),
        sa.Column("created_at", sa.DateTime(), nullable=False),
    )
    op.create_index("ix_tag_selections_user_id", "tag_selections", ["user_id"])


def downgrade():
    op.drop_index("ix_tag_selections_user_id", "tag_selections")
    op.drop_table("tag_selections")
