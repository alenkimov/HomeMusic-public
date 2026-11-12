"""2.0: a download's track is in its playlists at once — nothing waits to be added

Revision ID: c8e1a3b5d906
Revises: b7d0f2a4c895
"""

from alembic import op

revision = "c8e1a3b5d906"
down_revision = "b7d0f2a4c895"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.drop_table("pending_playlist_adds")


def downgrade() -> None:
    raise NotImplementedError("2.0 does not go back")
