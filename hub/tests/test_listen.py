"""Listening before downloading: GET /api/listen picks the file a download would pick and streams it with Range."""

from __future__ import annotations

import asyncio
from datetime import date
from urllib.parse import quote

import httpx
import pytest
import respx

from tests.support import FakeDeezer, FakeProvider, app_client, cand, filed_track

AUDIO = bytes(range(256)) * 1024  # 256 KiB standing in for an MP3


async def test_reset_waits_for_cancelled_resolution_cleanup(monkeypatch):
    from app import listen

    started, cleaning, release = asyncio.Event(), asyncio.Event(), asyncio.Event()

    async def resolve(*args, **kwargs):
        started.set()
        try:
            await asyncio.Event().wait()
        finally:
            cleaning.set()
            await release.wait()

    monkeypatch.setattr(listen, "_resolve", resolve)
    caller = asyncio.create_task(listen.choose("dz:cleanup"))
    await asyncio.wait_for(started.wait(), 1)
    reset = asyncio.create_task(listen.reset())
    await asyncio.wait_for(cleaning.wait(), 1)
    assert not reset.done()
    release.set()
    await asyncio.wait_for(reset, 1)
    with pytest.raises(asyncio.CancelledError):
        await caller
    assert not listen._inflight


def ranged(request: httpx.Request, body: bytes = AUDIO) -> httpx.Response:
    """A CDN that honours `Range: bytes=a-b`."""
    rng = request.headers.get("range")
    if not rng:
        return httpx.Response(200, content=body, headers={"content-type": "audio/mpeg"})
    a, _, b = rng.removeprefix("bytes=").partition("-")
    start, end = int(a), min(int(b) if b else len(body) - 1, len(body) - 1)
    return httpx.Response(206, content=body[start : end + 1], headers={
        "content-type": "audio/mpeg", "content-range": f"bytes {start}-{end}/{len(body)}"})


class Partway(httpx.AsyncByteStream):
    """A CDN body that stops after `at` bytes: the connection drops (`drop`), or the rest waits for `gate`."""

    def __init__(self, body: bytes, at: int, *, drop: bool = False, gate: asyncio.Event | None = None):
        self.body, self.at, self.drop, self.gate = body, at, drop, gate

    async def __aiter__(self):
        yield self.body[: self.at]
        if self.drop:
            raise httpx.RemoteProtocolError("peer closed connection without sending complete message body")
        if self.gate is not None:
            await self.gate.wait()
        yield self.body[self.at :]


def partway(request: httpx.Request, at: int, **kw) -> httpx.Response:
    """`ranged` whose body stops partway (`Partway`)."""
    r = ranged(request)
    return httpx.Response(r.status_code, headers=r.headers, stream=Partway(r.content, at, **kw))


@pytest.fixture
async def env(hub_env):
    from app import config
    from app.main import create_app
    from app.services import svc

    music = hub_env

    def nd_handler(request: httpx.Request) -> httpx.Response:
        ok = {"status": "ok", "version": "1.16.1"}
        if request.url.path.endswith("getUser"):
            return httpx.Response(200, json={"subsonic-response": {**ok, "user": {"adminRole": True}}})
        return httpx.Response(200, json={"subsonic-response": ok})

    app = create_app()
    async with app.router.lifespan_context(app), httpx.AsyncClient() as upstream:
        with respx.mock(assert_all_called=False) as mock:
            mock.route(host="navidrome.test").mock(side_effect=nd_handler)
            mock.route(host="cdn.test", path="/good.mp3").mock(side_effect=ranged)
            mock.route(host="cdn.test", path="/dead.mp3").mock(return_value=httpx.Response(404))
            mock.route(host="cdn.test", path="/page.mp3").mock(
                return_value=httpx.Response(200, text="<html>", headers={"content-type": "text/html"}))
            mock.route(host="preview.test").mock(side_effect=ranged)
            hm = FakeProvider("hitmoz", [], client=upstream)
            fm = FakeProvider("hitmos_fm", [], client=upstream)
            svc.providers = {"hitmoz": hm, "hitmos_fm": fm}
            svc.deezer = FakeDeezer()
            config.get_settings().cfg.sources.ru = ["hitmoz", "hitmos_fm"]
            async with app_client(app) as client:
                yield {"client": client, "hm": hm, "fm": fm, "mock": mock, "music": music, "app": app}


async def test_listen_picks_the_ranked_file_and_streams_ranges(env):
    c, hm = env["client"], env["hm"]
    hm.cands = [
        cand("9", "https://cdn.test/dead.mp3", title="Группа крови (караоке)"),  # rejected by ranking
        cand("2", "https://cdn.test/dead.mp3", bitrate=128),
        cand("1", "https://cdn.test/good.mp3"),  # 320 kbps ranks first
    ]
    r = await c.get("/api/listen", params={"ref": "dz:100"})
    assert r.status_code == 200, r.text
    out = r.json()
    assert out["kind"] == "full" and out["provider"] == "hitmoz" and out["page_url"] == "https://hitmoz.test/song/1"
    # a file still at its source says where it is and its bitrate; what it is is known once it is the library's
    assert (out["source"], out["bitrate_kbps"], out["codec"], out["sounds_like_kbps"]) == ("hitmoz", 320, None, None)
    assert out["title"] == "Группа крови" and out["duration_s"] == 245
    assert out["stream_url"] == "/api/listen/stream?ref=dz%3A100"

    r = await c.get(out["stream_url"], headers={"Range": "bytes=100-199"})
    assert r.status_code == 206
    assert r.headers["content-range"] == f"bytes 100-199/{len(AUDIO)}"
    assert r.headers["content-type"].startswith("audio/mpeg") and r.headers["accept-ranges"] == "bytes"
    assert r.content == AUDIO[100:200]

    r = await c.get(out["stream_url"])
    assert r.status_code == 200 and r.content == AUDIO

    # listening has to start fast: a short search and no track pages (a download asks for 15 and 3)
    assert hm.limits == [4]

    # the choice is cached; once heard, its track page is checked, and a download of this ref tries it first
    await c.get("/api/listen", params={"ref": "dz:100"})
    assert hm.searched == 1
    from app.listen import listened_candidate

    heard = await listened_candidate("dz:100")
    assert heard is not None and heard.provider_id == "1" and heard.enriched
    assert hm.enriched == ["1"]
    assert await listened_candidate("dz:101") is None


async def test_slow_listen_releases_auth_connection_before_resolving_and_allows_likes(env, monkeypatch):
    from app import listen
    from app.db import engine
    from app.listen import Choice

    entered, release = asyncio.Event(), asyncio.Event()

    async def choose(ref, *, background=False):
        entered.set()
        await release.wait()
        return Choice(ref=ref, kind="full", artist="QA", title="Song", duration_s=214)

    monkeypatch.setattr(listen, "choose", choose)
    pending = asyncio.create_task(env["client"].get("/api/listen", params={"ref": "dz:100"}))
    try:
        await asyncio.wait_for(entered.wait(), 1)
        # Fifteen such requests used to consume the entire pool while their own resolvers
        # needed another connection, blocking star/starred and authentication as well.
        assert engine().pool.checkedout() == 0
        response = await env["client"].post("/api/star", json={"song_ids": ["qa-song"]})
        assert response.status_code == 200, response.text
    finally:
        release.set()
        response = await asyncio.wait_for(pending, 1)
    assert response.status_code == 200, response.text


async def test_file_that_does_not_open_moves_to_the_next(env):
    c, hm, fm = env["client"], env["hm"], env["fm"]
    hm.cands = [cand("1", "https://cdn.test/dead.mp3"), cand("2", "https://cdn.test/page.mp3")]
    fm.cands = [cand("7", "https://cdn.test/good.mp3", provider="hitmos_fm")]
    # a tap doesn't open the file first: the stream does, so the pick names the first source that has the track
    r = await c.get("/api/listen", params={"ref": "dz:100"})
    assert r.status_code == 200, r.text
    assert r.json()["provider"] == "hitmoz"
    # none of its files opens: take the saved source without repeating the catalog search
    r = await c.get(r.json()["stream_url"], headers={"Range": "bytes=0-9"})
    assert r.status_code == 206 and r.content == AUDIO[:10]
    r = await c.get("/api/listen", params={"ref": "dz:100"})
    assert (r.json()["provider"], r.json()["page_url"]) == ("hitmos_fm", "https://hitmos_fm.test/song/7")
    assert hm.searched == fm.searched == 1


