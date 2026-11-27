"""A track's files: one main one, the others kept beside it, and the journal of what happened to them."""

from __future__ import annotations

from tests.test_flow import download_one, make_mp3
from tests.test_flow import env as env  # the fixture
from tests.test_flow import src_mp3 as src_mp3  # its file


async def _track(c) -> int:
    item = await download_one(c, {"artist": "Кино", "title": "Группа крови"})
    assert item["status"] == "done", item
    return item["track_id"]


async def _staged(tmp_path, name: str, seconds: int = 2):
    from app.config import get_settings

    folder = get_settings().staging_dir / "test"
    folder.mkdir(parents=True, exist_ok=True)
    return make_mp3(folder / name, seconds=seconds)


async def _beside(s, track_id: int, tmp_path, name: str):
    """One more file of the track (a version picked next to the old one): it comes as the main one."""
    from app.library.importer import ImportSpec, import_file
    from app.models import Track

    track = await s.get(Track, track_id)
    spec = ImportSpec(artist=track.artist, title=track.title, album=track.album, album_id=track.album_id,
                              track_id=track.id, source="ytdlp", codec="mp3", bitrate_kbps=256, force=True,
                              why="picked")
    await import_file(s, await _staged(tmp_path, name), spec)


async def test_a_replacement_takes_the_main_file_only_and_is_taken_back(env, tmp_path):
    """The track has two files; a better one in the place of the main one sends that one alone to the trash folder —
    the other stays. Taken back from the journal, the old main one plays again, the new one goes to the trash."""
    from app.db import session_scope
    from app.library import files, upgrade
    from app.library.importer import library_abs

    c = env["client"]
    track_id = await _track(c)
    async with session_scope() as s:
        first = await files.main_of(s, track_id)
        await _beside(s, track_id, tmp_path, "second.mp3")
        second = await files.main_of(s, track_id)
        assert second.id != first.id and [f.id for f in await files.of_track(s, track_id)] == [second.id, first.id]
        gone = await upgrade.put(s, track_id, await _staged(tmp_path, "better.mp3"), user_id=None, codec="flac",
                                 bitrate_kbps=900, provider="ytdlp", source_url=None)
        now = await files.of_track(s, track_id)
        # the main one went, the other is still there; the new one is the main one
        assert [f.id for f in now][1:] == [first.id] and now[0].codec == "flac" and now[0].main
        assert library_abs(first.path).is_file() and gone.file["uid"] == second.uid and files.kept(gone)

        await files.restore(s, gone, why="revert")
        await files.trash(s, now[0], why="revert")
        back = await files.main_of(s, track_id)
        assert back.uid == second.uid and library_abs(back.path).is_file()
        assert sorted(f.id for f in await files.of_track(s, track_id)) == sorted([first.id, back.id])
        kinds = [(e.kind, e.why) for e in await files.history(s, track_id)]
        assert kinds[:4] == [("trashed", "revert"), ("main", "revert"), ("restored", "revert"), ("added", "upgrade")]


async def test_the_listener_makes_another_file_main_and_brings_one_back(env, tmp_path):
    """Through the page of a track's files: another file is made the main one; one kept beside goes to the trash
    folder and comes back from it as the main one. The main one itself is not sent to the trash from there."""
    from app.db import session_scope
    from app.library import files

    c = env["client"]
    track_id = await _track(c)
    async with session_scope() as s:
        first = (await files.main_of(s, track_id)).id
        await _beside(s, track_id, tmp_path, "second.mp3")
        second = (await files.main_of(s, track_id)).id

    r = await c.post(f"/api/library/tracks/{track_id}/files/{first}/main")
    assert r.status_code == 200, r.text
    got = r.json()
    assert [(f["id"], f["main"]) for f in got["files"]] == [(first, True), (second, False)]
    assert got["events"][0]["kind"] == "main" and got["events"][0]["file_id"] == first
    assert (await c.post(f"/api/library/tracks/{track_id}/files/{first}/trash")).status_code == 409

    got = (await c.post(f"/api/library/tracks/{track_id}/files/{second}/trash")).json()
    assert [f["id"] for f in got["files"]] == [first]
    trashed = got["events"][0]
    assert trashed["kind"] == "trashed" and trashed["can_restore"] and trashed["file"]["source"] == "ytdlp"

    got = (await c.post(f"/api/library/tracks/{track_id}/files/restore/{trashed['id']}")).json()
    main = got["files"][0]
    assert main["main"] and main["source"] == "ytdlp" and len(got["files"]) == 2
    assert not next(e for e in got["events"] if e["id"] == trashed["id"])["can_restore"]  # brought back: not there any more
    assert (await c.post(f"/api/library/tracks/{track_id}/files/restore/{trashed['id']}")).status_code == 410
    async with session_scope() as s:
        from app.models import Track

        t = await s.get(Track, track_id)
        assert t.path == (await files.main_of(s, track_id)).path  # the track plays its main file


