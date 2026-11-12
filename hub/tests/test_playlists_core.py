"""The Hub's own playlists (app/playlists.py) and the registry that makes a track of a catalog track."""

from __future__ import annotations

from datetime import datetime

import pytest

from app.catalog.models import CatalogTrack
from tests.support import filed_track


@pytest.fixture
async def s(hub_env):
    from app import db
    from app.models import User
    from app.security import encrypt

    db.init_engine()
    try:
        async with db.session_scope() as session:
            session.add(User(nd_username="u", nd_password_enc=encrypt("x")))
            await session.flush()
            yield session
    finally:
        await db.dispose()


def cat(n: int, artist: str = "Кино", title: str = "", **kw) -> CatalogTrack:
    return CatalogTrack(ref=f"dz:{n}", source="dz", id=str(n), title=title or f"Песня {n}", artist=artist,
                        duration_s=200, cover_url=f"https://cover/{n}.jpg", **kw)


async def test_a_catalog_track_becomes_a_track_once(s):
    from app.library import tracks

    a = await tracks.of_catalog(s, cat(1), user_id=1)
    again = await tracks.of_catalog(s, cat(1))
    assert a.id == again.id and a.ref == "dz:1" and a.catalog["cover_url"] == "https://cover/1.jpg"
    assert not await tracks.has_file(s, a.id) and a.nd_song_id is None


async def test_an_uploaded_file_of_the_same_song_takes_the_catalog_name(s):
    from app.library import tracks
    from app.matching.normalize import fingerprint

    own = filed_track(path="a.mp3", nd_song_id="nd1", artist="Кино", title="Группа крови", duration_s=201,
                      fingerprint=fingerprint("Кино", "Группа крови"))
    other = filed_track(path="b.mp3", artist="Кино", title="Группа крови", duration_s=400,
                        fingerprint=fingerprint("Кино", "Группа крови"))  # another recording: much longer
    s.add_all([own, other])
    await s.flush()

    t = await tracks.of_catalog(s, cat(7, title="Группа крови"))
    assert t.id == own.id and t.ref == "dz:7" and await tracks.has_file(s, t.id)


async def test_a_playlist_keeps_order_dates_and_tracks_without_files(s):
    from app import playlists
    from app.library import tracks

    filed = filed_track(path="x.mp3", nd_song_id="ndX", artist="Свой", title="Файл", duration_s=100, fingerprint="свой|файл")
    s.add(filed)
    await s.flush()
    a, b, c = [(await tracks.of_catalog(s, cat(n))).id for n in (1, 2, 3)]

    p = await playlists.create(s, 1, "  Вечер  ")
    assert p.name == "Вечер"
    old = datetime(2024, 9, 8, 12, 0)
    assert await playlists.add(s, p, [a, filed.id, b], at=old) == [a, filed.id, b]
    assert await playlists.add(s, p, [b, c]) == [c]  # one that is there already stays where it is
    rows = await playlists.items(s, p.id)
    assert [t.id for _, t in rows] == [a, filed.id, b, c]
    assert rows[0][0].added_at == old and rows[3][0].added_at > old
    assert await playlists.counts(s, [p.id]) == {p.id: (4, 1, 700)}  # four tracks, one with a file

    await playlists.reorder(s, p, [rows[3][0].id, rows[0][0].id])
    assert await playlists.track_ids(s, p.id) == [c, a, filed.id, b]
    assert await playlists.remove_tracks(s, p, [filed.id]) == 1
    assert await playlists.track_ids(s, p.id) == [c, a, b]


async def test_liked_is_a_playlist_and_a_like_goes_on_top(s):
    from app import playlists
    from app.library import tracks

    a, b, c = [(await tracks.of_catalog(s, cat(n))).id for n in (1, 2, 3)]
    liked = await playlists.liked(s, 1)
    assert (await playlists.liked(s, 1)).id == liked.id and liked.kind == "liked"
    await playlists.add(s, liked, [a], first=True)
    await playlists.add(s, liked, [b], first=True)
    assert await playlists.track_ids(s, liked.id) == [b, a]

    evening = await playlists.create(s, 1, "Вечер")
    await playlists.add(s, evening, [a, c])
    assert await playlists.kept(s, 1) == ({a, b}, {a, c})
    assert await playlists.having(s, 1, [a, c]) == {liked.id: {a}, evening.id: {a, c}}
    assert [p.id for p in await playlists.of_user(s, 1)] == [evening.id]  # «Любимое» is not in the list of playlists