async def test_a_source_that_returns_no_audio_uses_saved_recording_after_restart(env):
    from app import listen

    c, hm, fm = env["client"], env["hm"], env["fm"]
    hm.cands = [cand("1", "https://cdn.test/empty.mp3")]
    fm.cands = [cand("7", "https://cdn.test/good.mp3", provider="hitmos_fm")]
    env["mock"].route(host="cdn.test", path="/empty.mp3").mock(
        return_value=httpx.Response(200, content=b"", headers={"content-type": "audio/mpeg"}))
    result = (await c.get("/api/listen", params={"ref": "dz:100"})).json()
    assert result["provider"] == "hitmoz"
    if task := listen._source_lookups.get("dz:100"):
        await task
    await listen.reset()
    hm.cands = fm.cands = []
    r = await c.get(result["stream_url"], headers={"Range": "bytes=0-9"})
    assert r.status_code == 206 and r.content == AUDIO[:10]
    assert r.headers["x-homemusic-source"] == "hitmos_fm"
    assert hm.searched == fm.searched == 1


def test_saved_sources_require_the_same_edition_and_length():
    from app.listen import _same_recording
    from app.providers.base import TrackQuery

    q = TrackQuery(artist="Кино", title="Группа крови", duration_s=245)
    candidate = cand("7", "https://cdn.test/good.mp3", provider="hitmos_fm")
    assert _same_recording(q, candidate)
    assert not _same_recording(q, candidate.model_copy(update={"title": "Группа крови (Live)"}))
    assert not _same_recording(q, candidate.model_copy(update={"duration_s": 240}))


async def test_all_sources_are_searched_at_once(env):
    c, hm, fm = env["client"], env["hm"], env["fm"]
    fm.cands = [cand("7", "https://cdn.test/good.mp3", provider="hitmos_fm")]
    r = await c.get("/api/listen", params={"ref": "dz:100"})
    assert r.json()["provider"] == "hitmos_fm"
    assert hm.searched == 1 and fm.searched == 1


async def test_pick_is_remembered_across_restarts(env):
    from app import listen

    c, hm = env["client"], env["hm"]
    hm.cands = [cand("1", "https://cdn.test/good.mp3")]
    url = (await c.get("/api/listen", params={"ref": "dz:100"})).json()["stream_url"]
    assert (await c.get(url, headers={"Range": "bytes=0-9"})).status_code == 206
    assert await listen.listened_candidate("dz:100") is not None  # (waits for the check after listening)
    await listen.reset()  # the Hub restarted: nothing in memory

    r = await c.get("/api/listen", params={"ref": "dz:100"})
    assert r.json()["provider"] == "hitmoz" and r.json()["page_url"] == "https://hitmoz.test/song/1"
    r = await c.get(url, headers={"Range": "bytes=10-19"})
    assert r.status_code == 206 and r.content == AUDIO[10:20]
    assert hm.searched == 1  # no new search
    heard = await listen.listened_candidate("dz:100")
    assert heard is not None and heard.provider_id == "1"


async def test_russian_track_takes_soundcloud_uploaded_before_the_cutoff_over_youtube(env):
    from app.config import SourcesCfg, get_settings
    from app.services import svc

    get_settings().cfg.sources.ru = SourcesCfg().ru
    up = env["hm"].client
    sc = FakeProvider("soundcloud", [
        # the better file, but uploaded since May 2026: it may be edited
        cand("new", "https://cdn.test/good.mp3", provider="soundcloud").model_copy(
            update={"upload_date": date(2026, 5, 1)}),
        cand("old", "https://cdn.test/good.mp3", provider="soundcloud", bitrate=160),
    ], client=up, streams=False)
    yt = FakeProvider("ytdlp", [cand("yt", "https://cdn.test/good.mp3", provider="ytdlp")], client=up)
    svc.providers.update(soundcloud=sc, ytdlp=yt)
    r = await env["client"].get("/api/listen", params={"ref": "dz:100"})
    assert r.status_code == 200, r.text
    assert r.json()["provider"] == "soundcloud" and r.json()["page_url"] == "https://soundcloud.test/song/old"


@pytest.mark.parametrize("picked", ["hitmoz", "ytdlp"])
async def test_pick_outranked_by_a_new_source_order_is_picked_again(env, picked):
    from app import listen
    from app.config import SourcesCfg, get_settings
    from app.services import svc

    c, hm, up = env["client"], env["hm"], env["hm"].client
    sources = get_settings().cfg.sources
    sources.ru = ["hitmoz", "hitmos_fm", "ytdlp"]  # the order before SoundCloud came ahead of YouTube
    svc.providers["ytdlp"] = FakeProvider("ytdlp", [cand("yt", "https://cdn.test/good.mp3", provider="ytdlp")],
                                          client=up)
    if picked == "hitmoz":
        hm.cands = [cand("1", "https://cdn.test/good.mp3")]
    assert (await c.get("/api/listen", params={"ref": "dz:100"})).json()["provider"] == picked
    await listen.reset()

    sources.ru = SourcesCfg().ru
    sc = FakeProvider("soundcloud", [cand("sc", "https://cdn.test/good.mp3", provider="soundcloud")], client=up,
                      streams=False)
    svc.providers["soundcloud"] = sc
    r = await c.get("/api/listen", params={"ref": "dz:100"})
    # a hitmoz pick still comes first; a YouTube one gives way to SoundCloud, which was not asked then
    assert r.json()["provider"] == ("hitmoz" if picked == "hitmoz" else "soundcloud")
    assert sc.searched == (0 if picked == "hitmoz" else 1)


async def test_youtube_pick_gives_way_when_hitmoz_moves_ahead(env):
    # hitmoz was behind YouTube, so it was not asked when YouTube had the track
    from app import listen
    from app.config import get_settings
    from app.services import svc

    c, hm, up = env["client"], env["hm"], env["hm"].client
    sources = get_settings().cfg.sources
    sources.ru = ["ytdlp", "hitmoz"]
    svc.providers["ytdlp"] = FakeProvider("ytdlp", [cand("yt", "https://cdn.test/good.mp3", provider="ytdlp")],
                                          client=up)
    hm.cands = [cand("1", "https://cdn.test/good.mp3")]
    assert (await c.get("/api/listen", params={"ref": "dz:100"})).json()["provider"] == "ytdlp"
    await listen.reset()

    sources.ru = ["hitmoz", "ytdlp"]
    r = await c.get("/api/listen", params={"ref": "dz:100"})
    assert r.json()["provider"] == "hitmoz", r.text


async def test_imported_track_forgets_the_pick(env):
    from app import listen

    c, hm = env["client"], env["hm"]
    hm.cands = [cand("1", "https://cdn.test/good.mp3")]
    await c.get("/api/listen", params={"ref": "dz:100"})
    await listen.forget("dz:100")
    await listen.reset()
    await c.get("/api/listen", params={"ref": "dz:100"})
    assert hm.searched == 2


async def test_prefetch_picks_and_opens_tracks_on_screen(env):
    import asyncio

    from app import listen

    c, hm = env["client"], env["hm"]
    hm.cands = [cand("1", "https://cdn.test/dead.mp3", bitrate=None), cand("2", "https://cdn.test/good.mp3", bitrate=256)]
    r = await c.post("/api/listen/prefetch", json={"refs": ["dz:100", "dz:100", "bad"]})
    assert r.status_code == 200 and r.json()["queued"] == 2
    for _ in range(200):
        hit = listen._choices.get("dz:100")
        if hit and hit.source is not None and "bad" in listen._missed:
            break
        await asyncio.sleep(0.01)
    hit = listen._choices["dz:100"]
    # the background pick has opened its file already: the dead one gave way to the next
    assert hit.candidate.provider_id == "2" and hit.source is not None
    # what is ready (or recently not found) is not queued again
    assert (await c.post("/api/listen/prefetch", json={"refs": ["dz:100", "bad"]})).json()["queued"] == 0

    r = await c.get("/api/listen", params={"ref": "dz:100"})
    assert r.json()["page_url"] == "https://hitmoz.test/song/2"
    assert (await c.get(r.json()["stream_url"], headers={"Range": "bytes=0-9"})).status_code == 206
    assert hm.searched == 1
    assert (await c.post("/api/listen/prefetch", json={"refs": []})).json()["queued"] == 0


