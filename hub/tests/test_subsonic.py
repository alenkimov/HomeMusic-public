"""The Hub as a Subsonic server (`/rest`): calls go on to Navidrome as they came; a search gets the catalog's songs
after the library's, and such a song plays, shows and is kept through the Hub."""

from __future__ import annotations

import hashlib
from types import SimpleNamespace

import httpx
import pytest
import respx
from sqlalchemy import select

from tests.support import FakeDeezer, app_client

AUTH = {"u": "admin", "p": "adminpw", "v": "1.16.1", "c": "test"}
LIB_SONG = {"id": "nd1", "title": "Звезда по имени Солнце", "artist": "Кино", "isDir": False}


@pytest.fixture
async def env(hub_env):
    from app.main import create_app
    from app.services import svc

    calls: list[tuple[str, str, dict]] = []

    def navidrome(request: httpx.Request) -> httpx.Response:
        ep = request.url.path.rsplit("/", 1)[-1]
        params = dict(request.url.params) or dict(httpx.QueryParams(request.content.decode()))
        calls.append((request.method, ep, params))
        ok = {"status": "ok", "version": "1.16.1", "type": "navidrome"}
        if ep == "getUser":
            return httpx.Response(200, json={"subsonic-response": {**ok, "user": {"adminRole": True}}})
        if ep == "search3":
            return httpx.Response(200, json={"subsonic-response": {**ok, "searchResult3": {"song": [LIB_SONG]}}})
        if ep == "stream":
            return httpx.Response(206, content=b"abc", headers={"content-type": "audio/flac", "content-range": "bytes 0-2/9",
                                                                 "x-secret": "no"})
        return httpx.Response(200, json={"subsonic-response": ok})

    app = create_app()
    async with app.router.lifespan_context(app):
        svc.deezer = FakeDeezer(cover_url="https://covers.test/big.jpg")
        with respx.mock(assert_all_called=False) as mock:
            mock.route(host="navidrome.test").mock(side_effect=navidrome)
            mock.get("https://covers.test/big.jpg").mock(return_value=httpx.Response(200, content=b"jpg", headers={
                "content-type": "image/jpeg"}))
            async with app_client(app) as client:
                calls.clear()
                yield {"client": client, "calls": calls}


def ext(ref: str = "dz:100", kind: str = "s") -> str:
    from app.api.subsonic import ext_id

    return ext_id(ref, kind)


def test_ids_of_catalog_songs_are_told_from_the_library():
    from app.api.subsonic import ext_of, ext_ref

    assert ext_ref(ext("dz:100")) == "dz:100" and ext("dz:100").startswith("hms-")
    assert ext_ref("nd1") is None and ext_ref("hms-!!!") is None and ext_ref(None) is None
    # the same ref is a song, an album or an artist: the id says which
    assert ext_of(ext("dz:10", "a")) == ("a", "dz:10") and ext_of(ext("dz:1", "r")) == ("r", "dz:1")
    assert ext_ref(ext("dz:10", "a")) is None and ext_ref(ext("dz:10", "a"), "a") == "dz:10"


async def test_calls_go_on_to_navidrome_as_they_came(env):
    c = env["client"]
    r = await c.get("/rest/ping.view", params={**AUTH, "f": "json"})
    assert r.json()["subsonic-response"]["type"] == "navidrome"  # Navidrome's own answer, untouched
    assert env["calls"] == [("GET", "ping", {**AUTH, "f": "json"})]

    r = await c.get("/rest/stream", params={**AUTH, "id": "nd1"}, headers={"range": "bytes=0-2"})
    assert r.status_code == 206 and r.content == b"abc" and r.headers["content-range"] == "bytes 0-2/9"
    assert r.headers["content-type"] == "audio/flac" and "x-secret" not in r.headers

    r = await c.post("/rest/scrobble", data={**AUTH, "id": "nd1"})  # a form goes on as a form
    assert r.status_code == 200 and env["calls"][-1] == ("POST", "scrobble", {**AUTH, "id": "nd1"})


