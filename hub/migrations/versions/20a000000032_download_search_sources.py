"""Persist the sources queried by a download job."""

import sqlalchemy as sa
from alembic import op

revision = "20a000000032"
down_revision = "20a000000031"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column("download_items", sa.Column("search_sources_json", sa.JSON(), nullable=True))


def downgrade() -> None:
    op.drop_column("download_items", "search_sources_json")
