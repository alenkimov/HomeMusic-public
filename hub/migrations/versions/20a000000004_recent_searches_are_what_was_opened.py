"""«Недавнее» of the search keeps what was opened, not what was typed

A query used to be written down every time the typing paused: «still lovi» stood there beside «still loving you».
Now a line is a result that was opened (a track, an album, an artist) or a query asked for with Enter. The old
lines are those leftovers of typing: they go.

Revision ID: 20a000000004
Revises: 20a000000003
"""

import sqlalchemy as sa
from alembic import op

revision = "20a000000004"
down_revision = "20a000000003"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.drop_table("search_history")
    op.create_table(
        "search_history",
        sa.Column("user_id", sa.Integer(), nullable=False),
        sa.Column("key", sa.String(length=1024), nullable=False),
        sa.Column("kind", sa.String(length=16), nullable=False),
        sa.Column("title", sa.String(length=512), nullable=False),
        sa.Column("subtitle", sa.String(length=512), nullable=True),
        sa.Column("ref", sa.String(length=255), nullable=True),
        sa.Column("cover_url", sa.String(length=1024), nullable=True),
        sa.Column("link", sa.String(length=512), nullable=True),
        sa.Column("searched_at", sa.DateTime(), nullable=False),
        sa.ForeignKeyConstraint(["user_id"], ["users.id"], ondelete="CASCADE"),
        sa.PrimaryKeyConstraint("user_id", "key"),
    )


def downgrade() -> None:
    raise NotImplementedError("2.0 does not go back")
