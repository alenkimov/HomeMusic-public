"""A followed artist keeps their photo

«Подписки» shows the artists as pictures. Every artist is looked up again to learn theirs.

Revision ID: 20a000000010
Revises: 20a000000009
"""

import sqlalchemy as sa
from alembic import op

revision = "20a000000010"
down_revision = "20a000000009"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column("artist_release_checks", sa.Column("picture_url", sa.String(length=512), nullable=True))
    op.execute("UPDATE artist_release_checks SET checked_at = '2000-01-01 00:00:00'")


def downgrade() -> None:
    raise NotImplementedError("2.0 does not go back")
