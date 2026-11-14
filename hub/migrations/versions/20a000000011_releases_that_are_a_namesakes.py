"""Releases that are a namesake's

The catalog keeps artists of one name under one id; a listener says which releases are not this artist's, for the
whole server (app/catalog/namesakes.py).

Revision ID: 20a000000011
Revises: 20a000000010
"""

import sqlalchemy as sa
from alembic import op

revision = "20a000000011"
down_revision = "20a000000010"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "artist_release_exclusions",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column("artist_ref", sa.String(length=64), nullable=False),
        sa.Column("artist", sa.String(length=512), nullable=False),
        sa.Column("album_ref", sa.String(length=64), nullable=False),
        sa.Column("title", sa.String(length=512), nullable=False),
        sa.Column("record_type", sa.String(length=16), nullable=True),
        sa.Column("year", sa.Integer(), nullable=True),
        sa.Column("cover_url", sa.String(length=512), nullable=True),
        sa.Column("created_by", sa.Integer(), sa.ForeignKey("users.id", ondelete="SET NULL"), nullable=True),
        sa.Column("created_at", sa.DateTime(), nullable=False),
        sa.UniqueConstraint("artist_ref", "album_ref", name="uq_artist_release_exclusions"),
    )


def downgrade() -> None:
    raise NotImplementedError("2.0 does not go back")
