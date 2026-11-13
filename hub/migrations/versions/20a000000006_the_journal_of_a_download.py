"""The journal of a download

A download keeps its steps: where the file was looked for, which ones were tried and why each was dropped or taken.

Revision ID: 20a000000006
Revises: 20a000000005
"""

import sqlalchemy as sa
from alembic import op

revision = "20a000000006"
down_revision = "20a000000005"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column("download_items", sa.Column("log_json", sa.JSON(), nullable=False, server_default="[]"))


def downgrade() -> None:
    raise NotImplementedError("2.0 does not go back")