async def test_a_deleted_track_comes_back_with_its_journal(env, tmp_path):
    """A track sent to the trash and brought back keeps the journal of its files, its main file the main one."""
    from sqlalchemy import select

    from app.db import session_scope
    from app.library import files, trash
    from app.models import TrashedTrack

    c = env["client"]
    track_id = await _track(c)
    async with session_scope() as s:
        await _beside(s, track_id, tmp_path, "second.mp3")
        before = [(e.kind, e.why) for e in await files.history(s, track_id)]
    assert (await c.delete(f"/api/library/tracks/{track_id}")).status_code in (200, 204)
    async with session_scope() as s:
        row = (await s.execute(select(TrashedTrack))).scalars().one()
        back = await trash.restore(s, row)
        mains = [f for f in await files.of_track(s, back.id) if f.main]
        assert len(mains) == 1 and mains[0].source == "ytdlp"
        after = [(e.kind, e.why) for e in await files.history(s, back.id)]
        assert after[0] == ("restored", "listener") and after[1:] == before


async def test_the_like_moves_to_the_new_main_file_once_navidrome_has_it(env, tmp_path, monkeypatch):
    """The ♥ a listener has on a track moves in Navidrome from the old main file's song to the new one's — after
    the scan that gave the new file its song, not before."""
    from sqlalchemy import select

    from app import playlists
    from app.db import session_scope
    from app.library import files
    from app.models import User

    moved: list[tuple[str, list[str]]] = []

    class Nd:
        async def star(self, song_ids):
            moved.append(("star", song_ids))

        async def unstar(self, song_ids):
            moved.append(("unstar", song_ids))

        async def aclose(self):
            pass

    monkeypatch.setattr("app.auth.user_navidrome", lambda user: Nd())
    c = env["client"]
    track_id = await _track(c)
    async with session_scope() as s:
        user = (await s.execute(select(User).limit(1))).scalar_one()
        await playlists.add(s, await playlists.liked(s, user.id), [track_id])
        old = (await files.main_of(s, track_id)).nd_song_id
        await _beside(s, track_id, tmp_path, "second.mp3")
        assert await files.move_stars(s) == 0 and not moved  # Navidrome has not scanned it yet
        (await files.main_of(s, track_id)).nd_song_id = "nd-new"
        assert await files.move_stars(s) == 1
        assert moved == [("star", ["nd-new"]), ("unstar", [old])]
        assert await files.move_stars(s) == 0  # once


async def test_a_replacement_that_fails_leaves_the_old_file(env, tmp_path, monkeypatch):
    """The new file does not come in: the old main file is back where it was, its row with it."""
    import pytest

    from app.db import session_scope
    from app.library import files, importer
    from app.library.importer import library_abs

    c = env["client"]
    track_id = await _track(c)
    async with session_scope() as s:
        before = await files.main_of(s, track_id)
        uid, path = before.uid, before.path

    async def broken(*a, **k):
        raise RuntimeError("tagging failed")

    monkeypatch.setattr(importer, "import_file", broken)
    with pytest.raises(RuntimeError):
        async with session_scope() as s:
            await files.replace_main(s, track_id, await _staged(tmp_path, "better.mp3"), importer.ImportSpec(
                artist="Кино", title="Группа крови", track_id=track_id), why="upgrade", user_id=None)
    async with session_scope() as s:
        now = await files.main_of(s, track_id)
        assert (now.uid, now.path) == (uid, path) and library_abs(path).is_file()
        assert [e.kind for e in await files.history(s, track_id)] == ["added"]
