"""«Поделиться ссылкой»: the service ids imports and mixes have seen become the track's own."""

from __future__ import annotations

import uuid

import httpx
import pytest
import respx

from tests.support import app_client


@pytest.fixture
async def client(hub_env):
    from app.main import create_app

    app = create_app()
    async with app.router.lifespan_context(app):
        with respx.mock(assert_all_called=False) as mock:
            mock.route(host="navidrome.test").mock(return_value=httpx.Response(200, json={"subsonic-response": {
                "status": "ok", "version": "1.16.1", "user": {"adminRole": True}}}))
            async with app_client(app) as c:
                yield c


async def add_track(nd: str, **kw) -> int:
    from app.db import session_scope
    from app.matching.normalize import fingerprint
    from app.models import Track

    async with session_scope() as s:
        t = Track(uid=str(uuid.uuid4()), path=f"Кино/{nd}.mp3", nd_song_id=nd, artist="Кино", title=nd,
                  fingerprint=fingerprint("Кино", nd), **kw)
        s.add(t)
        await s.flush()
        return t.id


async def test_ids_come_from_imports_and_the_source_map_and_stay(client):
    from sqlalchemy import select

    from app.db import session_scope
    from app.imports import core
    from app.models import Import, ImportCollection, ImportItem, SourceMap, TrackLink, User

    a = await add_track("a", deezer_track_id="111", source_url="https://www.youtube.com/watch?v=D3gOdoFWCdY")
    b = await add_track("b", source_url="https://soundcloud.com/x/y")
    await add_track("c", source_url="https://ru.hitmoz.org/song/1")
    async with session_scope() as s:
        user = (await s.execute(select(User))).scalars().first()
        imp = Import(user_id=user.id, source="spotify", method="csv", status="preview")
        s.add(imp)
        await s.flush()
        coll = ImportCollection(import_id=imp.id, kind="playlist", external_id="csv:x", title="x")
        s.add(coll)
        await s.flush()
        for ext, track_id in (("4OJV0JNPCQ6M1n6JCtiZ9T", a), ("isrc:JPB600760301", b), ("5xAznC0k6LeMwmqA4qXdK3", None)):
            s.add(ImportItem(import_id=imp.id, collection_id=coll.id, external_id=ext, track_id=track_id))
        s.add(SourceMap(source="yandex", kind="track", external_id="138601318", ref="dz:111", match="exact"))
        s.add(SourceMap(source="ytm", kind="track", external_id="3jXiv7C_PVE", ref="dz:111", match="probable"))
        import_id = imp.id

    r = (await client.get("/api/links", params={"nd_song_id": "a"})).json()
    assert [(x["service"], x["url"]) for x in r["links"]] == [
        ("spotify", "https://open.spotify.com/track/4OJV0JNPCQ6M1n6JCtiZ9T"),
        ("yandex", "https://music.yandex.ru/track/138601318"),
        ("deezer", "https://www.deezer.com/track/111"),
        ("youtube", "https://www.youtube.com/watch?v=D3gOdoFWCdY")]
    assert r["links"][1]["title"] == "Яндекс Музыка" and r["universal"] == "https://song.link/s/4OJV0JNPCQ6M1n6JCtiZ9T"

    # a CSV line without a Spotify link gives no id; the page a file is from is a link only where it is a service
    r = (await client.get("/api/links", params={"nd_song_id": "b"})).json()
    assert r == {"links": [{"service": "soundcloud", "title": "SoundCloud", "url": "https://soundcloud.com/x/y"}], "universal": None}
    assert (await client.get("/api/links", params={"nd_song_id": "c"})).json() == {"links": [], "universal": None}

    # a catalog track that is not in the library
    r = (await client.get("/api/links", params={"ref": "dz:999"})).json()
    assert [x["service"] for x in r["links"]] == ["deezer"] and r["universal"] == "https://song.link/d/999"

    # the import goes, the ids stay
    async with session_scope() as s:
        await s.execute(TrackLink.__table__.delete())
        await core.remove(s, await s.get(Import, import_id))
    r = (await client.get("/api/links", params={"nd_song_id": "a"})).json()
    assert [x["service"] for x in r["links"]][:2] == ["spotify", "yandex"]


async def test_a_playlist_keeps_the_dates_of_the_import_it_came_from(client):
    from datetime import datetime

    from sqlalchemy import select

    from app import playlist_dates
    from app.db import session_scope
    from app.models import Import, ImportCollection, ImportItem, User

    a, b = await add_track("a"), await add_track("b")
    async with session_scope() as s:
        user = (await s.execute(select(User))).scalars().first()
        imp = Import(user_id=user.id, source="spotify", method="csv", status="preview")
        s.add(imp)
        await s.flush()
        coll = ImportCollection(import_id=imp.id, kind="playlist", external_id="csv:japan", title="japan")
        s.add(coll)
        await s.flush()
        s.add(ImportItem(import_id=imp.id, collection_id=coll.id, external_id="x", track_id=a,
                         raw_json={"liked_at": "2024-09-08T12:51:22Z"}))
        await playlist_dates.collect(s, user.id, "pl1", "JAPAN")  # the same name, any case
        await playlist_dates.stamp(s, "pl1", [a, b])  # `a` keeps the service's date
        got = await playlist_dates.of(s, "pl1")
        assert got[a] == datetime(2024, 9, 8, 12, 51, 22) and got[b].year >= 2025
        assert await playlist_dates.of(s, "other") == {}
