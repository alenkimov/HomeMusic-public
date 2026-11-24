"""«Это разные альбомы»: groups of albums of one name that a listener said are separate releases are not offered
for joining again (`album_split_dismissals`, keyed by the group's album ids).

Revision ID: 30a000000005
Revises: 30a000000004
"""
from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision: str = '30a000000005'
down_revision: Union[str, None] = '30a000000004'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.create_table(
        'album_split_dismissals',
        sa.Column('id', sa.Integer(), primary_key=True),
        sa.Column('key', sa.String(1024), nullable=False, unique=True),
        sa.Column('album_ids', postgresql.ARRAY(sa.Integer()), nullable=False),
        sa.Column('user_id', sa.Integer(), sa.ForeignKey('users.id', ondelete='SET NULL'), nullable=True),
        sa.Column('created_at', sa.DateTime(), nullable=False),
    )


def downgrade() -> None:
    op.drop_table('album_split_dismissals')
