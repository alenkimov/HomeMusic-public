"""The Hub's own client of Yandex Music's API: answers read as objects, errors told apart, requests as Yandex wants."""

from __future__ import annotations

from urllib.parse import parse_qs

import httpx
import pytest
import respx

from app.catalog import ym
from app.catalog.ym import YandexError, YandexMusic, wrap

BASE = "https://api.music.yandex.net"


@pytest.fixture(autouse=True)
async def fresh_client():
    await ym.aclose()
    yield
    await ym.aclose()


def test_an_answer_is_read_by_names_in_snake_case():
    t = wrap({"id": "5", "durationMs": 200500, "albums": [{"id": 7, "trackPosition": {"volume": 1, "index": 3}}],
              "contentWarning": "explicit"})
    assert (t.id, t.duration_ms, t.content_warning, t.albums[0].track_position.index) == ("5", 200500, "explicit", 3)
    assert t.cover_uri is None and t.raw["durationMs"] == 200500  # a missing field is None, the JSON is kept


async def test_tracks_are_asked_by_their_ids_and_the_result_unwrapped():
    with respx.mock() as mock:
        route = mock.post(f"{BASE}/tracks").mock(return_value=httpx.Response(200, json={
            "invocationInfo": {}, "result": [{"id": "1", "title": "Один"}, {"id": "2", "title": "Два"}]}))
        found = await YandexMusic("tok").tracks(["1:10", 2])
    sent = route.calls[0].request
    assert parse_qs(sent.content.decode())["track-ids"] == ["1:10", "2"]
    assert sent.headers["authorization"] == "OAuth tok" and sent.headers["x-yandex-music-client"].startswith("YandexMusicAndroid")
    assert [t.title for t in found] == ["Один", "Два"]


@pytest.mark.parametrize(("status", "kind"), [(404, "not_found"), (400, "not_found"), (401, "auth"), (403, "auth"),
                                              (451, "unavailable"), (418, "bad_answer")])
async def test_the_errors_are_told_apart(status, kind):
    with respx.mock() as mock:
        mock.get(f"{BASE}/albums/1/with-tracks").mock(return_value=httpx.Response(status, json={
            "error": {"name": "x", "message": "сказал Яндекс"}}))
        with pytest.raises(YandexError) as e:
            await YandexMusic().albums_with_tracks(1)
    assert e.value.kind == kind and e.value.status == status


async def test_a_refusal_of_the_moment_is_tried_again(monkeypatch):
    import asyncio

    async def no_wait(*_):
        return None

    monkeypatch.setattr(asyncio, "sleep", no_wait)
    with respx.mock() as mock:
        route = mock.get(f"{BASE}/account/status").mock(side_effect=[
            httpx.Response(503), httpx.Response(200, json={"result": {"account": {"uid": 9, "login": "me"}}})])
        client = await YandexMusic("tok").init()
    assert route.call_count == 2 and client.account_uid == 9


async def test_a_users_likes_go_to_the_signed_in_user_and_an_artist_alone_is_a_like():
    with respx.mock() as mock:
        mock.get(f"{BASE}/account/status").mock(return_value=httpx.Response(200, json={"result": {"account": {"uid": 9}}}))
        likes = mock.get(f"{BASE}/users/9/likes/artists").mock(return_value=httpx.Response(200, json={
            "result": [{"id": "1", "name": "Кино"}, {"timestamp": "t", "artist": {"id": "2", "name": "ДДТ"}}]}))
        client = await YandexMusic("tok").init()
        rows = await client.users_likes_artists()
    assert likes.called and [r.artist.name for r in rows] == ["Кино", "ДДТ"]


async def test_the_file_of_a_track_is_asked_signed_as_the_desktop_app():
    with respx.mock() as mock:
        route = mock.get(f"{BASE}/get-file-info").mock(return_value=httpx.Response(200, json={
            "result": {"downloadInfo": {"codec": "flac", "bitrate": 0, "url": "https://strm.yandex.net/f"}}}))
        info = await YandexMusic("tok").tracks_file_info("55", "lossless", ["flac", "mp3"], "raw")
    q = route.calls[0].request.url.params
    assert (q["trackId"], q["quality"], q["codecs"], q["transports"]) == ("55", "lossless", "flac,mp3", "raw")
    assert q["sign"] and q["ts"].isdigit()
    assert route.calls[0].request.headers["x-yandex-music-client"] == ym.FILE_CLIENT
    assert info.download_info.url == "https://strm.yandex.net/f"
