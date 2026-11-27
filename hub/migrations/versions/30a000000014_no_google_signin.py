"""Sign-in with Google is gone (it was the Android app's): the accounts' links to Google go with it.

Revision ID: 30a000000014
Revises: 30a000000013
"""
from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op

revision: str = '30a000000014'
down_revision: Union[str, None] = '30a000000013'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.drop_column("users", "google_email")
    op.drop_column("users", "google_sub")


def downgrade() -> None:
    op.add_column("users", sa.Column("google_sub", sa.String(255), unique=True))
    op.add_column("users", sa.Column("google_email", sa.String(320)))