async def test_censored_copy_is_not_downloaded_and_is_picked_again(env):
    from app import listen
    from app.services import svc

    c, hm = env["client"], env["hm"]
    svc.deezer.track = svc.deezer.track.model_copy(update={"explicit": True})
    hm.cands = [cand("1", "https://cdn.test/good.mp3"), cand("3", "https://cdn.test/good.mp3", bitrate=256)]
    hm.explicit = {"1": False, "3": True}  # only the track pages tell: "1" is the clean copy
    url = (await c.get("/api/listen", params={"ref": "dz:100"})).json()["stream_url"]
    assert (await c.get(url, headers={"Range": "bytes=0-9"})).status_code == 206
    assert await listen.listened_candidate("dz:100") is None  # a download searches as usual
    await listen.reset()
    r = await c.get("/api/listen", params={"ref": "dz:100"})
    assert r.json()["page_url"] == "https://hitmoz.test/song/3"  # remembered from the download-grade search
    assert hm.searched == 2


async def test_expired_link_is_renewed(env):
    c, hm, mock = env["client"], env["hm"], env["mock"]
    exp = {"n": 1}
    # hitmoz-like: /get/music/ redirects to a signed CDN link; only the newest link works
    mock.route(host="hm.test").mock(side_effect=lambda req: httpx.Response(
        302, headers={"location": f"https://signed.test/a.mp3?exp={exp['n']}"}))
    mock.route(host="signed.test").mock(side_effect=lambda req: ranged(req) if req.url.params["exp"] == str(exp["n"])
                                        else httpx.Response(403))
    hm.cands = [cand("1", "https://hm.test/get/music/1.mp3")]
    url = (await c.get("/api/listen", params={"ref": "dz:100"})).json()["stream_url"]
    assert (await c.get(url, headers={"Range": "bytes=0-9"})).status_code == 206

    from app.listen import _choices, _copies

    assert _choices["dz:100"].source.url == "https://signed.test/a.mp3?exp=1"  # later requests skip the redirect
    exp["n"] = 2
    _copies.clear()  # the server copy was pruned: the file comes from the source again
    r = await c.get(url, headers={"Range": "bytes=10-19"})
    assert r.status_code == 206 and r.content == AUDIO[10:20]


async def test_audio_is_proxied_over_http1_with_the_provider_headers(env):
    from app import listen

    c, hm, mock = env["client"], env["hm"], env["mock"]
    seen: list[httpx.Request] = []
    mock.route(host="h1.test").mock(side_effect=lambda req: seen.append(req) or ranged(req))
    hm.client = httpx.AsyncClient(headers={"Accept-Language": "ru-RU"}, http2=True)
    hm.cands = [cand("1", "https://h1.test/a.mp3")]
    url = (await c.get("/api/listen", params={"ref": "dz:100"})).json()["stream_url"]
    for rng, body in (("bytes=0-9", AUDIO[:10]), ("bytes=100-", AUDIO[100:])):  # the player leaves for a seek
        r = await c.get(url, headers={"Range": rng})
        assert r.status_code == 206 and r.content == body
    # the Hub downloads the whole file once (its server copy); the seek is served from its disk
    assert [r.headers["range"] for r in seen] == ["bytes=0-"]
    assert all(r.headers["accept-language"] == "ru-RU" and r.headers["accept-encoding"] == "identity" for r in seen)
    # not the provider's HTTP/2 client: abandoned streams there starve the next ones on the connection
    assert listen._media is not None and listen._media is not hm.client
    await hm.client.aclose()


async def test_dropped_upstream_connection_is_resumed(env):
    c, hm, mock = env["client"], env["hm"], env["mock"]
    seen: list[str | None] = []

    def cdn(req: httpx.Request) -> httpx.Response:  # googlevideo cuts a long response midway
        seen.append(req.headers.get("range"))
        return partway(req, 1000, drop=True) if len(seen) == 1 else ranged(req)

    mock.route(host="drop.test").mock(side_effect=cdn)
    hm.cands = [cand("1", "https://drop.test/a.mp3")]
    url = (await c.get("/api/listen", params={"ref": "dz:100"})).json()["stream_url"]
    r = await c.get(url)
    # the player gets the whole file: the Hub resumes its download from the byte the source broke at
    assert r.status_code == 200 and r.content == AUDIO
    assert seen == ["bytes=0-", "bytes=1000-"]


async def test_a_whole_copy_is_found_again_on_disk_after_a_restart(env):
    from app import listen

    c, hm, mock = env["client"], env["hm"], env["mock"]
    seen: list[str | None] = []

    def cdn(req: httpx.Request) -> httpx.Response:
        seen.append(req.headers.get("range"))
        return ranged(req)

    mock.route(host="keep.test").mock(side_effect=cdn)
    hm.cands = [cand("1", "https://keep.test/a.mp3")]
    url = (await c.get("/api/listen", params={"ref": "dz:100"})).json()["stream_url"]
    assert (await c.get(url)).content == AUDIO
    assert seen == ["bytes=0-"]
    # the Hub forgot its copies (a restart, the pick gone from memory): the one on its disk is served, not fetched anew
    listen._copies.clear()
    listen._choices.clear()
    r = await c.get(url, headers={"Range": "bytes=10-19"})
    assert r.status_code == 206 and r.content == AUDIO[10:20]
    assert seen == ["bytes=0-"]
    # «Офлайн» says what the copies take and how long they stay
    st = (await c.get("/api/listen/storage")).json()
    assert st["count"] == 1 and st["bytes"] >= len(AUDIO) and st["keep_days"] == 30 and st["limit_bytes"] == 30 * 2**30


async def test_track_loaded_ahead_waits_for_the_played_one(env):
    from app import listen

    c, hm, mock = env["client"], env["hm"], env["mock"]
    gate = asyncio.Event()
    mock.route(host="slow.test").mock(side_effect=lambda req: partway(req, 1000, gate=gate))
    hm.cands = [cand("1", "https://slow.test/a.mp3"), cand("2", "https://cdn.test/good.mp3")]
    await search_files(c)

    played = await listen.open_media("file:hitmoz:1", None)
    assert played.body is not None
    assert await anext(played.body) == AUDIO[:1000]  # the player reads it while the Hub downloads the rest
    # the app loads the next track ahead: its server copy waits while the played one downloads
    ahead = await listen.open_media("file:hitmoz:2", "bytes=0-9", ahead=True)
    assert ahead.status == 206 and ahead.body is not None
    await asyncio.sleep(0.3)
    assert listen._copies["file:hitmoz:2"].have == 0

    gate.set()
    assert AUDIO[:1000] + b"".join([chunk async for chunk in played.body]) == AUDIO
    assert b"".join([chunk async for chunk in ahead.body]) == AUDIO[:10]


async def test_seek_far_past_the_server_copy_goes_to_the_source(env, monkeypatch):
    from app import listen

    c, hm, mock = env["client"], env["hm"], env["mock"]
    monkeypatch.setattr(listen, "SEEK_WAIT_BYTES", 1000)
    gate = asyncio.Event()
    seen: list[str] = []

    def cdn(req: httpx.Request) -> httpx.Response:
        seen.append(req.headers["range"])
        return partway(req, 1000, gate=gate) if req.headers["range"] == "bytes=0-" else ranged(req)

    mock.route(host="slow.test").mock(side_effect=cdn)
    hm.cands = [cand("1", "https://slow.test/a.mp3")]
    url = (await c.get("/api/listen", params={"ref": "dz:100"})).json()["stream_url"]
    r = await c.get(url, headers={"Range": "bytes=100000-100009"})
    assert r.status_code == 206 and r.content == AUDIO[100000:100010]
    assert r.headers["content-range"] == f"bytes 100000-100009/{len(AUDIO)}"
    assert seen == ["bytes=0-", "bytes=100000-100009"]  # not waiting for the copy to get there
    gate.set()


