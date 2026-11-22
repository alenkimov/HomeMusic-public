"""Track cards (`app.library.cards`): a track as every list shows it, kept; the database marks a card stale itself when
anything it was made of changes.

A card's row names what it was made of beyond its own track (`deps`, tokens):
  k:<key>   a name of an artist in its credits, as `artists.key` makes it (`artist_names.key`)
  r:<ref>   a catalog record of an artist it names (`artist_refs.ref`)
  a:<id>    an artist it was tied to
  c:<key>   a search of its recording (`catalog_cache.key`, "listen:<ref>")
  g:<ref>   its catalog album, whose group of the same release in other catalogs gives its other recordings
and the triggers below mark (`dirty`, `gen` + 1) the cards whose track, files, covers, album, journal of downloads,
transfers' rows or versions changed, and those whose tokens a change touches.

Revision ID: 30a000000004
Revises: 30a000000003
"""
from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision: str = '30a000000004'
down_revision: Union[str, None] = '30a000000003'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None

# what of a track its card is drawn from (`cards.draw`): a change of anything else of it (its genres, when they were
# looked up) leaves the card as it is
TRACK_COLUMNS = ("album_id", "artist", "title", "album", "album_artist", "track_no", "disc_no", "duration_s", "isrc",
                 "ref", "explicit", "catalog", "renamed_at", "cover_key")

TRIGGERS = {
    # table: (events, WHEN or None, body of the function)
    "tracks": ("UPDATE", "({old}) IS DISTINCT FROM ({new})".format(
        old=", ".join(f"OLD.{c}" for c in TRACK_COLUMNS), new=", ".join(f"NEW.{c}" for c in TRACK_COLUMNS)),
        "PERFORM hm_mark_cards(ARRAY[NEW.id]);"),
    "track_files": ("INSERT OR UPDATE OR DELETE", None, """
        IF TG_OP <> 'INSERT' THEN PERFORM hm_mark_cards(ARRAY[OLD.track_id]); END IF;
        IF TG_OP <> 'DELETE' THEN PERFORM hm_mark_cards(ARRAY[NEW.track_id]); END IF;"""),
    "track_covers": ("INSERT OR UPDATE OR DELETE", None, """
        IF TG_OP <> 'INSERT' THEN PERFORM hm_mark_cards(ARRAY[OLD.track_id]); END IF;
        IF TG_OP <> 'DELETE' THEN PERFORM hm_mark_cards(ARRAY[NEW.track_id]); END IF;"""),
    # the album's picture (its address names the album's last change)
    "albums": ("UPDATE", "(OLD.cover_path, OLD.updated_at) IS DISTINCT FROM (NEW.cover_path, NEW.updated_at)",
               "PERFORM hm_mark_cards(ARRAY(SELECT id FROM tracks WHERE album_id = NEW.id));"),
    # a file whose download kept its journal: only whose track and whether there is a journal (not its progress)
    "download_items": ("INSERT OR UPDATE OR DELETE", None, """
        IF TG_OP = 'UPDATE' AND OLD.track_id IS NOT DISTINCT FROM NEW.track_id
           AND (jsonb_array_length(OLD.log_json) > 0) = (jsonb_array_length(NEW.log_json) > 0) THEN RETURN NULL; END IF;
        IF TG_OP <> 'INSERT' AND OLD.track_id IS NOT NULL THEN PERFORM hm_mark_cards(ARRAY[OLD.track_id]); END IF;
        IF TG_OP <> 'DELETE' AND NEW.track_id IS NOT NULL THEN PERFORM hm_mark_cards(ARRAY[NEW.track_id]); END IF;"""),
    # a transfer's row of a track: its picture is the track's when it has no other
    "import_items": ("INSERT OR UPDATE OR DELETE", None, """
        IF TG_OP = 'UPDATE' AND OLD.track_id IS NOT DISTINCT FROM NEW.track_id
           AND OLD.raw_json->>'cover_url' IS NOT DISTINCT FROM NEW.raw_json->>'cover_url' THEN RETURN NULL; END IF;
        IF TG_OP <> 'INSERT' AND OLD.track_id IS NOT NULL THEN PERFORM hm_mark_cards(ARRAY[OLD.track_id]); END IF;
        IF TG_OP <> 'DELETE' AND NEW.track_id IS NOT NULL THEN PERFORM hm_mark_cards(ARRAY[NEW.track_id]); END IF;"""),
    # how many versions a song has is on the card of each of them
    "song_tracks": ("INSERT OR UPDATE OR DELETE", None, """
        IF TG_OP <> 'INSERT' THEN
            PERFORM hm_mark_cards(ARRAY[OLD.track_id] || ARRAY(SELECT track_id FROM song_tracks WHERE song_id = OLD.song_id));
        END IF;
        IF TG_OP <> 'DELETE' THEN
            PERFORM hm_mark_cards(ARRAY[NEW.track_id] || ARRAY(SELECT track_id FROM song_tracks WHERE song_id = NEW.song_id));
        END IF;"""),
    "artist_names": ("INSERT OR UPDATE OR DELETE", None, """
        IF TG_OP <> 'INSERT' THEN PERFORM hm_mark_cards_by(ARRAY['k:' || OLD.key, 'a:' || OLD.artist_id]); END IF;
        IF TG_OP <> 'DELETE' THEN PERFORM hm_mark_cards_by(ARRAY['k:' || NEW.key, 'a:' || NEW.artist_id]); END IF;"""),
    # a record tied to an artist: the cards naming the record, and those naming the artist by any of their names
    # (whether a name is theirs depends on the catalogs they have records in)
    "artist_refs": ("INSERT OR UPDATE OR DELETE", None, """
        IF TG_OP <> 'INSERT' THEN
            PERFORM hm_mark_cards_by(ARRAY['r:' || OLD.ref, 'a:' || OLD.artist_id]
                || ARRAY(SELECT 'k:' || key FROM artist_names WHERE artist_id = OLD.artist_id));
        END IF;
        IF TG_OP <> 'DELETE' THEN
            PERFORM hm_mark_cards_by(ARRAY['r:' || NEW.ref, 'a:' || NEW.artist_id]
                || ARRAY(SELECT 'k:' || key FROM artist_names WHERE artist_id = NEW.artist_id));
        END IF;"""),
    "artists": ("UPDATE OR DELETE", None, """
        IF TG_OP = 'UPDATE' AND (OLD.name, OLD.merged_into_id) IS NOT DISTINCT FROM (NEW.name, NEW.merged_into_id)
           THEN RETURN NULL; END IF;
        PERFORM hm_mark_cards_by(ARRAY['a:' || OLD.id]);"""),
    "catalog_cache": ("INSERT OR UPDATE OR DELETE", None, """
        IF TG_OP <> 'INSERT' AND OLD.key LIKE 'listen:%' THEN PERFORM hm_mark_cards_by(ARRAY['c:' || OLD.key]); END IF;
        IF TG_OP <> 'DELETE' AND NEW.key LIKE 'listen:%' THEN PERFORM hm_mark_cards_by(ARRAY['c:' || NEW.key]); END IF;"""),
    # an album found the same in another catalog: every album of the group has other recordings now
    "catalog_release_matches": ("INSERT OR UPDATE OR DELETE", None, """
        IF TG_OP <> 'INSERT' THEN
            PERFORM hm_mark_cards_by(ARRAY['g:' || OLD.ref]
                || ARRAY(SELECT 'g:' || ref FROM catalog_release_matches WHERE group_ref = OLD.group_ref));
        END IF;
        IF TG_OP <> 'DELETE' THEN
            PERFORM hm_mark_cards_by(ARRAY['g:' || NEW.ref]
                || ARRAY(SELECT 'g:' || ref FROM catalog_release_matches WHERE group_ref = NEW.group_ref));
        END IF;"""),
}


