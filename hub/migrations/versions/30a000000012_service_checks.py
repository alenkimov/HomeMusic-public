"""The checks of outside services (`app.checks`): what the real work and the nightly checks found, and the fields
the services' answers come with.

Revision ID: 30a000000012
Revises: 30a000000011
"""
from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects.postgresql import JSONB

revision: str = '30a000000012'
down_revision: Union[str, None] = '30a000000011'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.create_table(
        "service_checks",
        sa.Column("id", sa.BigInteger, primary_key=True),
        sa.Column("check", sa.String(64), nullable=False),
        sa.Column("at", sa.DateTime, nullable=False),
        sa.Column("ok", sa.Boolean, nullable=False),
        sa.Column("source", sa.String(8), nullable=False),
        sa.Column("detail", sa.String(300)),
        sa.Column("elapsed_ms", sa.Integer),
        sa.Column("count", sa.Integer, nullable=False, server_default="1"),
    )
    op.create_index("ix_service_checks_check_at", "service_checks", ["check", "at"])
    op.create_index("ix_service_checks_at", "service_checks", ["at"])
    op.create_table(
        "service_shapes",
        sa.Column("check", sa.String(64), primary_key=True),
        sa.Column("seen", sa.Integer, nullable=False, server_default="0"),
        sa.Column("paths", JSONB, nullable=False, server_default="{}"),
        sa.Column("updated_at", sa.DateTime, nullable=False, server_default=sa.func.now()),
    )


def downgrade() -> None:
    op.drop_table("service_shapes")
    op.drop_index("ix_service_checks_at", "service_checks")
    op.drop_index("ix_service_checks_check_at", "service_checks")
    op.drop_table("service_checks")