async def test_unavailable_recording_does_not_serve_a_catalog_preview(env):
    c = env["client"]
    for endpoint in ("/api/listen", "/api/listen/stream"):
        response = await c.get(endpoint, params={"ref": "dz:100"})
        assert response.status_code == 404, response.text
        assert "Полная версия" in response.json()["detail"]
    assert not any(call.request.url.host == "preview.test" for call in env["mock"].calls)


async def test_legacy_preview_in_cache_is_ignored_and_full_sources_are_searched(env):
    from app.catalog.cache import cache_set
    from app.services import svc

    c = env["client"]
    await cache_set("listen:dz:100", {
        "kind": "preview", "artist": "Кино", "title": "Группа крови", "duration_s": 30,
    }, 86400)
    svc.providers["soundcloud"] = FakeProvider("soundcloud", [
        cand("full", "https://cdn.test/good.mp3", provider="soundcloud"),
    ], client=env["hm"].client)
    response = await c.get("/api/listen/stream", params={"ref": "dz:100"},
                           headers={"Range": "bytes=0-"})
    assert response.status_code == 206, response.text
    assert response.headers["X-HomeMusic-Listen-Kind"] == "full"
    assert response.content == AUDIO
    assert not any(call.request.url.host == "preview.test" for call in env["mock"].calls)


async def test_slow_regular_sources_have_a_deadline_before_full_song_fallback(env, monkeypatch):
    from app import listen
    from app.services import svc

    # Expire the regular-source budget immediately; no real timer is waited out.
    monkeypatch.setattr(listen, "SEARCH_TIMEOUT_S", 0)
    svc.providers["soundcloud"] = FakeProvider("soundcloud", [
        cand("full", "https://cdn.test/good.mp3", provider="soundcloud"),
    ], client=env["hm"].client)
    response = await env["client"].get("/api/listen", params={"ref": "dz:100"})
    assert response.status_code == 200, response.text
    assert response.json()["kind"] == "full" and response.json()["provider"] == "soundcloud"


async def test_optional_name_lookup_cannot_delay_playback(env, monkeypatch):
    from app import listen

    monkeypatch.setattr(listen, "NAME_TIMEOUT_S", 0)
    env["hm"].cands = [cand("full", "https://cdn.test/good.mp3")]
    response = await env["client"].get("/api/listen", params={"ref": "dz:100"})
    assert response.status_code == 200, response.text
    assert response.json()["kind"] == "full" and response.json()["provider"] == "hitmoz"


async def test_simultaneous_metadata_and_stream_requests_agree_when_no_file_is_available(env, monkeypatch):
    entered, release = asyncio.Event(), asyncio.Event()

    async def search(q, limit=10):
        entered.set()
        await release.wait()
        return []

    monkeypatch.setattr(env["hm"], "search", search)
    browser = asyncio.create_task(env["client"].get("/api/listen", params={"ref": "dz:100"}))
    native = None
    try:
        await asyncio.wait_for(entered.wait(), 1)
        native = asyncio.create_task(env["client"].get("/api/listen/stream", params={"ref": "dz:100"}))
        await asyncio.sleep(0)
        release.set()
        assert (await asyncio.wait_for(browser, 1)).status_code == 404
        response = await asyncio.wait_for(native, 1)
        assert response.status_code == 404, response.text
        assert not any(call.request.url.host == "preview.test" for call in env["mock"].calls)
    finally:
        release.set()
        await asyncio.gather(*(t for t in [browser, native] if t is not None), return_exceptions=True)


async def test_import_finishing_during_resolution_provides_the_file(env, monkeypatch):
    from app.db import session_scope
    from app.matching.normalize import fingerprint

    entered, release = asyncio.Event(), asyncio.Event()

    async def search(q, limit=10):
        entered.set()
        await release.wait()
        return []

    monkeypatch.setattr(env["hm"], "search", search)
    pending = asyncio.create_task(env["client"].get("/api/listen/stream", params={"ref": "dz:100"}))
    try:
        await asyncio.wait_for(entered.wait(), 1)
        rel = "QA/song.mp3"
        path = env["music"] / "library" / rel
        path.parent.mkdir(parents=True)
        path.write_bytes(AUDIO)
        async with session_scope() as s:
            s.add(filed_track(uid="imported-during-search", path=rel, artist="Кино", title="Группа крови",
                        duration_s=245, isrc="RUA1D8800001", nd_song_id="qa-song",
                        fingerprint=fingerprint("Кино", "Группа крови")))
    finally:
        release.set()
        response = await asyncio.wait_for(pending, 1)
    assert response.status_code == 200, response.text
    assert response.headers["X-HomeMusic-Listen-Kind"] == "library"
    assert response.content == AUDIO


async def test_source_failure_is_not_cached_and_playback_can_retry(env):
    from app import listen

    c, hm, fm = env["client"], env["hm"], env["fm"]
    hm.down = fm.down = True  # e.g. the Hub's network dropped for a minute
    r = await c.get("/api/listen", params={"ref": "dz:100"})
    assert r.status_code == 502 and "недоступны" in r.json()["detail"]
    assert "dz:100" not in listen._choices
    hm.down = fm.down = False
    hm.cands = [cand("1", "https://cdn.test/good.mp3")]
    assert (await c.get("/api/listen", params={"ref": "dz:100"})).json()["kind"] == "full"
    await listen.reset()
    assert (await c.get("/api/listen", params={"ref": "dz:100"})).json()["kind"] == "full"


async def test_nothing_to_play(env):
    from app.services import svc

    svc.deezer = FakeDeezer(preview=None)
    c = env["client"]
    r = await c.get("/api/listen", params={"ref": "dz:100"})
    assert r.status_code == 404 and "ни в одном источнике" in r.json()["detail"]
    assert (await c.get("/api/listen", params={"ref": "dz:404"})).status_code == 404
    assert (await c.get("/api/listen", params={"ref": "nonsense"})).status_code == 400


@pytest.mark.parametrize("by_hub_id", [False, True])
async def test_unavailable_catalog_track_can_open_the_file_picker(env, monkeypatch, by_hub_id):
    from app import listen
    from app.catalog.cache import cache_delete, cache_set
    from app.db import session_scope
    from app.models import TrackFile
    from app.services import svc

    c = env["client"]
    ref = "dz:100"
    target = await c.post("/api/quality/track/resolve", json={"ref": ref})
    assert target.status_code == 200, target.text
    track_id = target.json()["track_id"]
    hub_ref = f"lib:{track_id}"
    playback_ref = hub_ref if by_hub_id else ref
    playlist = await c.post("/api/playlists", json={"name": "Выбор файла"})
    page = f"/api/playlists/{playlist.json()['id']}"
    assert (await c.post(f"{page}/add", json={"track_ids": [track_id]})).status_code == 200

    async def availability():
        playlist_track, = (await c.get(page)).json()["tracks"]
        album_track, = (await c.get("/api/catalog/album/dz:10")).json()["tracks"]
        assert playlist_track["library_track_id"] == album_track["library_track_id"] == track_id
        fields = "unknown", "unavailable", "owned"
        assert all(playlist_track[field] == album_track[field] for field in fields)
        return tuple(album_track[field] for field in fields)

    assert await availability() == (False, False, False)
    env["hm"].down = True
    assert (await c.get("/api/listen", params={"ref": playback_ref})).status_code == 502
    assert await availability() == (False, False, False)
    env["hm"].down = False
    assert (await c.get("/api/listen", params={"ref": playback_ref})).status_code == 404
    await listen.reset()
    assert await availability() == (False, True, False)

    other_key = f"listen:{ref if by_hub_id else hub_ref}"
    candidate = cand("1", "https://cdn.test/good.mp3").model_dump(mode="json")
    await cache_set(other_key, {"kind": "full", "candidate": candidate}, 600)
    assert await availability() == (False, False, False)
    await cache_delete(other_key)

    again = await c.post("/api/quality/track/resolve", json={"ref": ref})
    assert again.json() == target.json()
    info = await c.get(f"/api/quality/track/{track_id}")
    assert (info.json()["title"], info.json()["file_uid"]) == ("Группа крови", None)

    # The manual choice still plays if it differs in duration and the catalog goes offline.
    path = env["music"] / "library" / "picked.mp3"
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_bytes(AUDIO)
    async with session_scope() as s:
        s.add(TrackFile(track_id=track_id, uid="manual-pick", path="picked.mp3", main=True))

    async def catalog_offline(_):
        raise RuntimeError("catalog offline")

    monkeypatch.setattr(svc.deezer, "get_track", catalog_offline)
    stream = await c.get("/api/listen/stream", params={"ref": ref})
    assert stream.status_code == 200 and stream.content == AUDIO
    assert await availability() == (False, False, True)


