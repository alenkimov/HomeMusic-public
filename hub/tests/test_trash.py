"""«Корзина»: a deleted track comes back as it was; a rename does not move its file; a list of «Для вас» knows it
was opened."""

from __future__ import annotations

from datetime import date

from tests.support import filed_track
from tests.test_plays import env as env  # the fixture


async def test_a_deleted_track_comes_back_with_its_places(env):
    from sqlalchemy import select

    from app import playlists as lists
    from app.db import session_scope
    from app.library.importer import library_abs
    from app.models import Album, PlaylistItem, Track, TrackFile, TrashedTrack, User

    c = env["client"]
    async with session_scope() as s:
        uid = (await s.execute(select(User.id))).scalar_one()
        album = Album(uid="a-1", title="Альбом", album_artist="Кино", created_by=uid)
        s.add(album)
        await s.flush()
        t = filed_track(uid="f-1", path="Кино/Альбом/1-01 - Песня.mp3", nd_song_id="nd1", artist="Кино", title="Песня",
                        fingerprint="кино|песня", album_id=album.id, added_by=uid, ref="dz:9", duration_s=200)
        s.add(t)
        await s.flush()
        tid, aid = t.id, album.id
        mine = await lists.create(s, uid, "Мой")
        await lists.add(s, mine, [tid])
        await lists.add(s, await lists.liked(s, uid), [tid])
    file = library_abs("Кино/Альбом/1-01 - Песня.mp3")
    file.parent.mkdir(parents=True, exist_ok=True)
    file.write_bytes(b"not really music")

    assert (await c.get("/api/library/trash")).json() == []
    assert (await c.delete(f"/api/library/tracks/{tid}")).status_code == 200
    assert not file.exists()
    async with session_scope() as s:
        assert await s.get(Track, tid) is None and await s.get(Album, aid) is None  # its last track: the album too
        assert list((await s.execute(select(PlaylistItem.id))).scalars()) == []
    got = (await c.get("/api/library/trash")).json()
    assert [(x["title"], x["artist"], x["playlists"], x["days_left"]) for x in got] == [("Песня", "Кино", 2, 30)]

    r = await c.post(f"/api/library/trash/{got[0]['id']}/restore")
    assert r.status_code == 200 and r.json()["track_id"] == tid  # under the id it had
    assert file.read_bytes() == b"not really music"
    async with session_scope() as s:
        t = await s.get(Track, tid)
        a = await s.get(Album, aid)
        assert (t.title, t.ref, t.album_id, a.title) == ("Песня", "dz:9", aid, "Альбом")
        f = (await s.execute(select(TrackFile).where(TrackFile.track_id == tid))).scalar_one()
        assert (f.path, f.uid, f.nd_song_id) == ("Кино/Альбом/1-01 - Песня.mp3", "f-1", None)  # Navidrome meets it anew
        assert len(list((await s.execute(select(PlaylistItem).where(PlaylistItem.track_id == tid))).scalars())) == 2
        assert list((await s.execute(select(TrashedTrack.id))).scalars()) == []
    assert (await c.post(f"/api/library/trash/{got[0]['id']}/restore")).status_code == 404

    # a file stirred out of the trash: nothing to bring back
    assert (await c.delete(f"/api/library/tracks/{tid}")).status_code == 200
    row = (await c.get("/api/library/trash")).json()[0]
    async with session_scope() as s:
        from pathlib import Path

        gone = (await s.get(TrashedTrack, row["id"])).data_json["files"][0]["trash_path"]
        Path(gone).unlink()
    assert (await c.post(f"/api/library/trash/{row['id']}/restore")).status_code == 410


async def test_renaming_a_track_leaves_its_file_where_it_is(env):
    """Navidrome meets a moved file as a new song before it learns it is the old one: the library showed the track
    twice for a while. A rename changes the name, not the file's place."""
    from sqlalchemy import select

    from app.db import session_scope
    from app.models import Album, TrackFile, User

    c = env["client"]
    async with session_scope() as s:
        uid = (await s.execute(select(User.id))).scalar_one()
        album = Album(uid="a-2", title="Альбом", album_artist="Марк Бернес", created_by=uid)
        s.add(album)
        await s.flush()
        t = filed_track(uid="f-2", path="Марк Бернес/Альбом/1-07 - Тёмная ночь (2026 Remaster).mp3", nd_song_id="nd7",
                        artist="Марк Бернес", title="Тёмная ночь (2026 Remaster)", fingerprint="x", album_id=album.id,
                        added_by=uid)
        s.add(t)
        await s.flush()
        tid = t.id
    r = await c.patch(f"/api/library/tracks/{tid}", json={"title": "Тёмная ночь"})
    assert r.status_code == 200 and r.json()["title"] == "Тёмная ночь"
    async with session_scope() as s:
        f = (await s.execute(select(TrackFile).where(TrackFile.track_id == tid))).scalar_one()
        assert (f.path, f.nd_song_id) == ("Марк Бернес/Альбом/1-07 - Тёмная ночь (2026 Remaster).mp3", "nd7")
    history = (await c.get(f"/api/library/tracks/{tid}/edits")).json()
    assert [h["changes"] for h in history] == [{"title": ["Тёмная ночь (2026 Remaster)", "Тёмная ночь"]}]


async def test_a_list_for_you_is_new_until_it_is_opened(env):
    from sqlalchemy import select

    from app.db import session_scope
    from app.models import DiscoveryIssue, User

    c = env["client"]

    async def fresh() -> dict[str, bool]:
        return {x["kind"]: x["fresh"] for x in (await c.get("/api/for-you")).json()}

    assert not any((await fresh()).values())  # nothing was made yet
    async with session_scope() as s:
        uid = (await s.execute(select(User.id))).scalar_one()
        s.add(DiscoveryIssue(user_id=uid, week=date(2026, 11, 9), tracks_json=[]))
    assert (await fresh())["weekly"] is True
    assert (await c.post("/api/for-you/weekly/seen")).status_code == 200
    assert (await fresh())["weekly"] is False
    async with session_scope() as s:
        s.add(DiscoveryIssue(user_id=uid, week=date(2026, 11, 16), tracks_json=[]))
    assert (await fresh())["weekly"] is True  # the next week's
    assert (await c.post("/api/for-you/top-month/seen")).status_code == 404
