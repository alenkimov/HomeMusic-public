"""Lyrics as one lower-case text, for a search inside them."""

import json

import sqlalchemy as sa
from alembic import op

revision = "d1f4b6c8e230"
down_revision = "c9e3a5b7d120"
branch_labels = depends_on = None


def upgrade():
    with op.batch_alter_table("lyrics") as t:
        t.add_column(sa.Column("search_text", sa.Text(), nullable=True))
    conn = op.get_bind()
    for track_id, lines, plain in conn.execute(sa.text("select track_id, lines_json, plain from lyrics where kind != 'none'")).fetchall():
        try:
            parsed = json.loads(lines) if isinstance(lines, str) else lines
        except ValueError:
            parsed = None
        text = "\n".join(str(x.get("text") or "") for x in parsed) if parsed else (plain or "")
        conn.execute(sa.text("update lyrics set search_text = :t where track_id = :i"), {"t": text.casefold(), "i": track_id})


def downgrade():
    with op.batch_alter_table("lyrics") as t:
        t.drop_column("search_text")