async def test_file_picker_keeps_cover_from_the_selected_recording(env, monkeypatch):
    import io
    import wave

    from PIL import Image

    from app import listen
    from app.api import file_picker
    from app.db import session_scope
    from app.library import tagger
    from app.library.cards import draw_songs as songs_out
    from app.library.probe import ProbeInfo
    from app.library.files import main_of

    image = io.BytesIO()
    Image.new("RGB", (360, 360), "seagreen").save(image, "PNG")
    cover_url = "https://covers.test/recording.png"
    env["mock"].get(cover_url).mock(return_value=httpx.Response(200, content=image.getvalue(),
                                                              headers={"content-type": "image/png"}))
    candidate = cand("1", "https://cdn.test/good.mp3").model_copy(update={"cover_url": cover_url})
    await listen.remember_files([candidate])
    ref = "file:hitmoz:1"
    path = env["music"] / "picked.wav"
    with wave.open(str(path), "wb") as audio:
        audio.setparams((1, 2, 8000, 0, "NONE", "not compressed"))
        audio.writeframes(bytes(16000))
    listen._choices[ref].local = path

    async def probe_audio(_path):
        return ProbeInfo(duration_s=1, codec="wav")

    monkeypatch.setattr(file_picker, "_probe_audio", probe_audio)
    c = env["client"]
    track = await c.post("/api/quality/track/resolve", json={"ref": "dz:100"})
    track_id = track.json()["track_id"]
    playlist = await c.post("/api/playlists", json={"name": "Обложки"})
    page = f"/api/playlists/{playlist.json()['id']}"
    assert (await c.post(f"{page}/add", json={"track_ids": [track_id]})).status_code == 200
    take = await c.post(f"/api/quality/track/{track_id}/take", json={"ref": ref, "expected_file_uid": None})
    assert take.status_code == 200, take.text
    # which file the track has now: a device that keeps another fetches it anew (`web/src/lib/offline.ts`)
    async with session_scope() as s:
        uid = (await main_of(s, track_id)).uid
    assert (await c.get("/api/listen", params={"ref": f"lib:{track_id}"})).json()["file_uid"] == uid
    row, = (await c.get(page)).json()["tracks"]
    assert row["cover_url"].startswith("/api/library/albums/")
    cover = await c.get(row["cover_url"])
    assert cover.status_code == 200
    assert Image.open(io.BytesIO(cover.content)).size == (360, 360)

    async with session_scope() as s:
        file = await main_of(s, track_id)
        assert tagger.read_cover(env["music"] / "library" / file.path) == cover.content
        file.nd_song_id = "picked-song"
        await s.flush()
        scanned, = await songs_out(s, [{"id": "picked-song", "title": candidate.title, "artist": candidate.artist}])
        assert scanned.cover_url == row["cover_url"]


async def test_cover_search_and_saving_for_track_and_catalog_album(env):
    import io

    from PIL import Image

    from app.services import svc

    def picture(w, h, color):
        dest = io.BytesIO()
        Image.new("RGB", (w, h), color).save(dest, "PNG")
        return dest.getvalue()

    source_image = picture(720, 404, "seagreen")
    from_catalog = "https://covers.test/catalog.png"
    from_provider = "https://covers.test/recording.png"
    svc.deezer = FakeDeezer(cover_url=from_catalog)
    headers = {"Content-Type": "image/png"}
    env["mock"].get(from_catalog).mock(return_value=httpx.Response(200, content=picture(500, 500, "blue"), headers=headers))
    env["mock"].get(from_provider).mock(return_value=httpx.Response(200, content=source_image, headers=headers))
    env["hm"].cands = [cand("1", "https://cdn.test/good.mp3").model_copy(update={"cover_url": from_provider})]

    c = env["client"]
    query = await c.get("/api/search/covers", params={"q": "Группа крови", "album": True})
    assert query.status_code == 200, query.text
    covers = query.json()["covers"]
    assert sorted(o["source"] for o in covers) == ["deezer", "hitmoz"]
    result = next(o for o in covers if o["source"] == "hitmoz")
    response = await c.get(f"/api/search/covers/{result['key']}")
    assert response.status_code == 200 and response.content == source_image
    assert (response.headers["X-Cover-Width"], response.headers["X-Cover-Height"], response.headers["X-Cover-Format"]) == ("720", "404", "PNG")
    assert (await c.get("/api/search/covers/" + "0" * 64)).status_code == 404

    track = await c.post("/api/quality/track/resolve", json={"ref": "dz:100"})
    track_id = track.json()["track_id"]
    file = {"file": ("cover.png", response.content, "image/png")}
    saved_track = await c.put(f"/api/library/tracks/{track_id}/cover", files=file)
    assert saved_track.status_code == 200 and saved_track.json()["own_cover"]

    endpoint = "/api/catalog/album/dz:10"
    invalid = await c.put(endpoint + "/cover", files={"file": ("bad.jpg", b"invalid", "image/jpeg")})
    assert invalid.status_code == 400
    assert (await c.get(endpoint)).json()["library_album_id"] is None
    saved_album = await c.put(endpoint + "/cover", files=file)
    assert saved_album.status_code == 200, saved_album.text
    album = (await c.get(endpoint)).json()
    assert album["library_album_id"] == saved_album.json()["id"]
    assert album["cover_url"] == saved_album.json()["cover_url"]
    assert album["tracks"][0]["cover_url"] == saved_track.json()["cover_url"]
    assert (await c.get(album["cover_url"])).status_code == 200
    replaced = await c.put(endpoint + "/cover", files={"file": ("new.png", picture(500, 500, "brown"), "image/png")})
    assert replaced.status_code == 200
    assert replaced.json()["id"] == album["library_album_id"]
    assert replaced.json()["cover_url"] != album["cover_url"]
    assert (await c.get(f"/api/library/track-edit?track_id={track_id}")).json()["cover_url"] == saved_track.json()["cover_url"]


async def test_owned_track_plays_the_library_file(env, monkeypatch):
    from unittest.mock import AsyncMock

    from app.db import session_scope
    from app.matching.normalize import fingerprint
    from app.services import svc

    rel = "Кино/1988 - Группа крови/1-01 - Группа крови.mp3"
    f = env["music"] / "library" / rel
    f.parent.mkdir(parents=True)
    f.write_bytes(AUDIO)
    async with session_scope() as s:
        s.add(filed_track(uid="u1", path=rel, artist="Кино", title="Группа крови", duration_s=245, isrc="RUA1D8800001",
                    fingerprint=fingerprint("Кино", "Группа крови")))
    c = env["client"]
    album_lookup = AsyncMock(return_value=svc.deezer.album)
    monkeypatch.setattr(svc.deezer, "get_album", album_lookup)
    r = await c.get("/api/listen", params={"ref": "dz:100"})
    assert r.status_code == 200, r.text
    assert r.json()["kind"] == "library" and env["hm"].searched == 0
    album_lookup.assert_not_awaited()
    assert r.json()["nd_song_id"] is None  # Navidrome hasn't scanned it yet
    url = r.json()["stream_url"]
    r = await c.get(url, headers={"Range": "bytes=0-9"})
    assert r.status_code == 206 and r.content == AUDIO[:10]
    # through the gateway that sends files itself: the Hub says which, and sends no audio
    r = await c.get(url, headers={"Range": "bytes=0-9", "X-HomeMusic-Accel": "1"})
    assert r.status_code == 200 and r.content == b""
    assert r.headers["x-accel-redirect"] == "/_files/music/library/" + quote(rel)
    assert r.headers["content-type"] == "audio/mpeg" and r.headers["x-homemusic-listen-kind"] == "library"

    # scanned meanwhile: the cached choice is looked up again and carries the song id (♥ in the player)
    async with session_scope() as s:
        from sqlalchemy import update

        from app.models import TrackFile

        await s.execute(update(TrackFile).values(nd_song_id="nd7"))
    r = await c.get("/api/listen", params={"ref": "dz:100"})
    assert r.json()["kind"] == "library" and r.json()["nd_song_id"] == "nd7"


