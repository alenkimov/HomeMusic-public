"""Reading a Yandex Music collection for an import: the snapshot, profile links, sign-in by code.

The client is a fake that answers with real (trimmed) responses of the open profile of Yandex Music's own blog
(`fixtures/yandex_import/music_blog.json`), read as the Hub's own client reads them (`app.catalog.ym.wrap`).
"""

from __future__ import annotations

import json
from pathlib import Path

import httpx
import pytest
import respx

from app.catalog.ym import wrap
from app.imports.sources import yandex
from app.imports.sources.base import AuthPending, SourceError

DATA = json.loads((Path(__file__).parent / "fixtures" / "yandex_import" / "music_blog.json").read_text("utf-8"))


class FakeYandex:
    """The client of Yandex (`app.catalog.ym`) over the fixture (no requests): its answers read as the client reads
    them. `closed` — the profile hides its library; `dislikes` — track ids."""

    def __init__(self, *, closed: bool = False, dislikes: tuple[str, ...] = ()):
        self.closed = closed
        self.dislikes = dislikes
        self.fetched: list[list[str]] = []
        self.me = None

    def _check(self):
        if self.closed:
            from app.catalog.ym import YandexError

            raise YandexError("auth", "ownerOtherwiseUserBindingError", 403)

    async def users_likes_tracks(self, user_id=None):
        self._check()
        return wrap(DATA["likes"]["library"])

    async def tracks(self, ids):
        self.fetched.append(list(ids))
        want = {str(i).split(":")[0] for i in ids}
        return [wrap(t) for t in DATA["tracks"] if str(t["id"]) in want]

    async def users_playlists_list(self, user_id=None):
        likes = {**DATA["playlists_list"][0], "kind": 3, "title": "Мне нравится"}  # listed by some accounts
        return wrap([likes, *DATA["playlists_list"]])

    async def users_playlists(self, kind, user_id=None):
        assert kind == DATA["playlist"]["kind"]
        return wrap(DATA["playlist"])

    async def users_likes_albums(self, user_id=None):
        return wrap(DATA["likes_albums"])

    async def users_likes_artists(self, user_id=None):
        return [wrap(x if "artist" in x else {"artist": x}) for x in DATA["likes_artists"]]

    async def users_dislikes_tracks(self, user_id=None):
        return wrap({"uid": 1, "revision": 1, "tracks": [
            {"id": i, "albumId": "1", "timestamp": "2026-01-01T00:00:00+00:00"} for i in self.dislikes]})


async def test_open_profile_gives_likes_playlists_albums_and_artists():
    client = FakeYandex()
    snap = await yandex.fetch_snapshot(client, "music-blog", signed_in=False)
    assert snap.account == "music-blog"
    kinds = [c.kind for c in snap.collections]
    assert kinds == ["likes", "playlist", "albums", "artists"]  # no dislikes without signing in
    likes, playlist, albums, artists = snap.collections

    # likes come as bare ids, newest first: the tracks are fetched in one go, the order and the time of the like kept
    assert client.fetched == [[f"{t['id']}:{t['albumId']}" for t in DATA["likes"]["library"]["tracks"]]]
    assert [i.external_id for i in likes.items] == ["20935722", "149972244", "24142847"]
    first = likes.items[0].raw
    assert first == {"artist": "Hospital", "artists": ["Hospital"], "title": "Falling",
                     "album": "When the Trees Were Higher", "duration_s": 216, "cover_url": first["cover_url"],
                     "liked_at": "2026-09-16T11:40:55"}
    assert first["cover_url"].startswith("https://avatars.yandex.net/") and first["cover_url"].endswith("/200x200")
    assert likes.items[1].raw["title"] == "Обнял, поцеловал"

    # «Мне нравится» listed among the playlists is not taken twice; a playlist keeps its order
    assert playlist.title == "Они взлетели в Нитро: фоновая музыка"
    assert playlist.external_id == f"{DATA['playlist']['uid']}:{DATA['playlist']['kind']}"
    assert [i.raw["title"] for i in playlist.items] == [s["track"]["title"] for s in DATA["playlist"]["tracks"]]

    assert [(i.external_id, i.raw["artist"], i.raw["title"]) for i in albums.items] == [
        (str(x["album"]["id"]), x["album"]["artists"][0]["name"], x["album"]["title"]) for x in DATA["likes_albums"]]
    assert [i.raw["name"] for i in artists.items] == ["Варя Пипко", "Cosa Nostra (since 2010)"]


async def test_signed_in_user_gets_dislikes_too():
    snap = await yandex.fetch_snapshot(FakeYandex(dislikes=("20935722",)), None, signed_in=True)
    dislikes = snap.collections[-1]
    assert dislikes.kind == "dislikes"
    assert [i.raw["title"] for i in dislikes.items] == ["Falling"]


async def test_closed_profile_says_how_to_get_in():
    with pytest.raises(SourceError, match="Медиатека закрыта"):
        await yandex.fetch_snapshot(FakeYandex(closed=True), "someone", signed_in=False)


def test_track_version_and_unavailable_tracks():
    t = wrap({**DATA["tracks"][0], "version": "Live", "available": False})
    raw = yandex.track_raw(t)
    assert raw is not None and raw["title"] == "Falling (Live)" and raw["unavailable"] is True
    assert yandex.track_raw(wrap({**DATA["tracks"][0], "type": "podcast-episode"})) is None


@pytest.mark.parametrize(("text", "login"), [
    ("https://music.yandex.ru/users/music-blog/playlists/3", "music-blog"),
    ("music.yandex.ru/users/ivan.petrov/tracks", "ivan.petrov"),
    ("https://music.yandex.com/users/yamusic-bestsongs/playlists", "yamusic-bestsongs"),
    ("  music-blog ", "music-blog"),
], ids=["likes-link", "no-scheme", "com-domain", "bare-login"])
def test_profile_login(text, login):
    assert yandex.profile_login(text) == login


@pytest.mark.parametrize("text", ["https://music.yandex.ru/album/123", "https://example.com/users/x", "", "music.yandex.ru"],
                         ids=["album", "other-site", "empty", "host-only"])
def test_profile_login_rejects_other_links(text):
    with pytest.raises(SourceError):
        yandex.profile_login(text)


@respx.mock
async def test_sign_in_by_code():
    code = respx.post("https://oauth.yandex.ru/device/code").mock(return_value=httpx.Response(200, json={
        "device_code": "dc", "user_code": "abcd1234", "verification_url": "https://ya.ru/device", "interval": 5,
        "expires_in": 300}))
    got = await yandex.device_start("homemusic-1-1")
    assert got["user_code"] == "abcd1234"
    sent = dict(httpx.QueryParams(code.calls[0].request.content.decode()))
    assert sent["client_id"] == yandex.CLIENT_ID and sent["device_id"] == "homemusic-1-1"

    token = respx.post("https://oauth.yandex.ru/token")
    token.mock(return_value=httpx.Response(400, json={"error": "authorization_pending"}))
    with pytest.raises(AuthPending):
        await yandex.device_poll("dc")
    token.mock(return_value=httpx.Response(200, json={"access_token": "tok", "expires_in": 31536000}))
    assert await yandex.device_poll("dc") == "tok"
    token.mock(return_value=httpx.Response(400, json={"error": "expired_token"}))
    with pytest.raises(SourceError, match="устарел"):
        await yandex.device_poll("dc")
