"""«Двойники»: one person under two names, with their albums and tracks — found by the clues, joined as one case
(the steps the listener leaves out stay as they are), and taken back as it was."""

from __future__ import annotations

from tests.test_flow import env as env  # the fixture
from tests.test_flow import make_mp3
from tests.test_flow import src_mp3 as src_mp3  # its file
from tests.test_tidy import _open, _scan


def test_an_alias_in_brackets_is_read():
    from app.tidy import twins

    assert twins.ALIAS.match("Алексей Хвостенко (Хвост)").groups() == ("Алексей Хвостенко", "Хвост")
    assert twins.ALIAS.match("Yegor Letov [Егор Летов]").groups() == ("Yegor Letov", "Егор Летов")
    assert twins.ALIAS.match("Хвост") is None


async def _album(title: str, artist: str, songs: list[str], lengths: list[int] | None = None,
                 cover: bytes | None = None) -> list[int]:
    """An album of the library and its tracks, each with a file that can be tagged and moved."""
    import uuid

    from app.db import session_scope
    from app.library import artists
    from app.library.importer import album_reldir, library_abs
    from app.models import Album, Track, TrackFile

    async with session_scope() as s:
        album = Album(uid=str(uuid.uuid4()), title=title, album_artist=artist)
        s.add(album)
        await s.flush()
        await artists.credit_album(s, album)
        if cover is not None:
            folder = library_abs(album_reldir(album))
            folder.mkdir(parents=True, exist_ok=True)
            (folder / "cover.jpg").write_bytes(cover)
            album.cover_path = (album_reldir(album) / "cover.jpg").as_posix()
        ids = []
        for n, song in enumerate(songs, 1):
            rel = (album_reldir(album) / f"{n:02d} - {song}.mp3").as_posix()
            library_abs(rel).parent.mkdir(parents=True, exist_ok=True)
            make_mp3(library_abs(rel), seconds=2)
            t = Track(artist=artist, title=song, album=title, album_artist=artist, album_id=album.id, track_no=n,
                      duration_s=lengths[n - 1] if lengths else None,
                      fingerprint=f"{artist}|{song}".casefold(),
                      files=[TrackFile(uid=str(uuid.uuid4()), path=rel, codec="mp3", bitrate_kbps=320, main=True)])
            s.add(t)
            await s.flush()
            await artists.credit_track(s, t)
            ids.append(t.id)
        return ids


async def _library(monkeypatch) -> dict[str, int]:
    from sqlalchemy import select

    from app.db import session_scope
    from app.library import acoustic
    from app.models import Artist, Track

    orl, steamer, other = await _album("Орландина", "Хвост, Аукцыон", ["Орландина", "Пароход", "Ночь"])
    orl2, fish = await _album("Орландина", "Алексей Хвостенко (Хвост), Аукцыон", ["Орландина", "Рыба"])

    async def doubles(s=None, *, budget=None):
        return [[orl, orl2]]

    monkeypatch.setattr(acoustic, "doubles", doubles)
    async with session_scope() as s:
        names = {a.name: a.id for a in (await s.execute(select(Artist))).scalars()}
        album = {t.id: t.album_id for t in (await s.execute(select(Track))).scalars()}
    return {"orl": orl, "steamer": steamer, "orl2": orl2, "fish": fish, "into": names["Хвост"],
            "other": names["Алексей Хвостенко (Хвост)"], "album": album[orl], "album2": album[orl2]}


async def test_a_twin_is_found_joined_with_its_music_and_parted_again(env, monkeypatch):
    from app.db import session_scope
    from app.library.importer import library_abs
    from app.models import Album, Artist, Track

    c = env["client"]
    x = await _library(monkeypatch)
    await _scan()
    (p,) = await _open(c, "twins")
    assert (p["kind"], p["subject_id"], p["show"]["into_id"], p["sure"]) == ("twin", x["other"], x["into"], True)
    assert [k["kind"] for k in p["show"]["clues"]] == ["alias", "sound", "album"]
    case = p["show"]["case"]
    assert [(a["source"]["id"], a["target"]["id"]) for a in case["albums"]] == [(x["album2"], x["album"])]
    assert [(t["gone"]["id"], t["keep"]["id"]) for t in case["tracks"]] == [(x["orl2"], x["orl"])]
    assert not await _open(c, "artists")  # the plain question of the two is the case's

    r = await c.post(f"/api/tidy/proposals/{p['id']}/accept", json={"skip": []})
    assert r.status_code == 200, r.text
    async with session_scope() as s:
        assert (await s.get(Artist, x["other"])).merged_into_id == x["into"]
        assert await s.get(Album, x["album2"]) is None and await s.get(Track, x["orl2"]) is None
        assert (await s.get(Track, x["fish"])).album_id == x["album"]  # its album is the one of that name
    got = (await c.get(f"/api/library/tracks/{x['orl']}/files")).json()
    assert got["events"][0]["why"] == "double" and got["events"][0]["can_restore"]

    r = await c.post(f"/api/tidy/proposals/{p['id']}/revert")
    assert r.status_code == 200, r.text
    async with session_scope() as s:
        assert (await s.get(Artist, x["other"])).merged_into_id is None
        album2 = await s.get(Album, x["album2"])
        assert album2 is not None and album2.album_artist == "Алексей Хвостенко (Хвост), Аукцыон"
        fish = await s.get(Track, x["fish"])
        assert fish.album_id == x["album2"]
        back = [t for t in (await s.execute(Track.__table__.select().where(Track.album_id == x["album2"]))).all()]
        assert sorted(t.title for t in back) == ["Орландина", "Рыба"]
    assert library_abs((await _file_of(fish.id))).is_file()


