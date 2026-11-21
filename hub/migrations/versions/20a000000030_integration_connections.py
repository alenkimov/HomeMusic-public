"""Store service connections independently of transfers and Hub user records."""

import sqlalchemy as sa
from alembic import op

revision = "20a000000030"
down_revision = "20a000000029"
branch_labels = None
depends_on = None


def upgrade() -> None:
    connections = op.create_table(
        "integration_connections",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column("provider", sa.String(20), nullable=False),
        sa.Column("owner_user_id", sa.Integer(), sa.ForeignKey("users.id", ondelete="CASCADE")),
        sa.Column("subject", sa.String(256)),
        sa.Column("account", sa.String(256)),
        sa.Column("shared", sa.Boolean(), nullable=False),
        sa.Column("retained", sa.Boolean(), nullable=False),
        sa.Column("state", sa.String(24), nullable=False),
        sa.Column("plan", sa.String(20)),
        sa.Column("connected_at", sa.DateTime()),
        sa.Column("checked_at", sa.DateTime()),
        sa.UniqueConstraint("provider", "owner_user_id", "subject"),
        sa.CheckConstraint("shared = 0 OR provider IN ('yandex', 'spotify', 'lastfm')"),
        sa.CheckConstraint("owner_user_id IS NOT NULL OR provider = 'lastfm' OR "
                           "(provider = 'spotify' AND shared = 0 AND state = 'unclaimed')"),
    )
    op.create_index("ix_integration_connections_owner_user_id", "integration_connections", ["owner_user_id"])
    op.create_index("uq_integration_lastfm", "integration_connections", ["provider"], unique=True,
                    sqlite_where=sa.text("provider = 'lastfm'"))
    credentials = op.create_table(
        "integration_credentials",
        sa.Column("connection_id", sa.Integer(), sa.ForeignKey("integration_connections.id", ondelete="CASCADE"),
                  primary_key=True),
        sa.Column("encrypted", sa.Text(), nullable=False),
    )
    op.create_table(
        "integration_auth_attempts",
        sa.Column("digest", sa.String(64), primary_key=True),
        sa.Column("owner_user_id", sa.Integer(), sa.ForeignKey("users.id", ondelete="CASCADE"), nullable=False),
        sa.Column("provider", sa.String(20), nullable=False),
        sa.Column("stage", sa.String(24), nullable=False),
        sa.Column("shared", sa.Boolean(), nullable=False),
        sa.Column("expires_at", sa.DateTime(), nullable=False),
        sa.Column("next_poll_at", sa.DateTime()),
        sa.Column("payload", sa.JSON(), nullable=False),
    )
    op.create_index("ix_integration_auth_attempts_owner_user_id", "integration_auth_attempts", ["owner_user_id"])
    op.create_index("ix_integration_auth_attempts_expires_at", "integration_auth_attempts", ["expires_at"])
    op.create_table(
        "listenbrainz_sync",
        sa.Column("connection_id", sa.Integer(), sa.ForeignKey("integration_connections.id", ondelete="CASCADE"),
                  primary_key=True),
        sa.Column("sent_until", sa.DateTime()),
    )
    op.execute("ALTER TABLE imports ADD COLUMN connection_id INTEGER REFERENCES integration_connections(id) ON DELETE SET NULL")
    op.create_index("ix_imports_connection_id", "imports", ["connection_id"])
    db = op.get_bind()

    def move(provider, owner, encrypted, *, account=None, subject=None, retained=True):
        result = db.execute(connections.insert().values(
            provider=provider, owner_user_id=owner, account=account, subject=subject,
            retained=retained, shared=False, state="connected"))
        connection_id = result.inserted_primary_key[0]
        db.execute(credentials.insert().values(connection_id=connection_id, encrypted=encrypted))
        return connection_id

    rows = db.execute(sa.text("""
        SELECT id, user_id, source, account, token_enc, keep_token
        FROM imports WHERE source IN ('yandex', 'vk') AND token_enc IS NOT NULL
    """)).all()
    for row in rows:
        connection = move(row.source, row.user_id, row.token_enc, account=row.account, retained=bool(row.keep_token))
        db.execute(sa.text("UPDATE imports SET connection_id = :connection WHERE id = :id"),
                   {"connection": connection, "id": row.id})
    users = db.execute(sa.text("SELECT id, lb_user, lb_token_enc FROM users WHERE lb_token_enc IS NOT NULL")).all()
    for user in users:
        connection = move("listenbrainz", user.id, user.lb_token_enc, account=user.lb_user, subject=user.lb_user)
        db.execute(sa.text("""
            INSERT INTO listenbrainz_sync (connection_id, sent_until)
            SELECT :connection, lb_sent_until FROM users WHERE id = :user
        """), {"connection": connection, "user": user.id})
    # The previous Spotify sign-in was server-wide. Reconnect it under a Hub user.
    op.execute("DELETE FROM app_settings WHERE key = 'spotify_account'")
    op.execute("ALTER TABLE imports DROP COLUMN token_enc")
    for column in ("lb_token_enc", "lb_user", "lb_sent_until"):
        op.execute(f"ALTER TABLE users DROP COLUMN {column}")


def downgrade() -> None:
    raise RuntimeError("The previous schema cannot represent multiple or shared connections. Restore a pre-upgrade backup.")
