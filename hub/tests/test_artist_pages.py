"""An artist's page kept: shown at once, put together again behind it, completed by the warming."""

from __future__ import annotations

from app import artist_pages
from app.api import catalog
from app.catalog.models import ArtistPage, CatalogAlbum, CatalogArtist
from app.db import session_scope
from app.library import artists
from app.models import Artist, ArtistRef
from tests.test_flow import env as env
from tests.test_flow import src_mp3 as src_mp3


async def _artist_with_page(monkeypatch, name="Кино"):
    asked: list[str] = []
    album = CatalogAlbum(ref="dz:500", source="dz", id="500", title="Группа крови", artist=name, record_type="album")

    async def page(ref):
        asked.append(ref)
        return ArtistPage(artist=CatalogArtist(ref=ref, source="dz", id=ref[3:], name=name), albums=[album])

    async def none(*args, **kwargs):
        return None

    monkeypatch.setattr(catalog, "get_artist_page", page)
    monkeypatch.setattr(catalog, "spotify_profile", none)
    monkeypatch.setattr(catalog, "_tie_yandex", none)
    async with session_scope() as s:
        who = Artist(name=name)
        s.add(who)
        await s.flush()
        s.add(ArtistRef(ref="dz:1", artist_id=who.id))
        ident = who.id
    return ident, asked


async def test_the_second_opening_asks_no_catalog(env, monkeypatch):
    ident, asked = await _artist_with_page(monkeypatch)
    client = env["client"]
    first = (await client.get(f"/api/catalog/artist/{ident}")).json()
    assert [a["title"] for a in first["albums"]] == ["Группа крови"]
    n = len(asked)
    assert n
    again = (await client.get(f"/api/catalog/artist/{ident}")).json()
    assert again["albums"] == first["albums"] and again["artist"]["name"] == "Кино"
    assert len(asked) == n  # the kept page, no catalog asked
    kept = await artist_pages.read(ident)
    assert kept is not None and kept.complete and not kept.stale


async def test_a_page_not_theirs_any_more_is_put_together_again(env, monkeypatch):
    ident, asked = await _artist_with_page(monkeypatch)
    client = env["client"]
    await client.get(f"/api/catalog/artist/{ident}")
    n = len(asked)
    async with session_scope() as s:
        await artists.tie_profile(s, ident, "dz:2")  # another record of theirs
    await client.get(f"/api/catalog/artist/{ident}")
    assert len(asked) > n


async def test_with_the_warming_the_first_page_does_not_wait_for_the_search_and_is_completed(env, monkeypatch):
    ident, asked = await _artist_with_page(monkeypatch)
    searched: list[bool] = []
    whole = catalog.whole_artist

    async def watched(s, ref, **kwargs):
        searched.append(kwargs.get("tie", True))
        return await whole(s, ref, **kwargs)

    monkeypatch.setattr(catalog, "whole_artist", watched)
    monkeypatch.setattr(artist_pages, "_running", True)
    told: list[dict] = []
    from app.events import bus

    monkeypatch.setattr(bus, "broadcast", lambda event, data: told.append({"event": event, **data}))
    artist_pages._hot.clear()
    client = env["client"]
    await client.get(f"/api/catalog/artist/{ident}")
    assert searched == [False]
    kept = await artist_pages.read(ident)
    assert kept is not None and not kept.complete and kept.stale
    assert list(artist_pages._hot) == [str(ident)]  # asked of the warming
    assert await artist_pages._wanted(str(ident), artist_pages.FRESH_S)
    await artist_pages.refresh(artist_pages._hot.popleft())
    assert searched == [False, True]
    kept = await artist_pages.read(ident)
    assert kept is not None and kept.complete and not kept.stale
    assert told and told[-1]["event"] == "artist" and told[-1]["id"] == ident
    assert not await artist_pages._wanted(str(ident), artist_pages.FRESH_S)
    n = len(asked)
    await client.get(f"/api/catalog/artist/{ident}")
    assert len(asked) == n and not artist_pages._hot  # fresh: nothing asked


async def test_the_library_is_warmed_missing_pages_first(env, monkeypatch):
    ident, _ = await _artist_with_page(monkeypatch)
    async with session_scope() as s:
        other = Artist(name="Аквариум")
        s.add(other)
        await s.flush()
        s.add(ArtistRef(ref="dz:9", artist_id=other.id))
        other_id = other.id
    assert set(await artist_pages._candidates()) >= {ident, other_id}
    await artist_pages.refresh(str(ident))
    left = await artist_pages._candidates()
    assert other_id in left and ident not in left  # fresh for `REFRESH_S`


async def test_the_player_asks_for_artists(env):
    artist_pages._hot.clear()
    r = await env["client"].post("/api/catalog/artists/warm", json={"refs": ["12", "dz:3", "12"]})
    assert r.status_code == 204
    assert list(artist_pages._hot) == ["12", "dz:3"]
    artist_pages._hot.clear()