async def test_search_adds_the_catalog_after_the_library(env):
    c = env["client"]
    r = await c.get("/rest/search3", params={**AUTH, "f": "json", "query": "кино", "songCount": "5"})
    songs = r.json()["subsonic-response"]["searchResult3"]["song"]
    assert songs[0] == LIB_SONG and songs[1]["id"] == ext() and songs[1]["title"] == "Группа крови"
    assert songs[1]["coverArt"] == ext() and songs[1]["duration"] == 245 and len(songs) == 6
    assert songs[1]["albumId"] == ext("dz:10", "a")  # the song leads to its album
    albums = r.json()["subsonic-response"]["searchResult3"]["album"]
    assert albums == [{"id": ext("dz:10", "a"), "name": "Группа крови", "artist": "Кино", "coverArt": ext("dz:10", "a"),
                       "songCount": 1, "duration": 245, "year": 1988}]

    # XML is what an app gets unless it asks for JSON
    r = await c.get("/rest/search3.view", params={**AUTH, "query": "кино"})
    assert r.headers["content-type"].startswith("application/xml")
    assert f'<song id="{ext()}"' in r.text and 'title="Группа крови"' in r.text and '<song id="nd1"' in r.text

    # the Subsonic token is checked against the password the Hub keeps
    salt = "abc123"
    token = {"u": "admin", "t": hashlib.md5(("adminpw" + salt).encode()).hexdigest(), "s": salt}
    r = await c.get("/rest/search3", params={**token, "f": "json", "query": "кино"})
    assert len(r.json()["subsonic-response"]["searchResult3"]["song"]) > 1

    # an app copying the whole library (an empty query), a later page, or not the Hub's password: the library alone
    for extra in ({"query": '""'}, {"query": "кино", "songOffset": "20"}, {"query": "кино", "p": "wrong"}):
        r = await c.get("/rest/search3", params={**AUTH, "f": "json", **extra})
        assert r.json()["subsonic-response"]["searchResult3"]["song"] == [LIB_SONG], extra


async def test_a_catalog_song_shows_plays_and_is_kept(env, monkeypatch):
    from app import listen
    from app.db import session_scope
    from app.models import DownloadBatch, Job, Playlist

    c = env["client"]
    r = await c.get("/rest/getSong", params={**AUTH, "f": "json", "id": ext()})
    assert r.json()["subsonic-response"]["song"]["title"] == "Группа крови"
    r = await c.get("/rest/getCoverArt", params={**AUTH, "id": ext(), "size": "600"})
    assert r.content == b"jpg" and r.headers["content-type"] == "image/jpeg"

    asked: list = []

    async def open_media(ref, rng, **_):
        asked.append((ref, rng))

        async def body():
            yield b"mp3"

        return listen.Media(choice=SimpleNamespace(kind="preview"), body=body(), status=206, media_type="audio/mpeg",
                            headers={"content-range": "bytes 0-2/3"})

    monkeypatch.setattr(listen, "open_media", open_media)
    r = await c.get("/rest/stream.view", params={**AUTH, "id": ext()}, headers={"range": "bytes=0-"})
    assert r.status_code == 206 and r.content == b"mp3" and asked == [("dz:100", "bytes=0-")]
    assert not [x for x in env["calls"] if x[1] == "stream"]  # Navidrome was not asked for it

    # ♥: «Любимое» gets it and it is downloaded; the library's own song of the same call goes on to Navidrome
    r = await c.get("/rest/star", params=[*AUTH.items(), ("f", "json"), ("id", ext()), ("id", "nd1")])
    assert r.json()["subsonic-response"]["status"] == "ok"
    assert env["calls"][-1] == ("GET", "star", {**AUTH, "f": "json", "id": "nd1"})
    async with session_scope() as s:
        batch = (await s.execute(select(DownloadBatch))).scalar_one()
        liked = (await s.execute(select(Playlist).where(Playlist.kind == "liked"))).scalar_one()
        assert batch.target_playlist_id == str(liked.id)
        job = (await s.execute(select(Job).where(Job.kind == "expand_batch"))).scalar_one()
        assert job.payload_json["track_refs"] == ["dz:100"]

    # what a catalog song cannot have is an empty answer, not an error; the rest says what to do
    r = await c.get("/rest/getLyricsBySongId", params={**AUTH, "f": "json", "id": ext()})
    assert r.json()["subsonic-response"] == {"status": "ok", "version": "1.16.1", "type": "homemusic", "openSubsonic": True, "lyricsList": {}}
    r = await c.get("/rest/updatePlaylist", params={**AUTH, "f": "json", "playlistId": "p1", "songIdToAdd": ext()})
    assert r.json()["subsonic-response"]["error"]["code"] == 70
    r = await c.get("/rest/stream", params={**AUTH, "p": "wrong", "f": "json", "id": ext()})
    assert r.json()["subsonic-response"]["error"]["code"] == 40


