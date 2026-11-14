"""The wide picture over an artist's page

Found once (app/catalog/banners.py) and kept on the Hub — or that there is none, to look again in a month.

Revision ID: 20a000000014
Revises: 20a000000013
"""

import sqlalchemy as sa
from alembic import op

revision = "20a000000014"
down_revision = "20a000000013"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "artist_banners",
        sa.Column("artist_ref", sa.String(length=64), primary_key=True),
        sa.Column("image", sa.LargeBinary(), nullable=True),
        sa.Column("key", sa.String(length=16), nullable=True),
        sa.Column("checked_at", sa.DateTime(), nullable=False),
    )


def downgrade() -> None:
    raise NotImplementedError("2.0 does not go back")
