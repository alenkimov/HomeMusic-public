"""A proposal of «Наведение порядка» may be one listener's own (a match of their transfer to look over): its owner.

Revision ID: 30a000000013
Revises: 30a000000012
"""
from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op

revision: str = '30a000000013'
down_revision: Union[str, None] = '30a000000012'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.add_column("tidy_proposals", sa.Column("user_id", sa.Integer, sa.ForeignKey("users.id", ondelete="CASCADE")))
    op.create_index("ix_tidy_proposals_user_id", "tidy_proposals", ["user_id"])


def downgrade() -> None:
    op.drop_index("ix_tidy_proposals_user_id", "tidy_proposals")
    op.drop_column("tidy_proposals", "user_id")
