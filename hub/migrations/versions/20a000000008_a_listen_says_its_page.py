"""A listen says the page its list was started on

«История» names where a track was played from: «Плейлист» alone does not say which one.

Revision ID: 20a000000008
Revises: 20a000000007
"""

import sqlalchemy as sa
from alembic import op

revision = "20a000000008"
down_revision = "20a000000007"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column("play_events", sa.Column("page", sa.String(length=128), nullable=True))


def downgrade() -> None:
    raise NotImplementedError("2.0 does not go back")
