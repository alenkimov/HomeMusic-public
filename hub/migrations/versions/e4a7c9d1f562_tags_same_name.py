"""Tags of one user that differ only in the case of their letters become one.

The Hub looked for a tag of the same name with SQLite's `lower()`, which leaves Cyrillic as it is: «Пляжный» made
twice was two tags. The tracks of the later ones go to the first, and the later ones are gone."""

import sqlalchemy as sa
from alembic import op

revision = "e4a7c9d1f562"
down_revision = "d3f6b8c0e451"
branch_labels = depends_on = None


def upgrade():
    c = op.get_bind()
    first: dict[tuple, int] = {}
    for tag_id, group, name, user_id in c.execute(sa.text('SELECT id, "group", name, user_id FROM tags ORDER BY id')).all():
        key = (group, user_id, " ".join(str(name).split()).casefold())
        keep = first.setdefault(key, tag_id)
        if keep == tag_id:
            continue
        for table in ("track_tags", "tags_removed"):
            try:
                c.execute(sa.text(f"UPDATE OR IGNORE {table} SET tag_id = :keep WHERE tag_id = :gone"), {"keep": keep, "gone": tag_id})
                c.execute(sa.text(f"DELETE FROM {table} WHERE tag_id = :gone"), {"gone": tag_id})
            except sa.exc.OperationalError:
                pass  # no such table in this database
        c.execute(sa.text("DELETE FROM tags WHERE id = :gone"), {"gone": tag_id})


def downgrade():
    pass
