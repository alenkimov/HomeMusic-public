"""The library's files kept in the backups on Google Drive.

Revision ID: 30a000000008
Revises: 30a000000007
"""
from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op

revision: str = '30a000000008'
down_revision: Union[str, None] = '30a000000007'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.create_table(
        "backup_files",
        sa.Column("key", sa.String(80), primary_key=True),
        sa.Column("drive_id", sa.String(128), nullable=False),
        sa.Column("size", sa.BigInteger(), nullable=False, server_default="0"),
        sa.Column("stamp", sa.String(64), nullable=False),
        sa.Column("uploaded_at", sa.DateTime(), nullable=False, server_default=sa.func.now()),
        sa.Column("gone_at", sa.DateTime()),
    )


def downgrade() -> None:
    op.drop_table("backup_files")
