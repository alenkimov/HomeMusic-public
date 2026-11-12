"""«Поделиться ссылкой»: the service ids imports and mixes have seen become the track's own."""

from __future__ import annotations

import uuid

import httpx
import pytest
import respx

from tests.support import app_client, filed_track


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

    async with session_scope() as s:
        t = filed_track(uid=str(uuid.uuid4()), path=f"Кино/{nd}.mp3", nd_song_id=nd, artist="Кино", title=nd,
                  fingerprint=fingerprint("Кино", nd), **kw)
        s.add(t)
        await s.flush()
        return t.id


async def test_ids_come_from_imports_and_the_source_map_and_stay(client):
    from sqlalchemy import select

    from app.db import session_scope
    from app.imports import core
    from app.models import Import, ImportCollection, ImportItem, SourceMap, TrackLink, User

    a = await add_track("a", ref="dz:111", source_url="https://www.youtube.com/watch?v=D3gOdoFWCdY")
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
    assert (r["links"], r["universal"]) == ([{"service": "soundcloud", "title": "SoundCloud", "url": "https://soundcloud.com/x/y"}], None)
    r = (await client.get("/api/links", params={"nd_song_id": "c"})).json()
    assert (r["links"], r["universal"]) == ([], None)

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


async def test_a_homemusic_link_names_the_track_and_is_found_in_another_library(client, monkeypatch):
    from urllib.parse import parse_qs, urlsplit

    from app.db import session_scope
    from app.models import TrackLink

    tid = await add_track("Группа крови", ref="dz:100", isrc="RUA1D8700001", album="Группа крови")
    plain = await add_track("Своё")  # an uploaded file: nothing but its names
    async with session_scope() as s:
        s.add(TrackLink(track_id=tid, service="spotify", external_id="spK1234567"))

    r = (await client.get("/api/links", params={"nd_song_id": "Группа крови"})).json()
    url = urlsplit(r["homemusic"])
    assert f"{url.scheme}://{url.netloc}{url.path}" == "https://alenkimov.github.io/HomeMusic-public/"
    assert url.query == ""  # all of it is after the `#`: no server ever sees which track
    assert {k: v[0] for k, v in parse_qs(url.fragment).items()} == {
        "dz": "100", "isrc": "RUA1D8700001", "sp": "spK1234567", "a": "Кино", "t": "Группа крови", "al": "Группа крови"}
    r = (await client.get("/api/links", params={"nd_song_id": "Своё"})).json()
    assert parse_qs(urlsplit(r["homemusic"]).fragment) == {"a": ["Кино"], "t": ["Своё"]} and r["links"] == []
    # a catalog track the library has not got: its catalog id is enough
    assert (await client.get("/api/links", params={"ref": "dz:777"})).json()["homemusic"].endswith("/#dz=777")

    # opened here: by the catalog id, the Spotify id, the ISRC, then by the names
    async def found(**q) -> str | None:
        return (await client.get("/api/links/open", params=q)).json()["nd_song_id"]

    assert await found(dz="100") == await found(sp="spK1234567") == await found(isrc="rua1d8700001") == "Группа крови"
    assert await found(a="КИНО", t="своё") == "Своё" and plain
    assert await found(dz="100", a="Кино", t="Своё") == "Группа крови"  # an id outweighs the names

    # not here: the catalog knows it — its album page; or nothing does — the words to search by
    from app.catalog.models import CatalogTrack
    from app.services import svc

    async def get_track(id_):
        if str(id_) != "555":
            raise LookupError("no such track")
        return CatalogTrack(ref="dz:555", source="dz", id="555", title="Пачка сигарет", artist="Кино", album_ref="dz:9")

    monkeypatch.setattr(svc.deezer, "get_track", get_track)
    r = (await client.get("/api/links/open", params={"dz": "555"})).json()
    assert (r["nd_song_id"], r["ref"], r["album_ref"], r["query"]) == (None, "dz:555", "dz:9", "Кино Пачка сигарет")
    r = (await client.get("/api/links/open", params={"dz": "556", "a": "Кто-то", "t": "Что-то"})).json()
    assert (r["nd_song_id"], r["ref"], r["query"]) == (None, None, "Кто-то Что-то")
    assert (await client.get("/api/links/open", params={"dz": "abc"})).status_code == 422

    # a fork without the page: no such links
    monkeypatch.setenv("SHARE_PAGE_URL", "")
    from app.config import get_settings

    get_settings.cache_clear()
    assert (await client.get("/api/links", params={"nd_song_id": "Своё"})).json()["homemusic"] is None
    get_settings.cache_clear()
