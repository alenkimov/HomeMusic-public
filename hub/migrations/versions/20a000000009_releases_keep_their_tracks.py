"""Releases keep their tracks, a followed artist their latest release

«Подписки» shows the tracks of what came out at once, and beside each followed artist when they last released
something. Every artist is looked up again to learn the latter.

Revision ID: 20a000000009
Revises: 20a000000008
"""

import sqlalchemy as sa
from alembic import op

revision = "20a000000009"
down_revision = "20a000000008"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column("artist_releases", sa.Column("tracks_json", sa.JSON(), nullable=True))
    op.add_column("artist_release_checks", sa.Column("last_release_date", sa.Date(), nullable=True))
    op.add_column("artist_release_checks", sa.Column("last_release_title", sa.String(length=512), nullable=True))
    op.execute("UPDATE artist_release_checks SET checked_at = '2000-01-01 00:00:00'")


def downgrade() -> None:
    raise NotImplementedError("2.0 does not go back")
