"""Translations of lyrics, one per track and language."""

import sqlalchemy as sa
from alembic import op

revision = "b5d8f0a2c673"
down_revision = "a4c7e9f1b562"
branch_labels = depends_on = None


def upgrade():
    op.create_table(
        "lyrics_translations",
        sa.Column("track_id", sa.Integer(), sa.ForeignKey("tracks.id", ondelete="CASCADE"), primary_key=True),
        sa.Column("lang", sa.String(8), primary_key=True),
        sa.Column("provider", sa.String(16), nullable=True),
        sa.Column("lines_json", sa.JSON(), nullable=True),
        sa.Column("source_sig", sa.String(40), nullable=True),
        sa.Column("edited_json", sa.JSON(), nullable=True),
        sa.Column("created_at", sa.DateTime(), nullable=False),
        sa.Column("updated_at", sa.DateTime(), nullable=False),
    )


def downgrade():
    op.drop_table("lyrics_translations")
