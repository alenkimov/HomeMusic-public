from datetime import date

import pytest

from app.api import catalog, search
from app.catalog import release_matches
from app.catalog.models import CatalogAlbum, CatalogTrack, SearchResults
from app.db import dispose, session_scope
from app.library import album_names, artists
from app.library.tracks import of_catalog
from app.models import Album
from tests.test_flow import env as env
from tests.test_flow import src_mp3 as src_mp3


def release(source, id_, name, song, *, count=1, day=date(2025, 8, 22), duration=161, upc=None, isrc=None):
    ref = f"{source}:{id_}"
    return CatalogAlbum(
        source=source, id=id_, ref=ref, title=name, artist="TAK", artist_ref=f"{source}:artist",
        record_type="single", release_date=day, nb_tracks=count, upc=upc,
        tracks=[CatalogTrack(source=source, id=f"{id_}t", ref=f"{ref}t", title=song, artist="TAK",
                             album_ref=ref, album_title=name, duration_s=duration, track_no=1, isrc=isrc)],
    )


@pytest.fixture
async def db(hub_env):
    yield
    await dispose()


async def test_localized_release_and_track_names_survive_catalog_results(db, monkeypatch):
    deezer = release("dz", "original", "CHUKICHUKI POKOPOKO-PON", "CHUKICHUKI POKOPOKO-PON", isrc="JP92Q2502623")
    spotify = release("sp", "localized", "ちゅきちゅきポコポコポン", "ちゅきちゅきポコポコポン (feat. 初音ミク)")

    async def cover(album):
        return bytes([120] * 768)

    async def unexpected_details(ref):
        pytest.fail(f"Already have details of {ref}")

    monkeypatch.setattr(release_matches, "_cover", cover)
    monkeypatch.setattr(catalog, "get_album", unexpected_details)

    async with session_scope() as s:
        local = await of_catalog(s, spotify.tracks[0])
        album = Album(uid="local", title=spotify.title, album_artist=spotify.artist)
        s.add(album)
        await s.flush()
        local.album_id = album.id
        local_id, album_id = local.id, album.id
    async with session_scope() as s:
        discography = await catalog._artist_albums(s, [spotify, deezer])
        assert list(discography) == [spotify.ref]
        combined = discography[spotify.ref][0]
        assert combined.alternate_titles == [deezer.title]
        assert combined.tracks[0].alternate_titles == [deezer.tracks[0].title]
    async with session_scope() as s:
        assert (await of_catalog(s, deezer.tracks[0])).id == local_id
        assert [row.name for row in (await album_names.listed(s, [album_id]))[album_id]] == [deezer.title]
        hits = await artists.found(s, deezer.tracks[0].title, among=[local_id])
        assert [row.id for row, _ in hits] == [local_id]
        for title in (deezer.title, spotify.title):
            _, albums, tracks = await search._catalog_matches(s, [], 1, title, complete=True)
            assert [(a.ref, a.found_in, a.library_album_id) for a in albums] == [(spotify.ref, ["spotify", "deezer"], album_id)]
            assert [(t.ref, t.library_track_id) for t in tracks] == [(spotify.tracks[0].ref, local_id)]


async def test_distinct_editions_and_conflicting_recording_codes(db, monkeypatch):
    original = release("dz", "original", "Rejected Diva", "Rejected Diva", upc="4550756199610", isrc="AAA000000001")
    others = [
        release("ym", "expanded", "Rejected Diva", "Rejected Diva", count=3),
        release("sp", "later", "Rejected Diva", "Rejected Diva", day=date(2025, 9, 1)),
        release("ym", "different-code", "Rejected Diva", "Rejected Diva", isrc="AAA000000002"),
        release("sp", "different-barcode", "Rejected Diva", "Rejected Diva", upc="4550756199627"),
    ]

    async def unexpected_cover(album):
        pytest.fail(f"Known distinct release needs no cover check: {album.ref}")

    monkeypatch.setattr(release_matches, "_cover", unexpected_cover)
    async with session_scope() as s:
        for other in others:
            groups = await release_matches.discover(s, [original, other], unexpected_cover)
            assert groups == {}
        _, albums, _ = await search._catalog_matches(
            s, [SearchResults(albums=[original, *others[:2], others[-1]])], 1, "Rejected Diva", complete=True,
        )
        assert {a.ref for a in albums} == {original.ref, others[0].ref, others[1].ref, others[-1].ref}


async def test_catalog_album_names_can_be_edited_before_it_has_files(env, monkeypatch):
    spotify = release("sp", "album", "Dummy Romance", "Dummy Romance")

    async def get_album(ref, **kwargs):
        assert ref == spotify.ref
        return spotify

    async def album_links(release, progress=None):
        return []

    monkeypatch.setattr(catalog, "get_album", get_album)
    monkeypatch.setattr(catalog, "album_links", album_links)
    client = env["client"]
    edit_path = "/api/library/album-edit"
    target = {"ref": spotify.ref}
    initial = (await client.get(edit_path, params=target)).json()
    assert (initial["album_id"], initial["title"]) == (None, spotify.title)
    made = await client.post(f"{edit_path}/names", params=target, json={"name": "ダミーロマンス"})
    assert made.status_code == 200, made.text
    renamed = await client.patch(edit_path, params=target, json={"title": "ダミーロマンス"})
    assert renamed.status_code == 200, renamed.text
    saved = renamed.json()
    assert saved["album_id"] == made.json()["album_id"]
    assert saved["names"] == [{"id": None, "name": spotify.title, "source": "catalog"}]
    assert (await client.get("/api/catalog/album/sp:album")).json()["title"] == "ダミーロマンス"
    local_target = {"album_id": saved["album_id"]}
    assert (await client.get(edit_path, params=local_target)).json() == saved
    added = (await client.post(f"{edit_path}/names", params=local_target, json={"name": "Другая романтика"})).json()
    manual = next(name for name in added["names"] if name["source"] == "manual")
    hits = (await client.get("/api/library/albums", params={"q": manual["name"]})).json()
    assert [a["id"] for a in hits] == [saved["album_id"]]
    dropped = await client.delete(f"{edit_path}/names/{manual['id']}", params=local_target)
    assert dropped.json() == saved
