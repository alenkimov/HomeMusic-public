"""Store confirmed catalog references independently of a track's metadata."""

import sqlalchemy as sa
from alembic import op

revision = "20a000000031"
down_revision = "20a000000030"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "track_catalog_refs",
        sa.Column("ref", sa.String(64), primary_key=True),
        sa.Column("track_id", sa.Integer(), sa.ForeignKey("tracks.id", ondelete="CASCADE"), nullable=False),
    )
    op.create_index("ix_track_catalog_refs_track_id", "track_catalog_refs", ["track_id"])
    op.execute("""
        INSERT INTO track_catalog_refs (ref, track_id)
        SELECT ref, id FROM tracks WHERE substr(ref, 1, instr(ref, ':') - 1) IN ('sp', 'dz', 'ym', 'hm', 'mb')
    """)


def downgrade() -> None:
    op.drop_table("track_catalog_refs")
