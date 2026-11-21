"""A transferred track is the catalog's track it was matched with: who it is by is the catalog's word, each name
with its own record — not the service's names beside the catalog's records."""

import pytest

from app import db
from app.catalog.models import CatalogTrack
from app.imports import apply
from app.jobs.downloads import complete_meta
from app.library import artists
from app.library import tracks as registry
from app.mixes.resolve import remember_track
from app.models import DownloadItem, Import, ImportCollection, ImportItem, Track, User
from app.security import encrypt
from app.services import svc

CATALOG = CatalogTrack(ref="dz:7", source="dz", id="7", title="Песня", artist="LSP", artists=["LSP", "Гость"],
                       artist_refs=["dz:900", "dz:901"], artist_ref="dz:900", duration_s=200)
ROW = {"artist": "Гость, ЛСП", "artists": ["Гость", "ЛСП"], "title": "Песня (как в сервисе)", "duration_s": 201}


class Deezer:
    async def get_track(self, id_):
        return CATALOG

    async def get_album(self, id_):
        raise AssertionError("the track names no album")


@pytest.fixture
async def env(hub_env, monkeypatch):
    db.init_engine()
    monkeypatch.setattr(svc, "deezer", Deezer())
    try:
        async with db.session_scope() as s:
            user = User(nd_username="qa", nd_password_enc=encrypt("qa-password"))
            s.add(user)
            await s.flush()
            imp = Import(user_id=user.id, source="spotify", method="link", mode="full", status="running")
            s.add(imp)
            await s.flush()
            coll = ImportCollection(import_id=imp.id, kind="playlist", external_id="p", title="p")
            s.add(coll)
            await s.flush()
            it = ImportItem(import_id=imp.id, collection_id=coll.id, external_id="1", raw_json=ROW, ref="dz:7",
                            match="exact", status="pending")
            s.add(it)
            await s.flush()
            ids = (user.id, imp.id, it.id)
        yield ids
    finally:
        await db.dispose()


async def test_a_download_is_by_whom_the_catalog_says(env):
    # the service's names, in the service's order, do not take the places of the catalog's beside its records
    meta = await complete_meta({"track_ref": "dz:7", "deezer_track_id": "7", "artists": ROW["artists"]})
    assert (meta["artists"], meta["artist_refs"]) == (["LSP", "Гость"], ["dz:900", "dz:901"])


def test_records_beside_other_names_say_nothing():
    # one name by two records (a service's «GONE.Fludd» by the catalog's Iroh and GONE.Fludd): nobody is given the
    # first record
    assert artists.beside(["GONE.Fludd"], ["dz:1", "dz:2"]) == {}
    assert artists.beside(["Iroh", "GONE.Fludd"], ["dz:1", None]) == {artists.key("Iroh"): "dz:1"}
    assert artists.refs_of({"artists": ["GONE.Fludd"], "artist_refs": ["dz:1", "dz:2"], "artist": "Iroh", "artist_ref": "dz:1"}) == {artists.key("Iroh"): "dz:1"}


async def test_a_matched_row_is_downloaded_as_the_catalogs_track(env):
    user_id, imp_id, item_id = env
    await remember_track(CATALOG)
    async with db.session_scope() as s:
        assert await apply.queue_track(s, await s.get(Import, imp_id), await s.get(ImportItem, item_id), "playlist")
    async with db.session_scope() as s:
        qj = (await s.get(DownloadItem, (await s.get(ImportItem, item_id)).download_item_id)).query_json
    assert (qj["query"]["artist"], qj["query"]["title"]) == ("LSP", "Песня")
    assert (qj["meta"]["artists"], qj["meta"]["artist_refs"]) == (["LSP", "Гость"], ["dz:900", "dz:901"])


async def test_a_row_with_no_record_kept_leaves_who_it_is_by_to_the_catalog(env):
    user_id, imp_id, item_id = env
    async with db.session_scope() as s:
        assert await apply.queue_track(s, await s.get(Import, imp_id), await s.get(ImportItem, item_id), "playlist")
    async with db.session_scope() as s:
        qj = (await s.get(DownloadItem, (await s.get(ImportItem, item_id)).download_item_id)).query_json
    assert qj["meta"] == {"track_ref": "dz:7", "deezer_track_id": "7"}


async def test_the_track_of_a_row_is_named_by_the_catalog(env):
    user_id, _, item_id = env
    await remember_track(CATALOG)
    async with db.session_scope() as s:
        track = await apply.track_of(s, await s.get(ImportItem, item_id), user_id)
        assert (track.artist, track.title) == ("LSP", "Песня")
        assert artists.refs_of(track.catalog) == {artists.key("LSP"): "dz:900", artists.key("Гость"): "dz:901"}


async def test_the_services_words_are_not_kept_as_the_catalogs(env):
    user_id, _, item_id = env
    async with db.session_scope() as s:
        track = await apply.track_of(s, await s.get(ImportItem, item_id), user_id)
        assert track.title == "Песня (как в сервисе)" and track.catalog is None
        track_id = track.id
    async with db.session_scope() as s:  # the catalog is asked later: the track keeps its word
        assert await registry.fill_catalog(s) == 1
    async with db.session_scope() as s:
        track = await s.get(Track, track_id)
        assert artists.refs_of(track.catalog)[artists.key("LSP")] == "dz:900"
        assert await registry.fill_catalog(s) == 0
