"""Listens imported from another service's history («Статистика»)."""

import sqlalchemy as sa
from alembic import op

revision = "f9b2d4e6a017"
down_revision = "e8a1c3d5f906"
branch_labels = depends_on = None


def upgrade():
    op.create_table(
        "listen_history",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column("user_id", sa.Integer(), sa.ForeignKey("users.id", ondelete="CASCADE"), nullable=False),
        sa.Column("ts", sa.DateTime(), nullable=False),
        sa.Column("ms", sa.Integer(), nullable=False),
        sa.Column("key", sa.String(200), nullable=False),
        sa.Column("spotify_id", sa.String(32)),
        sa.Column("title", sa.String(512), nullable=False),
        sa.Column("artist", sa.String(512), nullable=False),
        sa.Column("album", sa.String(512)),
        sa.Column("source", sa.String(16), nullable=False, server_default="spotify"),
        sa.UniqueConstraint("user_id", "ts", "key", name="uq_listen_history"),
    )
    op.create_index("ix_listen_history_user_ts", "listen_history", ["user_id", "ts"])


def downgrade():
    op.drop_index("ix_listen_history_user_ts", "listen_history")
    op.drop_table("listen_history")
