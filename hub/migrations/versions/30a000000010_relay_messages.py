"""The Hub as two processes: a message too long for a NOTIFY waits here for the other one (`app.relay`).

Revision ID: 30a000000010
Revises: 30a000000009
"""
from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op

revision: str = '30a000000010'
down_revision: Union[str, None] = '30a000000009'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.create_table(
        "relay_messages",
        sa.Column("id", sa.BigInteger, primary_key=True),
        sa.Column("body", sa.Text, nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()),
    )


def downgrade() -> None:
    op.drop_table("relay_messages")
