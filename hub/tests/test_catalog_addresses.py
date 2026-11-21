import json

from fastapi import HTTPException
from sqlalchemy import func, select

from app.api import catalog, search
from app.catalog import release_matches
from app.catalog.models import ArtistPage, CatalogArtist, SearchResults
from app.db import session_scope
from app.library import artists
from app.models import Artist, CatalogRelease, Track
from app.services import svc
from tests.test_flow import download_one
from tests.test_flow import env as env
from tests.test_flow import src_mp3 as src_mp3
from tests.test_release_matches import release


async def test_search_then_open_artist_and_keep_its_address_without_deezer(env, monkeypatch):
    dz = CatalogArtist(ref="dz:71", source="dz", id="71", name="New Artist")
    sp = CatalogArtist(ref="sp:artist", source="sp", id="artist", name="New Artist")
    pages = {a.ref: ArtistPage(artist=a) for a in (dz, sp)}

    async def provider_page(ref):
        if ref == dz.ref and svc.deezer is None:
            raise HTTPException(404, "Deezer is off")
        return pages[ref]

    async def no_new_spotify(*args):
        return None

    monkeypatch.setattr(catalog, "get_artist_page", provider_page)
    monkeypatch.setattr(catalog, "spotify_profile", no_new_spotify)
    async with session_scope() as s:
        before = await s.scalar(select(func.count(Artist.id)))
        found, _, _ = await search._catalog_matches(s, [SearchResults(artists=[dz])], 1, dz.name, complete=True)
        assert found[0].hub_artist_id is None
        assert await s.scalar(select(func.count(Artist.id))) == before
    client = env["client"]
    first = (await client.get("/api/catalog/artist/dz:71")).json()
    ident = first["artist"]["hub_artist_id"]
    assert ident
    async with session_scope() as s:
        await artists.tie_profile(s, ident, sp.ref)
        old = await artists.who(s, "Old duplicate spelling")
        await artists.merge(s, None, await artists.by_id(s, ident), old)
        old_id = old.id
    monkeypatch.setattr(svc, "deezer", None)
    for address in ("dz:71", str(ident), str(old_id)):
        result = await client.get(f"/api/catalog/artist/{address}")
        assert result.status_code == 200, result.text
        assert result.json()["artist"]["hub_artist_id"] == ident
        assert result.json()["artist"]["ref"] == sp.ref


async def test_releases_keep_addresses_after_confirmation_and_catalog_shutdown(env, monkeypatch):
    dz = release("dz", "71", "Dummy Romance", "Dummy Romance")
    sp = release("sp", "new", "ダミーロマンス", "ダミーロマンス")
    expanded = release("ym", "expanded", "Dummy Romance", "Dummy Romance", count=3)
    albums = {a.ref: a for a in (dz, sp, expanded)}

    async def provider_album(ref, **kwargs):
        if ref == dz.ref and svc.deezer is None:
            raise HTTPException(404, "Deezer is off")
        return albums[ref]

    async def no_links(album, progress=None):
        return []

    async def cover(album):
        return bytes(768)

    client = env["client"]
    monkeypatch.setattr(catalog, "get_album", provider_album)
    monkeypatch.setattr(catalog, "album_links", no_links)
    monkeypatch.setattr(release_matches, "_cover", cover)
    monkeypatch.setattr(svc, "yandex", object())
    async with session_scope() as s:
        _, out, _ = await search._catalog_matches(s, [SearchResults(albums=[dz])], 1, dz.title, complete=True)
        assert out[0].release_id is None
        assert not await s.scalar(select(func.count(CatalogRelease.id)))

    async def open_album(ref):
        r = await client.get(f"/api/catalog/album/{ref}")
        assert r.status_code == 200, r.text
        return r.json()

    first, second, third = [(await open_album(row.ref))["release_id"] for row in (dz, sp, expanded)]
    assert len({first, second, third}) == 3
    async with session_scope() as s:
        await release_matches.discover(s, albums.values(), lambda a: provider_album(a.ref))
    monkeypatch.setattr(svc, "deezer", None)
    for addr in (dz.ref, sp.ref, str(first), str(second)):
        opened = await open_album(addr)
        assert opened["release_id"] == first
        assert opened["ref"] == sp.ref
        assert dz.title in opened["alternate_titles"]
    assert (await open_album(expanded.ref))["release_id"] == third


async def test_owned_track_plays_by_hub_id_with_deezer_off(env, monkeypatch):
    client = env["client"]
    await download_one(client, {"artist": "Кино", "title": "Группа крови"})
    async with session_scope() as s:
        track = await s.scalar(select(Track).where(Track.ref == "dz:100"))
        ident = track.id
    monkeypatch.setattr(svc, "deezer", None)
    for ref in (f"lib:{ident}", "dz:100"):
        resolved = await client.get("/api/listen", params={"ref": ref})
        assert resolved.status_code == 200, resolved.text
        choice = resolved.json()
        assert (choice["kind"], choice["track_id"]) == ("library", ident)
        media = await client.get(choice["stream_url"], headers={"Range": "bytes=0-31"})
        assert media.status_code == 206 and len(media.content) == 32


async def test_artist_progress_reports_each_catalog(env, monkeypatch):
    from app.models import ArtistRef

    class Spotify:
        async def artist_page(self, id_):
            return ArtistPage(artist=CatalogArtist(ref=f"sp:{id_}", source="sp", id=id_, name="Кино"))

    async def unavailable(_id):
        raise RuntimeError("offline")

    async with session_scope() as s:
        holder = Artist(name="Кино")
        s.add(holder)
        await s.flush()
        ident = holder.id
        s.add_all([ArtistRef(ref="sp:kino", artist_id=ident), ArtistRef(ref="dz:1", artist_id=ident)])
    monkeypatch.setattr(svc, "spotify", Spotify())
    monkeypatch.setattr(svc.deezer, "get_artist_page", unavailable)

    response = await env["client"].get(f"/api/catalog/artist/{ident}", headers={"Accept": "application/x-ndjson"})
    assert response.status_code == 200
    events = [json.loads(line) for line in response.text.splitlines()]
    states = [(e["data"]["service"], e["data"]["state"]) for e in events if e["type"] == "source"]
    assert set(states) == {("deezer", "searching"), ("deezer", "error"), ("spotify", "searching"), ("spotify", "ready")}
    for service, final in [("deezer", "error"), ("spotify", "ready")]:
        assert states.index((service, "searching")) < states.index((service, final))
    assert events[-1]["type"] == "done"
    assert events[-1]["data"]["artist"]["hub_artist_id"] == ident


async def test_import_and_recommendations_resolve_with_deezer_disabled(env, monkeypatch):
    from app.catalog.models import CatalogTrack
    from app.imports.resolve import catalog_track
    from app.mixes.resolve import resolve_hit
    from app.mixes.sources import Hit

    track = CatalogTrack(ref="sp:single", id="single", source="sp", title="Здесь и сейчас", artist="Исполнитель",
                         duration_s=180)

    async def search(query, **kwargs):
        assert query == f"{track.artist} {track.title}"
        return SearchResults(tracks=[track])

    monkeypatch.setattr(svc, "deezer", None)
    monkeypatch.setattr(svc.spotify, "search", search)
    from_import = await catalog_track({"artist": track.artist, "title": track.title, "duration_s": 180})
    assert (from_import.match, from_import.ref) == ("exact", track.ref)
    from_mix = await resolve_hit("lastfm", Hit(artist=track.artist, title=track.title, duration_s=180))
    assert from_mix == track