async def test_library_song_of_a_mix_plays_its_file(env):
    """A song only the library has (`nd:<song>`, a mix's track) plays its file in the page's player."""
    from app.db import session_scope
    from app.matching.normalize import fingerprint

    rel = "Кино/1988 - Группа крови/1-02 - Закрой за мной дверь.mp3"
    f = env["music"] / "library" / rel
    f.parent.mkdir(parents=True)
    f.write_bytes(AUDIO)
    async with session_scope() as s:
        s.add(filed_track(uid="u2", path=rel, artist="Кино", title="Закрой за мной дверь", duration_s=260,
                    nd_song_id="nd9", fingerprint=fingerprint("Кино", "Закрой за мной дверь")))
    c = env["client"]
    r = await c.get("/api/listen", params={"ref": "nd:nd9"})
    assert r.status_code == 200, r.text
    out = r.json()
    assert out["kind"] == "library" and out["nd_song_id"] == "nd9" and out["title"] == "Закрой за мной дверь"
    assert env["hm"].searched == 0
    r = await c.get(r.json()["stream_url"], headers={"Range": "bytes=0-9"})
    assert r.status_code == 206 and r.content == AUDIO[:10]
    assert (await c.get("/api/listen", params={"ref": "nd:gone"})).status_code == 404


async def search_files(c, group: str | None = "sources") -> list[dict]:
    """The Files tab of the search: the files it shows can be listened to by their ref."""
    params = {"q": "Кино - Группа крови", **({"group": group} if group else {})}
    r = await c.get("/api/search/providers", params=params)
    assert r.status_code == 200, r.text
    return r.json()["candidates"]


async def settled(cond) -> None:
    """Waits for the background prefetch (it goes through the DB thread, so a few real ticks)."""
    import asyncio

    for _ in range(200):
        if cond():
            return
        await asyncio.sleep(0.01)
    raise AssertionError("the prefetch did not finish")


async def test_file_from_the_search_is_listened_to_by_its_ref(env):
    from app import listen

    c, hm = env["client"], env["hm"]
    hm.cands = [cand("1", "https://cdn.test/good.mp3", bitrate=128), cand("2", "https://cdn.test/dead.mp3")]
    assert {f["provider_id"] for f in await search_files(c)} == {"1", "2"}

    r = await c.get("/api/listen", params={"ref": "file:hitmoz:1"})
    assert r.status_code == 200, r.text
    out = r.json()
    assert (out["kind"], out["provider"], out["page_url"], out["bitrate_kbps"]) == (
        "full", "hitmoz", "https://hitmoz.test/song/1", 128)
    r = await c.get(out["stream_url"], headers={"Range": "bytes=100-199"})
    assert r.status_code == 206 and r.content == AUDIO[100:200]
    # that very file: no search or pick of its own, no track page check
    assert hm.searched == 1 and hm.enriched == []

    await listen.reset()  # the Hub restarted: the files of the search are remembered
    r = await c.get(out["stream_url"], headers={"Range": "bytes=0-9"})
    assert r.status_code == 206 and r.content == AUDIO[:10]
    assert hm.searched == 1

    # a file that doesn't open is an error, not some other file
    r = await c.get("/api/listen/stream", params={"ref": "file:hitmoz:2"})
    assert r.status_code == 502, r.text
    r = await c.get("/api/listen", params={"ref": "file:hitmoz:404"})
    assert r.status_code == 404 and "повторите поиск" in r.json()["detail"]


async def test_prefetch_opens_files_on_screen(env):
    from app import listen

    c, hm = env["client"], env["hm"]
    hm.cands = [cand("1", "https://cdn.test/good.mp3")]
    await search_files(c)
    r = await c.post("/api/listen/prefetch", json={"refs": ["file:hitmoz:1"]})
    assert r.status_code == 200 and r.json()["queued"] == 1
    await settled(lambda: listen._choices["file:hitmoz:1"].source is not None)
    assert (await c.post("/api/listen/prefetch", json={"refs": ["file:hitmoz:1"]})).json()["queued"] == 0


async def test_file_without_http_audio_plays_from_a_local_copy(env, tmp_path):
    from app import listen
    from app.services import svc

    c = env["client"]
    src = tmp_path / "upload.mp3"
    src.write_bytes(AUDIO)
    sc = FakeProvider("soundcloud", [cand("5", "", provider="soundcloud")], src=src, streams=False)
    svc.providers["soundcloud"] = sc
    assert [f["provider"] for f in await search_files(c, group=None)] == ["soundcloud"]

    # on screen: the Hub downloads the copy in the background, so a tap plays at once
    assert (await c.post("/api/listen/prefetch", json={"refs": ["file:soundcloud:5"]})).json()["queued"] == 1
    await settled(lambda: listen._choices["file:soundcloud:5"].local is not None)
    r = await c.get("/api/listen/stream", params={"ref": "file:soundcloud:5"}, headers={"Range": "bytes=10-19"})
    assert r.status_code == 206 and r.content == AUDIO[10:20]
    assert r.headers["content-type"].startswith("audio/mpeg")
    r = await c.get("/api/listen/stream", params={"ref": "file:soundcloud:5"})
    assert r.status_code == 200 and r.content == AUDIO
    assert sc.fetched == 1
    assert len(list((env["music"] / "staging" / listen.LOCAL_DIR).glob("*/*.mp3"))) == 1


def test_local_copies_are_pruned(tmp_path, monkeypatch):
    import os
    import time

    from app import listen

    now = time.time()

    def copy(name: str, age_s: float, size: int = 10) -> None:
        d = tmp_path / name
        d.mkdir()
        f = d / "a.m4a"
        f.write_bytes(b"x" * size)
        os.utime(f, (now - age_s, now - age_s))

    copy("old", listen.LOCAL_KEEP_S + 1)
    copy("busy", listen.LOCAL_KEEP_S + 1)  # being downloaded again
    copy("b", 3)
    copy("c", 2)
    copy("d", 1)
    monkeypatch.setattr(listen, "LOCAL_MAX_BYTES", 25)  # room for two copies
    listen.prune_local(tmp_path, {"busy"}, now)
    assert sorted(p.name for p in tmp_path.iterdir()) == ["busy", "c", "d"]


async def test_file_listened_to_is_downloaded_by_its_ref(env):
    c, hm = env["client"], env["hm"]
    hm.cands = [cand("1", "https://cdn.test/good.mp3")]
    await search_files(c)
    # «Скачать» in the player: the file that played, as if picked in the search
    r = await c.post("/api/downloads", json={"track_refs": ["file:hitmoz:1"]})
    assert r.status_code == 200, r.text
    assert r.json()["queued"] == 1
    items = (await c.get("/api/downloads")).json()
    assert [(i["track_ref"], i["chosen"]["page_url"]) for i in items] == [("file:hitmoz:1", "https://hitmoz.test/song/1")]
    # ♥ on it then joins that download
    r = await c.post("/api/downloads", json={"track_refs": ["file:hitmoz:1"], "star": True})
    assert r.status_code == 200, r.text
    assert len((await c.get("/api/downloads")).json()) == 1
    r = await c.post("/api/downloads", json={"track_refs": ["file:hitmoz:404"]})
    assert r.status_code == 404


