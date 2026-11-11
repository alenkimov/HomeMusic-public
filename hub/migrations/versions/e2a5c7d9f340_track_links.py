"""A track's ids on music services, filled with the ones imports and mixes have already seen."""

import sqlalchemy as sa
from alembic import op

revision = "e2a5c7d9f340"
down_revision = "d1f4b6c8e230"
branch_labels = depends_on = None


def upgrade():
    op.create_table(
        "track_links",
        sa.Column("track_id", sa.Integer(), sa.ForeignKey("tracks.id", ondelete="CASCADE"), primary_key=True),
        sa.Column("service", sa.String(16), primary_key=True),
        sa.Column("external_id", sa.String(128), nullable=False),
        sa.Column("created_at", sa.DateTime(), nullable=False),
    )
    op.create_index("ix_track_links_external", "track_links", ["service", "external_id"])
    from app.links import FROM_IMPORTS, FROM_SOURCE_MAP, stamp

    conn = op.get_bind()
    now = stamp()
    conn.execute(sa.text(FROM_IMPORTS), {"now": now})
    conn.execute(sa.text(FROM_SOURCE_MAP), {"now": now})


def downgrade():
    op.drop_index("ix_track_links_external", "track_links")
    op.drop_table("track_links")
