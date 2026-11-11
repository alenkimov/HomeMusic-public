"""A playlist's own picture."""

import sqlalchemy as sa
from alembic import op

revision = "c6e9a1b3d784"
down_revision = "b5d8f0a2c673"
branch_labels = depends_on = None


def upgrade():
    op.create_table(
        "playlist_covers",
        sa.Column("nd_playlist_id", sa.String(64), primary_key=True),
        sa.Column("image", sa.LargeBinary(), nullable=False),
        sa.Column("key", sa.String(16), nullable=False),
        sa.Column("updated_at", sa.DateTime(), nullable=False),
    )


def downgrade():
    op.drop_table("playlist_covers")