async def _file_of(track_id: int) -> str:
    from app.db import session_scope
    from app.library.files import main_of

    async with session_scope() as s:
        return (await main_of(s, track_id)).path


async def test_a_step_left_out_stays_as_it_is(env, monkeypatch):
    """The listener unticks the recording: the artists and albums are one, the two tracks stay two."""
    from app.db import session_scope
    from app.models import Album, Artist, Track

    c = env["client"]
    x = await _library(monkeypatch)
    await _scan()
    (p,) = await _open(c, "twins")
    r = await c.post(f"/api/tidy/proposals/{p['id']}/accept", json={"skip": [f"track:{x['orl2']}"]})
    assert r.status_code == 200, r.text
    async with session_scope() as s:
        assert (await s.get(Artist, x["other"])).merged_into_id == x["into"]
        assert await s.get(Album, x["album2"]) is None
        kept = await s.get(Track, x["orl2"])
        assert kept is not None and kept.album_id == x["album"]


async def test_two_people_are_not_asked_about_again(env, monkeypatch):
    from app.db import session_scope
    from app.models import ArtistApart

    c = env["client"]
    x = await _library(monkeypatch)
    await _scan()
    (p,) = await _open(c, "twins")
    assert (await c.post(f"/api/tidy/proposals/{p['id']}/reject")).status_code == 200
    async with session_scope() as s:
        low, high = sorted((x["into"], x["other"]))
        assert await s.get(ArtistApart, (low, high)) is not None
    await _scan()
    assert not await _open(c, "twins") and not await _open(c, "artists")


def _jpeg(color: tuple[int, int, int], quality: int) -> bytes:
    import io

    from PIL import Image

    out = io.BytesIO()
    image = Image.new("RGB", (300, 300), color)
    image.paste((255, 255, 255), (40, 40, 160, 160))
    image.save(out, "JPEG", quality=quality)
    return out.getvalue()


async def test_an_album_named_in_another_language_is_the_same_by_its_tracks_and_cover(env, monkeypatch):
    """One release under both names, each catalog naming it its own way: as many tracks, each as long as the one
    in its place, under one cover (compressed otherwise) — it is the same album; another cover, and it is not."""
    from app.library import acoustic

    async def doubles(s=None, *, budget=None):
        return []

    monkeypatch.setattr(acoustic, "doubles", doubles)
    c = env["client"]
    await _album("DecoMiku LIVE starring Hatsune Miku", "DECO*27", ["Reunion", "Android Girl", "Ghost Rule"],
                 [77, 210, 209], _jpeg((40, 160, 220), 90))
    await _album("Hello", "DECO*27", ["Hello", "Vampire", "Rabbit Hole"], [180, 190, 200])  # more music: the one who stays
    await _album("デコミク LIVE starring 初音ミク『Hello』", "デコニーナ (DECO*27)",
                         ["Reunion", "アンドロイドガール", "ゴーストルール"], [78, 211, 210], _jpeg((40, 160, 220), 60))
    await _album("Another", "デコニーナ (DECO*27)", ["Other", "More"], [100, 120], _jpeg((200, 30, 30), 90))  # not that one
    await _scan()
    (p,) = await _open(c, "twins")
    assert [k["kind"] for k in p["show"]["clues"]] == ["alias", "album"]
    assert "те же треки по длительности и обложка" in p["show"]["clues"][1]["text"]
    (pair,) = p["show"]["case"]["albums"]
    assert (pair["source"]["title"], pair["target"]["title"]) == ("デコミク LIVE starring 初音ミク『Hello』", "DecoMiku LIVE starring Hatsune Miku")
