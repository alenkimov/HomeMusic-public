"""The user's own Telegram bot: its key (encrypted) and the chat it sends tracks to."""

import sqlalchemy as sa
from alembic import op

revision = "b7d2e4f6a918"
down_revision = "a3c8e5f17b92"
branch_labels = depends_on = None


def upgrade():
    with op.batch_alter_table("users") as t:
        t.add_column(sa.Column("tg_bot_token_enc", sa.Text(), nullable=True))
        t.add_column(sa.Column("tg_bot_username", sa.String(64), nullable=True))
        t.add_column(sa.Column("tg_chat_id", sa.String(32), nullable=True))
        t.add_column(sa.Column("tg_chat_name", sa.String(128), nullable=True))


def downgrade():
    with op.batch_alter_table("users") as t:
        t.drop_column("tg_chat_name")
        t.drop_column("tg_chat_id")
        t.drop_column("tg_bot_username")
        t.drop_column("tg_bot_token_enc")
