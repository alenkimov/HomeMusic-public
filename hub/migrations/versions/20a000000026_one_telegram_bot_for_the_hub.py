"""one telegram bot for the hub

Revision ID: 20a000000026
Revises: 20a000000025
Create Date: 2026-11-18 15:56:58.517658
"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


revision: str = '20a000000026'
down_revision: Union[str, None] = '20a000000025'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.create_table(
        "telegram_accounts",
        sa.Column("user_id", sa.Integer(), sa.ForeignKey("users.id", ondelete="CASCADE"), primary_key=True),
        sa.Column("bot_id", sa.String(32), nullable=False),
        sa.Column("telegram_id", sa.String(32)),
        sa.Column("name", sa.String(128)),
        sa.Column("link_digest", sa.String(64), unique=True),
        sa.Column("link_expires_at", sa.DateTime()),
        sa.UniqueConstraint("bot_id", "telegram_id", name="uq_telegram_accounts_identity"),
    )
    op.create_table(
        "telegram_groups",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column("bot_id", sa.String(32), nullable=False),
        sa.Column("chat_id", sa.String(32), nullable=False),
        sa.Column("kind", sa.String(16), nullable=False),
        sa.Column("title", sa.String(256), nullable=False),
        sa.Column("photo", sa.LargeBinary()),
        sa.Column("photo_key", sa.String(64)),
        sa.UniqueConstraint("bot_id", "chat_id", name="uq_telegram_groups_chat"),
        sqlite_autoincrement=True,
    )
    op.drop_table("tg_chats")
    for name in ("tg_bot_token_enc", "tg_bot_username", "tg_chat_id", "tg_chat_name"):
        op.drop_column("users", name)


def downgrade() -> None:
    op.add_column("users", sa.Column("tg_bot_token_enc", sa.Text()))
    op.add_column("users", sa.Column("tg_bot_username", sa.String(64)))
    op.add_column("users", sa.Column("tg_chat_id", sa.String(32)))
    op.add_column("users", sa.Column("tg_chat_name", sa.String(128)))
    op.create_table(
        "tg_chats",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column("user_id", sa.Integer(), sa.ForeignKey("users.id", ondelete="CASCADE"), nullable=False),
        sa.Column("chat_id", sa.String(32), nullable=False),
        sa.Column("kind", sa.String(16), nullable=False),
        sa.Column("title", sa.String(256), nullable=False),
        sa.Column("photo", sa.LargeBinary()),
        sa.Column("photo_key", sa.String(64)),
        sa.Column("created_at", sa.DateTime(), nullable=False),
        sa.Column("updated_at", sa.DateTime(), nullable=False),
        sa.UniqueConstraint("user_id", "chat_id", name="uq_tg_chats_user_chat"),
    )
    op.create_index("ix_tg_chats_user_id", "tg_chats", ["user_id"])
    op.drop_table("telegram_groups")
    op.drop_table("telegram_accounts")
