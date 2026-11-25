"""«Наведение порядка»: the changes it offers, the questions its AI answered «нет», and AI spending of the Hub's own.

Revision ID: 30a000000007
Revises: 30a000000006
"""
from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision: str = '30a000000007'
down_revision: Union[str, None] = '30a000000006'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.create_table(
        "tidy_proposals",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column("key", sa.String(128), nullable=False, unique=True),
        sa.Column("kind", sa.String(16), nullable=False),
        sa.Column("section", sa.String(16), nullable=False),
        sa.Column("subject_type", sa.String(8), nullable=False),
        sa.Column("subject_id", sa.Integer(), nullable=False),
        sa.Column("was_json", postgresql.JSONB(), nullable=False),
        sa.Column("to_json", postgresql.JSONB(), nullable=False),
        sa.Column("show_json", postgresql.JSONB(), nullable=False, server_default="{}"),
        sa.Column("by", sa.String(8), nullable=False),
        sa.Column("sure", sa.Boolean(), nullable=False, server_default=sa.true()),
        sa.Column("why", sa.String(300), nullable=False, server_default=""),
        sa.Column("state", sa.String(10), nullable=False, server_default="open"),
        sa.Column("undo_json", postgresql.JSONB()),
        sa.Column("created_at", sa.DateTime(), nullable=False, server_default=sa.func.now()),
        sa.Column("decided_at", sa.DateTime()),
        sa.Column("decided_by", sa.Integer(), sa.ForeignKey("users.id", ondelete="SET NULL")),
    )
    op.create_index("ix_tidy_proposals_open", "tidy_proposals", ["section", "state"])
    op.create_index("ix_tidy_proposals_subject_id", "tidy_proposals", ["subject_id"])
    op.create_index("ix_tidy_proposals_decided_at", "tidy_proposals", ["decided_at"])
    op.create_table(
        "tidy_asked",
        sa.Column("key", sa.String(64), primary_key=True),
        sa.Column("at", sa.DateTime(), nullable=False, server_default=sa.func.now()),
    )
    op.alter_column("ai_usage", "user_id", nullable=True)


def downgrade() -> None:
    op.execute("DELETE FROM ai_usage WHERE user_id IS NULL")
    op.alter_column("ai_usage", "user_id", nullable=False)
    op.drop_table("tidy_asked")
    op.drop_table("tidy_proposals")
