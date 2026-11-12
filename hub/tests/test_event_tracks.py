"""An event names the Hub's track it is about (`plays.attach_tracks`), and keeps it whatever becomes of the file."""

from datetime import timedelta

import pytest
from sqlalchemy import select

from app import db, stats
from app.catalog.models import CatalogTrack
from app.library import tracks as registry
from app.models import PlayEvent, TrackFile, User, utcnow
from app.plays import attach_tracks
from app.security import encrypt
from tests.support import filed_track


@pytest.fixture
async def user_id(hub_env):
    db.init_engine()
    try:
        async with db.session_scope() as s:
            user = User(nd_username="qa", nd_password_enc=encrypt("qa-password"))
            s.add(user)
            await s.flush()
            uid = user.id
        yield uid
    finally:
        await db.dispose()


def listen(user_id, n, **named):
    return PlayEvent(user_id=user_id, event_id=f"e{n}", ts=utcnow() - timedelta(minutes=10 - n), kind="finish",
                     client="navidrome", duration_s=200, track_metadata={"title": "Песня", "artist_credit": "Кто-то"},
                     **named)


async def test_a_listen_stays_with_its_track_when_the_file_is_replaced(user_id):
    async with db.session_scope() as s:
        track = filed_track(path="a.mp3", artist="Кто-то", title="Песня", fingerprint="x", nd_song_id="old")
        s.add(track)
        s.add(listen(user_id, 1, nd_song_id="old"))
        await s.flush()
        assert await attach_tracks(s, user_id) == 1
        track_id = track.id
    async with db.session_scope() as s:
        # the file is replaced: Navidrome knows the song by another id now
        (await s.execute(select(TrackFile).where(TrackFile.track_id == track_id))).scalar_one().nd_song_id = "new"
    async with db.session_scope() as s:
        assert (await s.execute(select(PlayEvent.track_id))).scalars().all() == [track_id]
        assert [p.track_id for p in await stats.plays(s, user_id)] == [track_id]
        assert await attach_tracks(s, user_id) == 0  # nothing to do twice


async def test_a_catalog_listen_becomes_the_tracks_history_once_the_hub_keeps_the_track(user_id):
    async with db.session_scope() as s:
        s.add(listen(user_id, 1, ref="dz:7"))
        await s.flush()
        assert await attach_tracks(s, user_id) == 0  # a catalog track the Hub has not kept: no track to name
        track = await registry.of_catalog(s, CatalogTrack(ref="dz:7", source="dz", id="7", title="Песня",
                                                          artist="Кто-то"), user_id=user_id)
        assert await attach_tracks(s) == 1
        assert (await s.execute(select(PlayEvent.track_id))).scalars().all() == [track.id]
