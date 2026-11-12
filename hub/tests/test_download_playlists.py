"""Downloads asked for with a playlist: a file to pick by hand must not hold the ready tracks hostage, and the
order asked for is kept."""

import uuid

import pytest
from sqlalchemy import select

from app import db, playlists
from app.jobs.downloads import process_playlist_adds
from app.models import DownloadBatch, DownloadItem, PendingPlaylistAdd, Playlist, Track, User
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
            existing = filed_track(path="existing.mp3", artist="Artist", title="existing", fingerprint="existing")
            s.add(existing)
            await s.flush()
            p = await playlists.create(s, user.id, "Плейлист")
            await playlists.add(s, p, [existing.id])
            state = {"playlist": str(p.id)}
            user_id = user.id
        yield user_id, state
    finally:
        await db.dispose()


async def seed(user_id, statuses, playlist):
    async with db.session_scope() as s:
        batch = DownloadBatch(user_id=user_id, origin="assistant", target_playlist_id=playlist)
        s.add(batch)
        await s.flush()
        ids = []
        for pos, status in enumerate(statuses):
            item = DownloadItem(batch_id=batch.id, user_id=user_id, status=status,
                                query_json={"query": {"artist": "Artist", "title": f"Song {pos}"}})
            s.add(item)
            await s.flush()
            ids.append(item.id)
            s.add(PendingPlaylistAdd(playlist_id=playlist, user_id=user_id, download_item_id=item.id, position=pos))
            if status == "done":
                await finish(s, item)
        return ids


async def finish(s, item):
    track = filed_track(uid=str(uuid.uuid4()), path=f"{item.id}.mp3", artist="Artist", title=f"song{item.id}",
                        fingerprint=str(item.id), nd_song_id=f"song{item.id}")
    s.add(track)
    await s.flush()
    item.track_id, item.status = track.id, "done"


async def sync():
    async with db.session_scope() as s:
        await process_playlist_adds(s)


async def pending():
    async with db.session_scope() as s:
        return list((await s.execute(select(PendingPlaylistAdd).order_by(PendingPlaylistAdd.id))).scalars())


async def titles(state):
    """The playlist as it stands."""
    async with db.session_scope() as s:
        return [t.title for _, t in await playlists.items(s, int(state["playlist"]))]


async def test_review_releases_ready_songs_and_retry_inserts_in_requested_order(playlist_env):
    user_id, state = playlist_env
    ids = await seed(user_id, ["done", "needs_review", "done", "done"], state["playlist"])
    await sync()
    assert await titles(state) == ["existing", f"song{ids[0]}", f"song{ids[2]}", f"song{ids[3]}"]
    assert [p.download_item_id for p in await pending()] == ids[1:]

    # The marks of the places survive a restart; what the user puts in meanwhile stays where it is.
    await db.dispose()
    db.init_engine()
    async with db.session_scope() as s:
        manual = filed_track(path="manual.mp3", artist="Artist", title="manual", fingerprint="manual")
        s.add(manual)
        await s.flush()
        await playlists.add(s, await s.get(Playlist, int(state["playlist"])), [manual.id])
        await finish(s, await s.get(DownloadItem, ids[1]))
    await sync()
    assert await titles(state) == ["existing", *(f"song{i}" for i in ids), "manual"]
    assert await pending() == []
    await sync()
    assert await titles(state) == ["existing", *(f"song{i}" for i in ids), "manual"]  # nothing is added twice


async def test_active_download_still_preserves_order(playlist_env):
    user_id, state = playlist_env
    ids = await seed(user_id, ["done", "downloading", "done"], state["playlist"])
    await sync()
    assert await titles(state) == ["existing", f"song{ids[0]}"]
    async with db.session_scope() as s:
        await finish(s, await s.get(DownloadItem, ids[1]))
    await sync()
    assert await titles(state) == ["existing", *(f"song{i}" for i in ids)]
    assert await pending() == []


async def test_batches_keep_submission_order_and_a_cancelled_one_is_left_out(playlist_env):
    user_id, state = playlist_env
    first = await seed(user_id, ["needs_review", "done"], state["playlist"])
    second = await seed(user_id, ["done"], state["playlist"])
    await sync()
    assert await titles(state) == ["existing", f"song{first[1]}", f"song{second[0]}"]
    async with db.session_scope() as s:
        (await s.get(DownloadItem, first[0])).status = "cancelled"
    await sync()
    assert await titles(state) == ["existing", f"song{first[1]}", f"song{second[0]}"]
    assert await pending() == []


async def test_a_deleted_playlist_is_not_waited_for(playlist_env):
    user_id, state = playlist_env
    await seed(user_id, ["done", "downloading"], state["playlist"])
    async with db.session_scope() as s:
        await s.delete(await s.get(Playlist, int(state["playlist"])))
    await sync()
    assert await pending() == []
    async with db.session_scope() as s:
        assert (await s.execute(select(Track.id))).scalars().all()  # the tracks themselves stay


async def test_a_download_goes_into_every_playlist_asked_for(playlist_env):
    """«В плейлисты» of a catalog track ticks several at once; a track that is being downloaded already is not
    downloaded again, but the playlists asked for now get it too."""
    from app.api.downloads import create_batch
    from app.api.schemas import DownloadIn, DownloadTrackIn

    user_id, _state = playlist_env
    async with db.session_scope() as s:
        user = await s.get(User, user_id)
        out = await create_batch(s, user, DownloadIn(
            items=[DownloadTrackIn(artist="Кино", title="Пачка сигарет", catalog_ref="dz:5")],
            playlist_id="1001", playlist_ids=["1002", "1001"]), origin="hub")
        assert out.playlist_id == "1001" and out.queued == 1
    async with db.session_scope() as s:
        adds = (await s.execute(select(PendingPlaylistAdd))).scalars().all()
        assert sorted(x.playlist_id for x in adds) == ["1001", "1002"]
        assert len({x.download_item_id for x in adds}) == 1

    async with db.session_scope() as s:
        user = await s.get(User, user_id)
        out = await create_batch(s, user, DownloadIn(track_refs=["dz:5"], playlist_ids=["1002", "1003"]), origin="hub")
        assert out.queued == 1 and not out.expanding
    async with db.session_scope() as s:
        adds = (await s.execute(select(PendingPlaylistAdd))).scalars().all()
        assert sorted(x.playlist_id for x in adds) == ["1001", "1002", "1003"]
        assert len((await s.execute(select(DownloadItem))).scalars().all()) == 1
