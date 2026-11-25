"""The assistant is gone: its chats, its tasks and its daily allowance.

The AI spending report (ai_usage) stays: it is the admin's, whoever spends.

Revision ID: 30a000000006
Revises: 30a000000005
"""
from typing import Sequence, Union

from alembic import op

revision: str = '30a000000006'
down_revision: Union[str, None] = '30a000000005'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.execute("DROP TABLE IF EXISTS assistant_chats")
    op.execute("DROP TABLE IF EXISTS assistant_tasks")
    op.execute("DROP TABLE IF EXISTS assistant_daily_usage")


def downgrade() -> None:
    raise NotImplementedError("the assistant was removed; its tables are not brought back")
