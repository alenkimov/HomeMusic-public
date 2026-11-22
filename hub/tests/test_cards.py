"""A track's card is drawn once and kept, and the database marks it stale itself when anything it was drawn from
changes (`app.library.cards`, migration 30a000000004)."""

import pytest
from sqlalchemy import select, text, update

from app import db
from app.library import cards
from app.models import Artist, ArtistName, CatalogCache, Song, SongTrack, Track, TrackCard, TrackFile, utcnow
from tests.support import filed_track


@pytest.fixture
async def env(hub_env):
    db.init_engine()
    try:
        async with db.session_scope() as s:
            owned = filed_track(path="a.mp3", artist="Artist", title="With a file", ref="dz:1", fingerprint="artist|with a file")
            bare = Track(artist="Artist", title="Without one", ref="dz:2", fingerprint="artist|without one",
                         catalog={"artist": "Artist", "artist_ref": "dz:art", "cover_url": "https://i.scdn.co/image/x"})
            s.add_all([owned, bare])
            await s.flush()
            ids = (owned.id, bare.id)
        yield ids
    finally:
        await db.dispose()


async def kept(track_id: int) -> TrackCard | None:
    async with db.session_scope() as s:
        return await s.get(TrackCard, track_id)


async def stale(track_id: int) -> bool:
    return (await kept(track_id)).dirty


async def test_a_card_is_drawn_as_a_list_draws_the_track_and_kept(env):
    owned, bare = env
    async with db.session_scope() as s:
        read = await cards.read(s, [owned, bare])
        rows = list((await s.execute(select(Track).where(Track.id.in_([owned, bare])).order_by(Track.id))).scalars())
        drawn = {o.library_track_id: o.model_dump(mode="json", exclude=cards.NOT_CARD) for o in await cards.draw(s, rows)}
    assert read == drawn
    assert read[owned]["owned"] and not read[bare]["owned"]
    card = await kept(bare)
    assert not card.dirty and "k:artist" in card.deps and "r:dz:art" in card.deps
    # read again: the kept card, not drawn anew
    async with db.session_scope() as s:
        await s.execute(text("UPDATE track_cards SET card = card || '{\"title\": \"kept\"}' WHERE track_id = :i"), {"i": bare})
    async with db.session_scope() as s:
        assert (await cards.read(s, [bare]))[bare]["title"] == "kept"


async def test_what_a_card_is_drawn_from_marks_it_stale(env):
    owned, bare = env
    async with db.session_scope() as s:
        await cards.read(s, [owned, bare])

    async def changed(statement, *, which: int, params=None):
        async with db.session_scope() as s:
            await s.execute(text("UPDATE track_cards SET dirty = false"))
        async with db.session_scope() as s:
            await s.execute(text(statement) if isinstance(statement, str) else statement, params or {})
        return await stale(which), await stale(owned if which == bare else bare)

    # the track itself — but not what the card does not show
    assert await changed(update(Track).where(Track.id == bare).values(title="Renamed"), which=bare) == (True, False)
    assert await changed(update(Track).where(Track.id == bare).values(genres_checked_at=utcnow()), which=bare) == (False, False)
    # its file
    assert await changed(update(TrackFile).where(TrackFile.track_id == owned).values(bitrate_kbps=128), which=owned) == (True, False)
    # an artist known by one of its names now
    async with db.session_scope() as s:
        a = Artist(name="Artist")
        s.add(a)
        await s.flush()
        artist_id = a.id
    async with db.session_scope() as s:
        await s.execute(text("UPDATE track_cards SET dirty = false"))
    async with db.session_scope() as s:
        s.add(ArtistName(artist_id=artist_id, name="Artist", key="artist"))
    assert await stale(owned) and await stale(bare)
    # a search of its recording
    assert await changed(
        "INSERT INTO catalog_cache (key, payload_json, fetched_at, ttl_s) VALUES ('listen:dz:2', '{}', now(), 60)",
        which=bare) == (True, False)
    # a search that is no search of a recording
    assert await changed(
        "INSERT INTO catalog_cache (key, payload_json, fetched_at, ttl_s) VALUES ('search:x', '{}', now(), 60)",
        which=bare) == (False, False)


async def test_versions_of_a_song_mark_each_of_them(env):
    owned, bare = env
    async with db.session_scope() as s:
        await cards.read(s, [owned, bare])
        song = Song()
        s.add(song)
        await s.flush()
        song_id = song.id
        s.add(SongTrack(track_id=owned, song_id=song_id))
    async with db.session_scope() as s:
        await s.execute(text("UPDATE track_cards SET dirty = false"))
    async with db.session_scope() as s:
        s.add(SongTrack(track_id=bare, song_id=song_id))
    assert await stale(owned) and await stale(bare)


async def test_a_stale_card_is_read_drawn_anew_and_the_background_draws_the_rest(env):
    owned, bare = env
    async with db.session_scope() as s:
        await cards.read(s, [owned, bare])
        await s.execute(update(Track).where(Track.id == bare).values(title="Renamed"))
    async with db.session_scope() as s:
        assert (await cards.read(s, [bare]))[bare]["title"] == "Renamed"
    assert not await stale(bare)
    async with db.session_scope() as s:
        await s.execute(update(Track).where(Track.id == owned).values(title="Renamed too"))
        await s.execute(text("DELETE FROM track_cards WHERE track_id = :i"), {"i": bare})
    assert await cards.refresh_some() == 2
    assert (await kept(owned)).card["title"] == "Renamed too" and (await kept(bare)) is not None


async def test_a_card_marked_while_it_was_drawn_stays_marked(env, monkeypatch):
    _, bare = env
    draw = cards.draw

    async def meanwhile(s, rows, **kw):
        out = await draw(s, rows, **kw)
        async with db.session_scope() as other:  # the track changes while its card is being drawn
            await other.execute(update(Track).where(Track.id == bare).values(title="Changed meanwhile"))
        return out

    async with db.session_scope() as s:
        await cards.read(s, [bare])
        await s.execute(update(Track).where(Track.id == bare).values(title="Changed"))
    monkeypatch.setattr(cards, "draw", meanwhile)
    await cards.build([bare])
    monkeypatch.setattr(cards, "draw", draw)
    assert await stale(bare)
    async with db.session_scope() as s:
        assert (await cards.read(s, [bare]))[bare]["title"] == "Changed meanwhile"


async def test_a_card_whose_search_runs_out_is_drawn_anew(env):
    _, bare = env
    async with db.session_scope() as s:
        s.add(CatalogCache(key="listen:dz:2", payload_json={"v": {"kind": "unavailable"}}, fetched_at=utcnow(), ttl_s=3600))
    async with db.session_scope() as s:
        assert (await cards.read(s, [bare]))[bare]["unavailable"] is True
        await s.execute(text("UPDATE track_cards SET valid_until = now() - interval '1 second', dirty = false"))
        await s.execute(text("UPDATE catalog_cache SET fetched_at = fetched_at - interval '2 hours'"))
        await s.execute(text("UPDATE track_cards SET dirty = false"))
    async with db.session_scope() as s:
        assert (await cards.read(s, [bare]))[bare]["unavailable"] is False


def test_a_version_is_known_as_a_compressing_proxy_gives_it_back():
    tag = '"abc123"'
    for said in ('"abc123"', 'W/"abc123"', '"abc123-gzip"', 'W/"abc123-zstd"', '"other", W/"abc123"', "*"):
        assert cards.unchanged(said, tag), said
    for said in (None, "", '"abc12"', '"other"', 'W/"abc1234-gzip"'):
        assert not cards.unchanged(said, tag), said