async def test_file_of_the_search_is_downloaded_as_it_is(env):
    c, hm = env["client"], env["hm"]
    hm.cands = [cand("1", "https://cdn.test/good.mp3")]
    await search_files(c)
    # a row of the Files tab: that very file is downloaded
    r = await c.post("/api/downloads", json={"track_refs": ["file:hitmoz:1"]})
    assert r.status_code == 200, r.text
    assert r.json()["queued"] == 1
    items = (await c.get("/api/downloads")).json()
    assert [(i["track_ref"], i["quiet"], i["chosen"]["page_url"]) for i in items] == [
        ("file:hitmoz:1", False, "https://hitmoz.test/song/1")]


@pytest.mark.parametrize("save", ["like", "playlist", "picked_file"])
async def test_saving_reuses_the_player_copy(env, monkeypatch, tmp_path, save):
    from sqlalchemy import select

    from app import listen
    from app.db import session_scope
    from app.jobs import downloads
    from app.library.probe import PostcheckResult
    from app.models import Job

    client, provider = env["client"], env["hm"]
    src = tmp_path / "fresh.mp3"
    src.write_bytes(AUDIO)
    provider.src = src
    provider.cands = [cand("1", "https://shared.test/song.mp3")]
    partial = save == "playlist"
    gate = asyncio.Event()
    requests = []

    def cdn(request):
        requests.append(request.headers.get("range"))
        return partway(request, 8192, gate=gate) if partial else ranged(request)

    env["mock"].route(host="shared.test").mock(side_effect=cdn)
    ref = "file:hitmoz:1" if save == "picked_file" else "dz:100"
    if save == "picked_file":
        await search_files(client)
    snap = await client.get("/api/listen/stream", params={"ref": ref}, headers={"Range": "bytes=0-1023"})
    assert snap.status_code == 206 and snap.content == AUDIO[:1024]
    copy = listen._copies[ref]

    body = {"track_refs": [ref]}
    if save == "playlist":
        playlist = await client.post("/api/playlists", json={"name": "Из плеера"})
        assert playlist.status_code == 200, playlist.text
        body["playlist_ids"] = [playlist.json()["id"]]
    elif save == "like":
        liked = await client.post("/api/star", json={"targets": [{"target_type": "track", "ref": ref}]})
        assert liked.status_code == 200, liked.text
    response = await client.post("/api/downloads", json=body)
    assert response.status_code == 200, response.text
    if response.json()["expanding"]:
        async with session_scope() as s:
            expansion = (await s.execute(select(Job).where(Job.kind == "expand_batch"))).scalar_one()
        await downloads.handle_expand_batch(expansion)
    async with session_scope() as s:
        job = (await s.execute(select(Job).where(Job.kind == "download_item"))).scalar_one()

    staged = asyncio.Event()
    original_stage = listen.stage_playback_file
    imported = []

    async def stage(*args):
        staged.set()
        return await original_stage(*args)

    async def postcheck(*_args):
        return PostcheckResult(ok=True)

    async def import_file(_item_id, _user, _query, _meta, candidate, path, _check, _force, **_kwargs):
        assert path != copy.path
        imported.append((candidate.provider_id, path.read_bytes()))

    monkeypatch.setattr(listen, "stage_playback_file", stage)
    monkeypatch.setattr(downloads.probe, "postcheck", postcheck)
    monkeypatch.setattr(downloads, "import_downloaded", import_file)
    if not partial:
        await asyncio.wait_for(copy.until(copy.settled), 5)
    pending = asyncio.create_task(downloads.handle_download_item(job))
    try:
        await asyncio.wait_for(staged.wait(), 5)
        if partial:
            assert not copy.done and not pending.done() and not imported and not provider.fetched
    finally:
        gate.set()
        await asyncio.wait_for(pending, 5)
    assert imported == [("1", AUDIO)]
    assert copy.path.read_bytes() == AUDIO
    assert requests == ["bytes=0-"] and provider.fetched == 0


async def test_attachment_requires_and_reuses_the_completed_preview(env, monkeypatch):
    from sqlalchemy import select

    from app import listen
    from app.db import session_scope
    from app.jobs import downloads
    from app.library.probe import PostcheckResult
    from app.models import DownloadBatch, DownloadItem, Job, Track, User

    client, provider = env["client"], env["hm"]
    candidate = cand("1", "https://cdn.test/good.mp3")
    provider.cands = [candidate]
    async with session_scope() as s:
        user = (await s.execute(select(User).where(User.nd_username == "admin"))).scalar_one()
        track = Track(artist=candidate.artist, title=candidate.title, fingerprint="fixture")
        batch = DownloadBatch(user_id=user.id, origin="hub")
        s.add_all([track, batch])
        await s.flush()
        item = DownloadItem(
            batch_id=batch.id, user_id=user.id, status="needs_review",
            query_json={"query": {"artist": track.artist, "title": track.title}, "meta": {"track_id": track.id}, "pick_only": True},
            candidates_json=[candidate.model_dump(mode="json")],
        )
        s.add(item)
        await s.flush()
        item_id, track_id = item.id, track.id

    base = f"/api/downloads/items/{item_id}"
    ref = "file:hitmoz:1"
    assert (await client.get(f"/api/downloads/{item_id}")).status_code == 200
    assert (await client.post(f"{base}/retry", json={"candidate_ref": ref})).status_code == 409
    assert (await client.get(f"{base}/prepared")).json() == []

    response = await client.get("/api/listen/stream", params={"ref": ref})
    assert response.content == AUDIO
    assert (await client.get(f"{base}/prepared")).json() == [ref]
    response = await client.post(f"{base}/retry", json={"candidate_ref": ref})
    assert response.status_code == 200, response.text

    # A queued attachment still has the exact audio if the playback cache disappears.
    listen._copies.clear()
    async with session_scope() as s:
        job = (await s.execute(select(Job).where(Job.kind == "download_item"))).scalar_one()

    imported = []

    async def postcheck(*_args):
        return PostcheckResult(ok=True)

    async def import_file(_item_id, _user, _query, meta, _cand, path, _check, _force, **_kwargs):
        imported.append((meta["track_id"], path.read_bytes()))

    monkeypatch.setattr(downloads.probe, "postcheck", postcheck)
    monkeypatch.setattr(downloads, "import_downloaded", import_file)
    await downloads.handle_download_item(job)
    assert imported == [(track_id, AUDIO)]
    assert provider.fetched == 0


async def test_replacement_reuses_the_completed_preview(env, monkeypatch):
    from app import listen
    from app.api import file_picker
    from app.db import session_scope
    from app.library.probe import ProbeInfo

    client, provider = env["client"], env["hm"]
    provider.cands = [cand("1", "https://cdn.test/good.mp3")]
    await search_files(client)
    async with session_scope() as s:
        track = filed_track(artist="Artist", title="Track", fingerprint="fixture")
        s.add(track)
        await s.flush()
        track_id = track.id

    ref = "file:hitmoz:1"
    base = f"/api/quality/track/{track_id}"
    file_uid = (await client.get(base)).json()["file_uid"]
    take_body = {"ref": ref, "expected_file_uid": file_uid, "sure": True}
    assert (await client.post(f"{base}/take", json=take_body)).status_code == 410
    assert (await client.get("/api/listen/stream", params={"ref": ref})).content == AUDIO
    cached = listen.prepared_file(ref)
    assert cached is not None

    async def probe_audio(path):
        assert path.read_bytes() == AUDIO
        return ProbeInfo(duration_s=100, codec="mp3")

    async def likeness(_mine, path):
        assert path.read_bytes() == AUDIO
        return 1.0

    taken = []

    async def take(_s, _user, track, path, **_kwargs):
        assert path != cached.path
        taken.append((track.id, path.read_bytes()))

    monkeypatch.setattr(file_picker, "_probe_audio", probe_audio)
    monkeypatch.setattr(file_picker, "_likeness", likeness)
    monkeypatch.setattr(file_picker.upgrade, "take_picked", take)
    response = await client.post(f"{base}/prepare", json={"ref": ref})
    assert response.status_code == 200, response.text
    assert response.json()["ref"] == ref
    response = await client.post(f"{base}/take", json={**take_body, "expected_file_uid": None})
    assert response.status_code == 409
    response = await client.post(f"{base}/take", json=take_body)
    assert response.status_code == 200, response.text
    assert response.json()["ok"]
    assert taken == [(track_id, AUDIO)]
    assert (await client.get(f"{base}/picked/audio", params={"ref": ref})).content == AUDIO
    assert provider.fetched == 0


