"""Give a Navidrome account one Hub identity regardless of username casing."""

import sqlalchemy as sa
from alembic import op

revision = "20a000000034"
down_revision = "20a000000033"
branch_labels = None
depends_on = None


def upgrade() -> None:
    if op.get_bind().execute(sa.text(
        "SELECT 1 FROM users GROUP BY nd_username COLLATE NOCASE HAVING COUNT(*) > 1 LIMIT 1"
    )).first():
        raise RuntimeError("Resolve existing case-duplicate Navidrome accounts before upgrading the Hub")
    op.create_index("uq_users_nd_username_nocase", "users", [sa.text("nd_username COLLATE NOCASE")], unique=True)


def downgrade() -> None:
    op.drop_index("uq_users_nd_username_nocase", table_name="users")
