"""Track cards say which file a track has (`FileOut.uid`): the kept ones are drawn anew.

Revision ID: 30a000000009
Revises: 30a000000008
"""
from typing import Sequence, Union

from alembic import op

revision: str = '30a000000009'
down_revision: Union[str, None] = '30a000000008'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.execute("UPDATE track_cards SET dirty = true")


def downgrade() -> None:
    pass
