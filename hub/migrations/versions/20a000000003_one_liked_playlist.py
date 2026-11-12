"""«Любимое» and «Микс дня» are one per user

Two requests at once could each make a «Любимое»: the first one made is the one in use, the others are copies
nobody reads. They go, and the database keeps it from happening again.

Revision ID: 20a000000003
Revises: 20a000000002
"""

import sqlalchemy as sa
from alembic import op

revision = "20a000000003"
down_revision = "20a000000002"
branch_labels = None
depends_on = None


def upgrade() -> None:
    extra = """SELECT id FROM playlists p WHERE kind IN ('liked', 'mix') AND id > (
        SELECT MIN(id) FROM playlists q WHERE q.user_id = p.user_id AND q.kind = p.kind)"""
    op.execute(f"DELETE FROM playlist_items WHERE playlist_id IN ({extra})")
    op.execute(f"UPDATE import_collections SET playlist_id = NULL WHERE playlist_id IN ({extra})")
    op.execute(f"DELETE FROM playlist_covers WHERE playlist_id IN ({extra})")
    op.execute(f"DELETE FROM playlists WHERE id IN ({extra})")
    op.create_index("uq_playlists_one_of_a_kind", "playlists", ["user_id", "kind"], unique=True,
                    sqlite_where=sa.text("kind IN ('liked', 'mix')"))


def downgrade() -> None:
    raise NotImplementedError("2.0 does not go back")
