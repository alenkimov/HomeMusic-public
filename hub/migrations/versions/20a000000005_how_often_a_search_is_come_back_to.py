"""How often a line of the search's «Недавнее» is come back to

«Вы часто ищете» needs it: a line counts the times it was opened or asked for.

Revision ID: 20a000000005
Revises: 20a000000004
"""

import sqlalchemy as sa
from alembic import op

revision = "20a000000005"
down_revision = "20a000000004"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column("search_history", sa.Column("times", sa.Integer(), nullable=False, server_default="1"))


def downgrade() -> None:
    raise NotImplementedError("2.0 does not go back")