async def test_a_catalog_album_opens_with_its_tracks_and_is_kept(env):
    from app.api.subsonic import artist
    from app.catalog.models import CatalogArtist
    from app.db import session_scope
    from app.models import Job

    c = env["client"]
    r = await c.get("/rest/getAlbum", params={**AUTH, "f": "json", "id": ext("dz:10", "a")})
    got = r.json()["subsonic-response"]["album"]
    assert (got["name"], got["artist"], got["songCount"]) == ("Группа крови", "Кино", 1)
    assert got["song"][0]["id"] == ext() and got["song"][0]["title"] == "Группа крови"
    r = await c.get("/rest/getCoverArt", params={**AUTH, "id": ext("dz:10", "a")})
    assert r.content == b"jpg"

    # ♥ of the album: it is downloaded whole
    r = await c.get("/rest/star", params={**AUTH, "f": "json", "albumId": ext("dz:10", "a")})
    assert r.json()["subsonic-response"]["status"] == "ok"
    async with session_scope() as s:
        job = (await s.execute(select(Job).where(Job.kind == "expand_batch"))).scalar_one()
        assert job.payload_json["album_refs"] == ["dz:10"] and job.payload_json["track_refs"] == []

    assert artist(CatalogArtist(ref="dz:1", source="dz", id="1", name="Кино", nb_albums=12)) == {
        "id": ext("dz:1", "r"), "name": "Кино", "coverArt": ext("dz:1", "r"), "albumCount": 12}
    r = await c.get("/rest/getArtistInfo2", params={**AUTH, "f": "json", "id": ext("dz:1", "r")})
    assert r.json()["subsonic-response"]["artistInfo2"] == {}


async def test_the_whole_discography_of_an_artist(env, monkeypatch):
    """`/api/catalog/artist/<ref>/discography`: every release with its tracks, newest first."""
    from datetime import date

    from app.catalog.models import ArtistPage, CatalogArtist
    from app.services import svc

    old = svc.deezer.album.model_copy(update={"ref": "dz:11", "id": "11", "title": "Раньше", "release_date": date(1986, 1, 1),
                                               "tracks": None})
    asked: list[str] = []

    async def get_artist_page(id_):
        return ArtistPage(artist=CatalogArtist(ref="dz:1", source="dz", id="1", name="Кино"),
                          albums=[old, svc.deezer.album.model_copy(update={"tracks": None})])

    async def get_album(id_):
        asked.append(str(id_))
        if str(id_) == "11":
            raise RuntimeError("the catalog does not give it")
        return svc.deezer.album

    monkeypatch.setattr(svc.deezer, "get_artist_page", get_artist_page, raising=False)
    monkeypatch.setattr(svc.deezer, "get_album", get_album)
    got = (await env["client"].get("/api/catalog/artist/dz:1/discography")).json()
    assert got["artist"]["name"] == "Кино" and sorted(asked) == ["10", "11"]
    assert [(a["title"], [t["title"] for t in a["tracks"] or []]) for a in got["albums"]] == [
        ("Группа крови", ["Группа крови"]), ("Раньше", [])]  # newest first; the one without tracks comes without them
