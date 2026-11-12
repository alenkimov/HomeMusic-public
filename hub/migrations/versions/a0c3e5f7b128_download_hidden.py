"""A download taken off the «Загрузки» list."""

import sqlalchemy as sa
from alembic import op

revision = "a0c3e5f7b128"
down_revision = "f9b2d4e6a017"
branch_labels = depends_on = None


def upgrade():
    op.add_column("download_items", sa.Column("hidden", sa.Boolean(), nullable=False, server_default="0"))


def downgrade():
    op.drop_column("download_items", "hidden")
