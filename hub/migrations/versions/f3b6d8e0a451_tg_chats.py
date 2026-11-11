"""The chats a user's Telegram bot can send to; the private chat already bound is the first of them."""

import sqlalchemy as sa
from alembic import op

revision = "f3b6d8e0a451"
down_revision = "e2a5c7d9f340"
branch_labels = depends_on = None


def upgrade():
    op.create_table(
        "tg_chats",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column("user_id", sa.Integer(), sa.ForeignKey("users.id", ondelete="CASCADE"), nullable=False),
        sa.Column("chat_id", sa.String(32), nullable=False),
        sa.Column("kind", sa.String(16), nullable=False),
        sa.Column("title", sa.String(256), nullable=False),
        sa.Column("photo", sa.LargeBinary(), nullable=True),
        sa.Column("photo_key", sa.String(64), nullable=True),
        sa.Column("created_at", sa.DateTime(), nullable=False),
        sa.Column("updated_at", sa.DateTime(), nullable=False),
        sa.UniqueConstraint("user_id", "chat_id", name="uq_tg_chats_user_chat"),
    )
    op.create_index("ix_tg_chats_user_id", "tg_chats", ["user_id"])
    from app.links import stamp

    op.get_bind().execute(sa.text(
        "insert into tg_chats (user_id, chat_id, kind, title, created_at, updated_at) "
        "select id, tg_chat_id, 'private', coalesce(tg_chat_name, tg_chat_id), :now, :now from users "
        "where tg_chat_id is not null and tg_bot_token_enc is not null"), {"now": stamp()})


def downgrade():
    op.drop_index("ix_tg_chats_user_id", "tg_chats")
    op.drop_table("tg_chats")
