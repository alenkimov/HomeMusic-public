"""«Любимое» remembers what it was made from (`favorites.project_liked`).

Revision ID: 30a000000002
Revises: 30a000000001
"""
from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op

revision: str = '30a000000002'
down_revision: Union[str, None] = '30a000000001'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.add_column('playlists', sa.Column('projected_from', sa.String(length=64), nullable=True))


def downgrade() -> None:
    op.drop_column('playlists', 'projected_from')
