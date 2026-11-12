"""«Открытия недели»: the weekly issues."""

import sqlalchemy as sa
from alembic import op

revision = "c2e5a7b9d340"
down_revision = "b1d4f6a8c239"
branch_labels = depends_on = None


def upgrade():
    op.create_table(
        "discovery_issues",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column("user_id", sa.Integer(), sa.ForeignKey("users.id", ondelete="CASCADE"), nullable=False),
        sa.Column("week", sa.Date(), nullable=False),
        sa.Column("tracks_json", sa.JSON(), nullable=False),
        sa.Column("stats_json", sa.JSON(), nullable=False),
        sa.Column("created_at", sa.DateTime(), nullable=False),
        sa.UniqueConstraint("user_id", "week", name="uq_discovery_issues"),
    )


def downgrade():
    op.drop_table("discovery_issues")
