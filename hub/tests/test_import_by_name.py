"""A row of a transfer the catalog does not know is a track of the Hub, known by its name alone: it stands in its
place in the playlist and in «Любимое», and the file the user picks for it is that very track's."""

import pytest
from sqlalchemy import select

from app import db, playlists
from app.api.playlists import track_without_file
from app.favorites import project_liked
from app.imports import apply
from app.library import tracks as registry
from app.matching.normalize import fingerprint
from app.models import DownloadItem, Import, ImportCollection, ImportItem, Track, User
from app.security import encrypt
from tests.support import filed_track


@pytest.fixture
async def env(hub_env):
    db.init_engine()
    try:
        async with db.session_scope() as s:
            user = User(nd_username="qa", nd_password_enc=encrypt("qa-password"))
            s.add(user)
            await s.flush()
            imp = Import(user_id=user.id, source="yandex", method="profile", mode="full", status="running")
            s.add(imp)
            await s.flush()
            colls = {}
            for kind in ("playlist", "likes"):
                colls[kind] = ImportCollection(import_id=imp.id, kind=kind, external_id=kind, title="Карантин", selected=True)
                s.add(colls[kind])
            tracks = [filed_track(path=f"{n}.mp3", artist="A", title=n, fingerprint=fingerprint("A", n)) for n in ("a", "c")]
            s.add_all(tracks)
            await s.flush()
            a, c = (t.id for t in tracks)
            for kind, coll in colls.items():
                for position, (title, track_id, status) in enumerate([("a", a, "done"), ("b", None, "not_found"), ("c", c, "done")]):
                    s.add(ImportItem(import_id=imp.id, collection_id=coll.id, external_id=f"{kind}{position}", position=position,
                                     raw_json={"artist": "A", "title": title, "duration_s": 100,
                                               "liked_at": f"2026-01-0{3 - position}T00:00:00"},
                                     match="not_found" if track_id is None else "owned", status=status, track_id=track_id))
            await s.flush()
            ids = (user.id, imp.id, colls["playlist"].id, (a, c))
        yield ids
    finally:
        await db.dispose()


async def settle(imp_id, coll_id):
    async with db.session_scope() as s:
        await apply.sync_playlists(s, await s.get(Import, imp_id))
        return (await s.get(ImportCollection, coll_id)).playlist_id


async def test_a_row_the_catalog_does_not_know_is_a_track_in_its_place(env):
    user_id, imp_id, coll_id, (a, c) = env
    pid = await settle(imp_id, coll_id)
    async with db.session_scope() as s:
        rows = await playlists.items(s, pid)
        assert [t.title for _, t in rows] == ["a", "b", "c"]
        b = rows[1][1]
        assert (b.ref, b.path, b.artist) == (None, None, "A")
        assert track_without_file(b).unknown
        # the same song met again is the same track, and the catalog's name, when it comes, is this track's
        assert (await registry.by_name(s, artist="A", title="b", duration_s=101)).id == b.id
    assert await settle(imp_id, coll_id) == pid
    async with db.session_scope() as s:
        assert len(await playlists.items(s, pid)) == 3


async def test_the_file_picked_for_it_is_that_tracks(env):
    user_id, imp_id, coll_id, _ = env
    pid = await settle(imp_id, coll_id)
    async with db.session_scope() as s:
        b = (await playlists.items(s, pid))[1][1].id
        it = (await s.execute(select(ImportItem).where(ImportItem.collection_id == coll_id, ImportItem.track_id == b))).scalar_one()
        dl = await apply.pick(s, await s.get(Import, imp_id), it)
        assert (it.status, it.download_item_id) == ("queued", dl.id)
        assert (await s.get(DownloadItem, dl.id)).query_json["meta"] == {"track_id": b}


async def test_a_like_of_it_is_in_the_liked(env):
    user_id, imp_id, coll_id, (a, c) = env
    async with db.session_scope() as s:
        assert await project_liked(s, user_id)
        liked = (await playlists.liked(s, user_id)).id
        assert [t.title for _, t in await playlists.items(s, liked)] == ["a", "b", "c"]
    pid = await settle(imp_id, coll_id)
    async with db.session_scope() as s:  # one track, in «Любимое» and in the playlist
        assert (await playlists.items(s, liked))[1][1].id == (await playlists.items(s, pid))[1][1].id
        assert len(list((await s.execute(select(Track).where(Track.title == "b"))).scalars())) == 1
