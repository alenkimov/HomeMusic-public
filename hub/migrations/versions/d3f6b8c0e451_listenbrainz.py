"""A user's ListenBrainz account: the token, the name, how far the listens are sent."""

import sqlalchemy as sa
from alembic import op

revision = "d3f6b8c0e451"
down_revision = "c2e5a7b9d340"
branch_labels = depends_on = None


def upgrade():
    op.add_column("users", sa.Column("lb_token_enc", sa.Text()))
    op.add_column("users", sa.Column("lb_user", sa.String(128)))
    op.add_column("users", sa.Column("lb_sent_until", sa.DateTime()))


def downgrade():
    op.drop_column("users", "lb_sent_until")
    op.drop_column("users", "lb_user")
    op.drop_column("users", "lb_token_enc")