async def test_picker_uploads_compares_and_attaches_without_replacing_the_track(env):
    import io
    import shutil
    import struct
    import wave

    from app.db import session_scope
    from app.library import probe
    from app.library.files import main_of
    from app.models import Track

    if not shutil.which(probe.ffmpeg_bin()) or not shutil.which(probe.ffprobe_bin()):
        pytest.skip("ffmpeg and ffprobe are needed")

    def signal(level: int) -> bytes:
        dest = io.BytesIO()
        with wave.open(dest, "wb") as out:
            out.setparams((1, 2, 44100, 0, "NONE", "not compressed"))
            for value in (800, level, 1200):
                out.writeframes(struct.pack("<h", value) * 44100)
        return dest.getvalue()

    async with session_scope() as s:
        track = Track(artist="Artist", title="Track", fingerprint="fixture")
        other = Track(artist="Artist", title="Different track", fingerprint="other")
        s.add_all([track, other])
        await s.flush()
        tid, other_id = track.id, other.id

    client = env["client"]
    base = f"/api/quality/track/{tid}"
    assert (await client.get(base)).json()["file_uid"] is None
    picks = []
    for level in (2400, 4800):
        response = await client.post(f"{base}/upload", files={"file": ("Запись.wav", signal(level), "audio/wav")})
        assert response.status_code == 200, response.text
        picks.append(response.json())
    mine, found = picks
    assert found["provider"] == "upload" and found["title"] == "Запись.wav"
    response = await client.post(f"{base}/compare", json={"ref": found["ref"], "baseline_ref": mine["ref"]})
    assert response.status_code == 200, response.text
    assert response.json()["length"] == {"mine": 3, "other": 3}
    assert all(response.json()["peaks"].values())
    params = {"ref": found["ref"], "start": 1.2, "end": 1.5}
    response = await client.get(f"/api/quality/track/{other_id}/picked/audio", params=params)
    assert response.status_code == 410
    response = await client.get(f"{base}/picked/audio", params=params)
    assert response.status_code == 200, response.text[:500]
    with wave.open(io.BytesIO(response.content), "rb") as excerpt:
        assert excerpt.getnframes() == 13230
        sample, *_ = struct.unpack("<hh", excerpt.readframes(1))
        assert sample > 2000  # comes from the middle section of the original
    assert (await client.get(base)).json()["file_uid"] is None

    response = await client.post(f"{base}/take", json={"ref": found["ref"], "expected_file_uid": None})
    assert response.status_code == 200, response.text
    async with session_scope() as s:
        track = await s.get(Track, tid)
        file = await main_of(s, tid)
        assert (track.title, track.artist) == ("Track", "Artist")
        assert file and file.source == "upload"
        assert (await main_of(s, other_id)) is None
    attached = await client.get("/api/listen/stream", params={"ref": f"lib:{tid}"})
    assert attached.status_code == 200 and attached.content.startswith(b"RIFF")


async def test_listen_needs_login(env):
    async with app_client(env["app"], login=None) as anon:
        assert (await anon.get("/api/listen", params={"ref": "dz:100"})).status_code == 401
        assert (await anon.get("/api/listen/stream", params={"ref": "dz:100"})).status_code == 401


async def test_a_track_two_ahead_is_copied_only_as_far_as_its_start(env, monkeypatch):
    """The queue's next track is loaded whole; the ones after it are opened and stop at their start, and go on
    from that byte when they are wanted."""
    from app import listen

    c, hm, mock = env["client"], env["hm"], env["mock"]
    monkeypatch.setattr(listen, "HEAD_BYTES", 500)
    seen: list[str | None] = []
    gate = asyncio.Event()

    def cdn(req: httpx.Request) -> httpx.Response:
        seen.append(req.headers.get("range"))
        # the first answer comes in pieces (1000 bytes, then the rest once `gate` opens), as a real source's does
        return partway(req, 1000, gate=gate) if len(seen) == 1 else ranged(req)

    mock.route(host="head.test").mock(side_effect=cdn)
    hm.cands = [cand("1", "https://head.test/a.mp3")]
    await search_files(c)

    r = await c.get("/api/listen", params={"ref": "file:hitmoz:1", "ahead": "head"})
    assert r.status_code == 200, r.text
    await settled(lambda: listen._copies["file:hitmoz:1"].have >= 500)
    await asyncio.sleep(0.2)
    cp = listen._copies["file:hitmoz:1"]
    assert (cp.have, cp.done, cp.failed, seen) == (1000, False, None, ["bytes=0-"])  # opened, and stopped at the start

    # it is the next track now: the same copy goes on from the byte it stopped at
    assert (await c.get("/api/listen", params={"ref": "file:hitmoz:1", "ahead": "whole"})).status_code == 200
    await settled(lambda: cp.done)
    assert (cp.have, seen) == (len(AUDIO), ["bytes=0-", "bytes=1000-"])
    assert listen._copies["file:hitmoz:1"] is cp
    gate.set()


async def test_a_track_stopped_at_its_start_plays_whole_when_it_is_reached(env, monkeypatch):
    from app import listen

    c, hm, mock = env["client"], env["hm"], env["mock"]
    monkeypatch.setattr(listen, "HEAD_BYTES", 500)
    seen: list[str | None] = []
    gate = asyncio.Event()

    def cdn(req: httpx.Request) -> httpx.Response:
        seen.append(req.headers.get("range"))
        return partway(req, 1000, gate=gate) if len(seen) == 1 else ranged(req)

    mock.route(host="head2.test").mock(side_effect=cdn)
    hm.cands = [cand("1", "https://head2.test/a.mp3")]
    await search_files(c)
    url = (await c.get("/api/listen", params={"ref": "file:hitmoz:1", "ahead": "head"})).json()["stream_url"]
    await settled(lambda: listen._copies["file:hitmoz:1"].have >= 500)

    r = await c.get(url)  # the player reaches it
    assert r.status_code == 200 and r.content == AUDIO
    assert seen == ["bytes=0-", "bytes=1000-"]
    gate.set()


async def test_a_tap_does_not_wait_for_the_names_before_it_searches(env, monkeypatch):
    """A Cyrillic name is searched at once; a Latin one is searched while its Cyrillic original is looked for."""
    import time

    from app import listen
    from app.services import svc

    c, hm = env["client"], env["hm"]
    hm.cands = [cand("1", "https://cdn.test/good.mp3")]
    started: list[float] = []
    real_search = listen.search_provider

    async def search(*a, **kw):
        started.append(time.monotonic())
        return await real_search(*a, **kw)

    async def slow_meta(meta):
        await asyncio.sleep(1.2)
        return meta

    async def slow_names(q, upc=None):
        await asyncio.sleep(1.2)
        return q, q  # no original: the catalog's names stand

    monkeypatch.setattr(listen, "search_provider", search)
    monkeypatch.setattr(listen, "complete_meta", slow_meta)
    monkeypatch.setattr(listen, "russian_original", slow_names)

    t0 = time.monotonic()
    r = await c.get("/api/listen", params={"ref": "dz:100"})  # «Кино — Группа крови»
    assert r.status_code == 200 and r.json()["kind"] == "full"
    assert time.monotonic() - t0 < 1.0 and started[0] - t0 < 0.5  # the names were not waited for

    svc.deezer.track = svc.deezer.track.model_copy(update={"id": "101", "ref": "dz:101", "artist": "Kino", "title": "Gruppa krovi"})
    started.clear()
    t0 = time.monotonic()
    r = await c.get("/api/listen", params={"ref": "dz:101"})
    assert r.status_code == 200 and r.json()["kind"] == "full"
    assert started and started[0] - t0 < 0.5  # searched before the original was known
    assert hm.searched == 2  # and that search was the one kept: the original changed nothing
