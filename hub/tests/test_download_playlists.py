"""Downloads asked for with a playlist: a track known before any file is in the playlist at once, in the order asked
for; a provider's file that is no track yet joins it when it is imported."""

import pytest
from sqlalchemy import select

from app import db, playlists
from app.api.downloads import create_batch
from app.api.schemas import DownloadIn, DownloadTrackIn
from app.jobs.downloads import update_item
from app.matching.normalize import fingerprint
from app.models import DownloadItem, Playlist, Track, User
from app.security import encrypt
from tests.support import filed_track


@pytest.fixture
async def playlist_env(hub_env):
    db.init_engine()
    try:
        async with db.session_scope() as s:
            user = User(nd_username="qa", nd_password_enc=encrypt("qa-password"))
            s.add(user)
            await s.flush()
            existing = filed_track(path="existing.mp3", artist="Artist", title="existing",
                                   fingerprint=fingerprint("Artist", "existing"))
            s.add(existing)
            await s.flush()
            ids = []
            for name in ("Плейлист", "Второй", "Третий"):
                p = await playlists.create(s, user.id, name)
                ids.append(str(p.id))
            await playlists.add(s, await s.get(Playlist, int(ids[0])), [existing.id])
            user_id = user.id
        yield user_id, ids
    finally:
        await db.dispose()


async def ask(user_id, **body):
    async with db.session_scope() as s:
        return await create_batch(s, await s.get(User, user_id), DownloadIn(**body), origin="hub")


async def rows(playlist):
    """The playlist as it stands: (title, has a file)."""
    async with db.session_scope() as s:
        return [(t.title, t.path is not None) for _, t in await playlists.items(s, int(playlist))]


async def test_catalog_tracks_are_in_the_playlist_at_once_in_the_order_asked_for(playlist_env):
    user_id, (first, second, _) = playlist_env
    out = await ask(user_id, playlist_id=first, playlist_ids=[second, first], items=[
        DownloadTrackIn(artist="Кино", title="Пачка сигарет", catalog_ref="dz:5", duration_s=268),
        DownloadTrackIn(artist="Кино", title="Группа крови", catalog_ref="dz:6")])
    assert (out.playlist_id, out.queued) == (first, 2)
    assert await rows(first) == [("existing", True), ("Пачка сигарет", False), ("Группа крови", False)]
    assert await rows(second) == [("Пачка сигарет", False), ("Группа крови", False)]
    async with db.session_scope() as s:
        track = (await s.execute(select(Track).where(Track.ref == "dz:5"))).scalar_one()
        assert (track.artist, track.duration_s, track.catalog["ref"]) == ("Кино", 268, "dz:5")
        assert len((await s.execute(select(DownloadItem))).scalars().all()) == 2

    # asked for again: no second track, no second place in the playlist
    await ask(user_id, playlist_id=first, items=[DownloadTrackIn(artist="Кино", title="Пачка сигарет", catalog_ref="dz:5")])
    assert await rows(first) == [("existing", True), ("Пачка сигарет", False), ("Группа крови", False)]
    async with db.session_scope() as s:
        assert len((await s.execute(select(Track))).scalars().all()) == 3


async def test_a_song_the_library_has_goes_in_as_itself(playlist_env):
    user_id, (_, second, _) = playlist_env
    out = await ask(user_id, playlist_id=second, items=[DownloadTrackIn(artist="Artist", title="existing", catalog_ref="dz:9")])
    assert out.already_owned == 1
    assert await rows(second) == [("existing", True)]
    async with db.session_scope() as s:
        assert len((await s.execute(select(Track))).scalars().all()) == 1


async def test_a_file_asked_for_by_no_catalog_name_joins_the_playlist_when_it_is_a_track(playlist_env):
    user_id, (first, _, _) = playlist_env
    await ask(user_id, playlist_id=first, items=[DownloadTrackIn(artist="Неизвестный", title="Демо")])
    assert await rows(first) == [("existing", True)]
    async with db.session_scope() as s:
        item = (await s.execute(select(DownloadItem))).scalar_one()
        assert item.query_json["playlists"] == [first]
        track = filed_track(path="demo.mp3", artist="Неизвестный", title="Демо", fingerprint="demo")
        s.add(track)
        await s.flush()
        item_id, track_id = item.id, track.id
    await update_item(item_id, track_id=track_id, status="importing")
    assert await rows(first) == [("existing", True), ("Демо", True)]
    await update_item(item_id, track_id=track_id, status="done")
    assert await rows(first) == [("existing", True), ("Демо", True)]  # once


async def test_a_track_being_downloaded_is_not_downloaded_again_but_joins_the_playlists(playlist_env):
    user_id, (first, second, third) = playlist_env
    await ask(user_id, playlist_id=first, items=[DownloadTrackIn(artist="Кино", title="Пачка сигарет", catalog_ref="dz:5")])
    out = await ask(user_id, track_refs=["dz:5"], playlist_ids=[second, third])
    assert out.queued == 1 and not out.expanding
    assert await rows(second) == await rows(third) == [("Пачка сигарет", False)]
    async with db.session_scope() as s:
        assert len((await s.execute(select(DownloadItem))).scalars().all()) == 1


async def test_a_deleted_playlist_is_passed_by(playlist_env):
    user_id, (first, second, _) = playlist_env
    async with db.session_scope() as s:
        await s.delete(await s.get(Playlist, int(second)))
    out = await ask(user_id, playlist_ids=[second, first], items=[
        DownloadTrackIn(artist="Кино", title="Пачка сигарет", catalog_ref="dz:5")])
    assert out.queued == 1
    assert await rows(first) == [("existing", True), ("Пачка сигарет", False)]
