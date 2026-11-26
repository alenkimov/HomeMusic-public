"""Proxies of accounts and services, and the journal of the Hub's requests to outside services (`app.outbound`).

Revision ID: 30a000000011
Revises: 30a000000010
"""
from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op

revision: str = '30a000000011'
down_revision: Union[str, None] = '30a000000010'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.create_table(
        "proxies",
        sa.Column("id", sa.Integer, primary_key=True),
        sa.Column("name", sa.String(100)),
        sa.Column("protocol", sa.String(10), nullable=False, server_default="http"),
        sa.Column("host", sa.String(255), nullable=False),
        sa.Column("port", sa.Integer, nullable=False),
        sa.Column("login", sa.String(255)),
        sa.Column("password_enc", sa.Text),
        sa.Column("ip", sa.String(64)),
        sa.Column("country_code", sa.String(2)),
        sa.Column("checked_at", sa.DateTime),
        sa.Column("check_ok", sa.Boolean),
        sa.Column("check_error", sa.String(300)),
        sa.Column("created_at", sa.DateTime, nullable=False, server_default=sa.func.now()),
        sa.CheckConstraint("port BETWEEN 1 AND 65535"),
        sa.CheckConstraint("protocol IN ('http', 'https', 'socks5', 'socks5h')"),
        sa.UniqueConstraint("host", "port", "login", postgresql_nulls_not_distinct=True),
    )
    op.create_table(
        "service_proxies",
        sa.Column("service", sa.String(32), primary_key=True),
        sa.Column("proxy_id", sa.Integer, sa.ForeignKey("proxies.id", ondelete="CASCADE"), nullable=False, index=True),
    )
    op.add_column("integration_connections",
                  sa.Column("proxy_id", sa.Integer, sa.ForeignKey("proxies.id", ondelete="SET NULL")))
    op.create_index("ix_integration_connections_proxy_id", "integration_connections", ["proxy_id"])
    op.create_table(
        "outbound_requests",
        sa.Column("id", sa.BigInteger, primary_key=True),
        sa.Column("started_at", sa.DateTime, nullable=False),
        sa.Column("service", sa.String(32), nullable=False),
        sa.Column("method", sa.String(10), nullable=False),
        sa.Column("host", sa.String(255), nullable=False),
        sa.Column("path", sa.String(500), nullable=False),
        sa.Column("purpose", sa.String(100)),
        sa.Column("auth", sa.String(10)),
        sa.Column("connection_id", sa.Integer, sa.ForeignKey("integration_connections.id", ondelete="SET NULL")),
        sa.Column("proxy_id", sa.Integer, sa.ForeignKey("proxies.id", ondelete="SET NULL")),
        sa.Column("status", sa.Integer),
        sa.Column("error", sa.String(300)),
        sa.Column("elapsed_ms", sa.Integer, nullable=False),
        sa.Column("bytes", sa.BigInteger),
        sa.Column("process", sa.String(10)),
    )
    op.create_index("ix_outbound_requests_started_at", "outbound_requests", ["started_at"])
    op.create_index("ix_outbound_requests_service_at", "outbound_requests", ["service", "started_at"])
    op.create_index("ix_outbound_requests_proxy_at", "outbound_requests", ["proxy_id", "started_at"])
    op.create_index("ix_outbound_requests_connection_at", "outbound_requests", ["connection_id", "started_at"])


def downgrade() -> None:
    op.drop_table("outbound_requests")
    op.drop_index("ix_integration_connections_proxy_id", "integration_connections")
    op.drop_column("integration_connections", "proxy_id")
    op.drop_table("service_proxies")
    op.drop_table("proxies")