def upgrade() -> None:
    op.create_table(
        'track_cards',
        sa.Column('track_id', sa.Integer(), sa.ForeignKey('tracks.id', ondelete='CASCADE'), primary_key=True),
        sa.Column('card', postgresql.JSONB(), nullable=False),
        sa.Column('deps', postgresql.ARRAY(sa.Text()), nullable=False, server_default='{}'),
        sa.Column('nd_digest', sa.String(length=32), nullable=True),
        sa.Column('valid_until', sa.DateTime(), nullable=True),
        sa.Column('dirty', sa.Boolean(), nullable=False, server_default='false'),
        sa.Column('gen', sa.BigInteger(), nullable=False, server_default='0'),
        sa.Column('built_at', sa.DateTime(), nullable=False, server_default=sa.text("(now() AT TIME ZONE 'utc')")),
    )
    op.create_index('ix_track_cards_deps', 'track_cards', ['deps'], postgresql_using='gin')
    op.create_index('ix_track_cards_dirty', 'track_cards', ['track_id'], postgresql_where=sa.text('dirty'))
    op.create_index('ix_track_cards_valid_until', 'track_cards', ['valid_until'],
                    postgresql_where=sa.text('valid_until IS NOT NULL'))
    op.execute("""
        CREATE FUNCTION hm_mark_cards(ids integer[]) RETURNS void LANGUAGE sql AS $$
            UPDATE track_cards SET dirty = true, gen = gen + 1 WHERE track_id = ANY(ids)
        $$""")
    op.execute("""
        CREATE FUNCTION hm_mark_cards_by(tokens text[]) RETURNS void LANGUAGE sql AS $$
            UPDATE track_cards SET dirty = true, gen = gen + 1 WHERE deps && tokens
        $$""")
    for table, (events, when, body) in TRIGGERS.items():
        op.execute(f"""
            CREATE FUNCTION hm_cards_{table}() RETURNS trigger LANGUAGE plpgsql AS $$
            BEGIN
                {body}
                RETURN NULL;
            END $$""")
        op.execute(f"""
            CREATE TRIGGER hm_cards AFTER {events} ON {table}
            FOR EACH ROW {f'WHEN ({when})' if when else ''} EXECUTE FUNCTION hm_cards_{table}()""")


def downgrade() -> None:
    for table in TRIGGERS:
        op.execute(f"DROP TRIGGER IF EXISTS hm_cards ON {table}")
        op.execute(f"DROP FUNCTION IF EXISTS hm_cards_{table}()")
    op.execute("DROP FUNCTION IF EXISTS hm_mark_cards_by(text[])")
    op.execute("DROP FUNCTION IF EXISTS hm_mark_cards(integer[])")
    op.drop_table('track_cards')
