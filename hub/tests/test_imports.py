"""Imports through the HTTP API: preview → matching → ♥ and playlists in the service's order → downloads below the
user's work. The service is a ready snapshot (its parsing: test_import_yandex.py), the catalog is FakeDeezer,
Navidrome a mock that keeps playlists and the order of stars. Downloads are not run (no ffmpeg needed): the tests
look at what is queued and play its end by hand.
"""

from __future__ import annotations

import asyncio
import uuid

import httpx
import pytest
import respx
from sqlalchemy import select

from tests.support import FakeDeezer, FakeProvider, app_client, by_songs, cand, filed_track, tids


def track(artist: str, title: str, duration_s: int | None = 200, **extra) -> dict:
    return {"artist": artist, "artists": [artist], "title": title, "duration_s": duration_s, **extra}


OSEN = track("ДДТ", "Осень", 260)  # in the library (ndB)
KROVI = track("Кино", "Группа крови", 245)  # in the catalog (dz:100)
GOROD = track("Аквариум", "Город золотой", 190)  # in the library (ndA)
NONE = track("Неизвестный", "Нет такого", 180)  # nowhere


def snapshot():
    from app.imports.sources.base import SnapCollection, SnapItem, Snapshot

    return Snapshot(account="ivan", collections=[
        # newest like first, as Yandex lists them: Город золотой is the oldest
        SnapCollection("likes", "likes", "Мне нравится", [
            SnapItem("2", OSEN), SnapItem("1", KROVI), SnapItem("3", GOROD)]),
        SnapCollection("playlist", "77:1001", "Дорога", [
            SnapItem("1", KROVI), SnapItem("3", GOROD), SnapItem("9", NONE), SnapItem("2", OSEN)]),
        SnapCollection("albums", "albums", "Альбомы", [SnapItem("500", {"artist": "Кино", "title": "Группа крови"})]),
        SnapCollection("artists", "artists", "Исполнители", [SnapItem("600", {"name": "Кино"})]),
        SnapCollection("dislikes", "dislikes", "Не нравится", [SnapItem("8", track("Кто-то", "Что-то"))]),
    ])


@pytest.fixture
async def env(hub_env, monkeypatch):
    from app.imports.sources import yandex
    from app.main import create_app
    from app.services import svc

    state: dict = {"playlists": {}, "stars": [], "star_log": [], "fetches": []}

    def nd_handler(request: httpx.Request) -> httpx.Response:
        ep = request.url.path.rsplit("/", 1)[-1]
        p = request.url.params
        ok = {"status": "ok", "version": "1.16.1"}

        def reply(**body) -> httpx.Response:
            return httpx.Response(200, json={"subsonic-response": {**ok, **body}})

        if ep == "getUser":
            return reply(user={"adminRole": True})
        if ep == "createPlaylist":
            pid = p.get("playlistId") or f"pl{len(state['playlists']) + 1}"
            name = p.get("name") or state["playlists"].get(pid, {}).get("name")
            state["playlists"][pid] = {"name": name, "songs": p.get_list("songId")}  # with an id: replaces
            return reply(playlist={"id": pid, "name": name})
        if ep == "getPlaylist":
            pl = state["playlists"].get(p["id"])
            if pl is None:
                return reply()
            return reply(playlist={"id": p["id"], "name": pl["name"], "songCount": len(pl["songs"]),
                                   "entry": [{"id": x, "title": x, "artist": "?"} for x in pl["songs"]]})
        if ep == "updatePlaylist":
            state["playlists"][p["playlistId"]]["songs"] += p.get_list("songIdToAdd")
            return reply()
        if ep in ("star", "unstar"):
            ids = p.get_list("id")
            state["star_log"].append((ep, ids))
            state["stars"] = [x for x in state["stars"] if x not in ids] + (ids if ep == "star" else [])
            return reply()
        if ep == "getStarred2":
            return reply(starred2={"song": [{"id": i} for i in state["stars"]]})
        return reply()

    async def fake_fetch(token, login, connection_id=None):
        state["fetches"].append((token, login))
        return state.get("snapshot") or snapshot()

    monkeypatch.setattr(yandex, "fetch", fake_fetch)
    app = create_app()
    async with app.router.lifespan_context(app):
        svc.providers = {"hitmoz": FakeProvider("hitmoz", [])}
        svc.deezer = FakeDeezer()
        with respx.mock(assert_all_called=False) as mock:
            mock.route(host="navidrome.test").mock(side_effect=nd_handler)
            async with app_client(app) as client:
                await add_library_track(GOROD, "ndA")
                await add_library_track(OSEN, "ndB")
                yield {"client": client, "state": state, "mock": mock}


async def add_library_track(raw: dict, nd_song_id: str) -> int:
    from sqlalchemy import select

    from app.db import session_scope
    from app.matching.normalize import fingerprint
    from app.models import Track, TrackFile

    async with session_scope() as s:
        # as the importer does: a file of a song the Hub knows already (it waits in a playlist) joins that track
        known = (await s.execute(select(Track).where(
            Track.fingerprint == fingerprint(raw["artist"], raw["title"])).order_by(Track.id).limit(1))).scalar_one_or_none()
        if known is not None:
            s.add(TrackFile(track_id=known.id, uid=str(uuid.uuid4()), path=f"{raw['artist']}/{raw['title']}.mp3",
                            nd_song_id=nd_song_id))
            await s.flush()
            return known.id
        t = filed_track(uid=str(uuid.uuid4()), path=f"{raw['artist']}/{raw['title']}.mp3", nd_song_id=nd_song_id,
                  artist=raw["artist"], title=raw["title"], duration_s=raw["duration_s"],
                  fingerprint=fingerprint(raw["artist"], raw["title"]))
        s.add(t)
        await s.flush()
        return t.id


async def playlist_tracks(c, name: str) -> list[tuple[str, str | None]]:
    """The Hub's playlist of this name: (title, the Navidrome song of its file or None) of its tracks, in order."""
    pid = next(x["id"] for x in (await c.get("/api/playlists")).json() if x["name"] == name)
    return [(x["title"], x["nd_song_id"]) for x in (await c.get(f"/api/playlists/{pid}")).json()["tracks"]]


async def run_imports(max_jobs: int = 50) -> None:
    """Runs the queued import jobs (not downloads), delays ignored; a job put back to wait (`RetryLater`) stops it."""
    from app.db import session_scope
    from app.jobs.queue import run_job
    from app.models import Job

    seen: set[int] = set()
    for _ in range(max_jobs):
        async with session_scope() as s:
            job = (await s.execute(select(Job).where(Job.status == "queued", Job.kind.like("import_%"))
                                   .order_by(Job.id).limit(1))).scalar_one_or_none()
            if job is None or job.id in seen:
                return
            seen.add(job.id)
            job.status, job.attempts = "running", job.attempts + 1
        await run_job(job)


async def jobs(kind: str) -> list:
    from app.db import session_scope
    from app.models import Job

    async with session_scope() as s:
        return list((await s.execute(select(Job).where(Job.kind == kind, Job.status == "queued"))).scalars())


async def prepared(c: httpx.AsyncClient) -> dict:
    r = await c.post("/api/imports", json={"source": "yandex", "method": "token", "token": "secret-token"})
    assert r.status_code == 200, r.text
    await run_imports()
    r = await c.get(f"/api/imports/{r.json()['id']}")
    assert r.json()["status"] == "preview", r.json()
    return r.json()


async def test_concurrent_import_matches_share_the_source_mapping(env, monkeypatch):
    from app.db import session_scope
    from app.imports.resolve import resolve_track
    from app.models import SourceMap
    from app.services import svc

    # Both collections miss the cache before the catalog answers; no timing sleeps.
    both_searching = asyncio.Event()
    calls = 0
    search = svc.deezer.search_tracks

    async def together(*args, **kwargs):
        nonlocal calls
        calls += 1
        if calls == 2:
            both_searching.set()
        await both_searching.wait()
        return await search(*args, **kwargs)

    monkeypatch.setattr(svc.deezer, "search_tracks", together)

    async def match():
        return await resolve_track("yandex", "same-track", KROVI)

    results = await asyncio.wait_for(asyncio.gather(match(), match()), timeout=1)
    assert [(r.match, r.ref) for r in results] == [("exact", "dz:100"), ("exact", "dz:100")]
    async with session_scope() as s:
        rows = list((await s.execute(select(SourceMap))).scalars())
        assert [(r.source, r.kind, r.external_id, r.ref) for r in rows] == [
            ("yandex", "track", "same-track", "dz:100")
        ]


async def items(c: httpx.AsyncClient, imp: dict, **params) -> list[dict]:
    r = await c.get(f"/api/imports/{imp['id']}/items", params=params)
    assert r.status_code == 200, r.text
    return r.json()["items"]


async def test_preview_counts_what_the_library_has(env):
    c = env["client"]
    imp = await prepared(c)
    assert "secret-token" not in str(imp) and imp["has_token"] and imp["account"] == "ivan"
    assert env["state"]["fetches"] == [("secret-token", None)]
    assert [(x["kind"], x["title"], x["count"], x["owned"]) for x in imp["collections"]] == [
        ("likes", "Мне нравится", 3, 2), ("playlist", "Дорога", 4, 2), ("albums", "Альбомы", 1, 0),
        ("artists", "Исполнители", 1, 0), ("dislikes", "Не нравится", 1, 0)]
    st = imp["stats"]
    # distinct tracks of likes and playlists: Осень and Город золотой are owned, the other two would be downloaded
    assert (st["tracks"], st["owned"], st["to_download"]) == (4, 2, 2)
    assert st["est_bytes"] == (245 + 180) * 40_000 and st["free_bytes"] > 0
    from app.services import svc

    assert svc.deezer.searched == 0  # nothing is matched against the catalog before the user starts


async def test_economy_import_likes_and_playlists_in_order(env):
    from app.db import session_scope
    from app.jobs.downloads import imports_done
    from app.models import DownloadItem, Import

    c, state = env["client"], env["state"]
    imp = await prepared(c)
    r = await c.post(f"/api/imports/{imp['id']}/start",
                     json={"collection_ids": [x["id"] for x in imp["collections"]], "mode": "economy"})
    assert r.status_code == 200, r.text
    await run_imports()
    imp = (await c.get(f"/api/imports/{imp['id']}")).json()
    assert imp["status"] == "done" and not imp["has_token"]  # nothing to download: done; the sign-in is gone

    rows = {(x["collection_id"], x["title"]): x for x in await items(c, imp)}
    likes, playlist = imp["collections"][0]["id"], imp["collections"][1]["id"]
    assert (rows[likes, "Группа крови"]["status"], rows[likes, "Группа крови"]["match"]) == ("waiting", "exact")
    assert rows[likes, "Группа крови"]["ref"] == "dz:100"
    assert (rows[playlist, "Нет такого"]["status"], rows[playlist, "Нет такого"]["match"]) == ("not_found", "not_found")
    assert rows[playlist, "Город золотой"]["nd_song_id"] == "ndA"
    assert rows[imp["collections"][4]["id"], "Что-то"]["status"] == "skipped"  # a dislike is only kept

    # the playlist is the Hub's: every matched track in Yandex's order, the one the library has no file of in its
    # place. ♥ — one by one from the oldest like
    # the song the catalog does not know stands in its place too: a track known by its name alone
    assert await playlist_tracks(c, "Дорога") == [("Группа крови", None), ("Город золотой", "ndA"), ("Нет такого", None), ("Осень", "ndB")]
    assert state["star_log"][-2:] == [("star", ["ndA"]), ("star", ["ndB"])]
    assert state["stars"] == ["ndA", "ndB"]
    # the liked track the library lacks shows ♥ by its catalog ref
    stars = (await c.get("/api/starred")).json()
    assert {k: sorted(stars[k]) for k in ("song_ids", "album_ids", "artist_ids", "refs")} == {
        "song_ids": ["ndA", "ndB"], "album_ids": [], "artist_ids": [], "refs": ["dz:100"]}
    assert await jobs("download_item") == []

    # listened to: downloaded now (the user's own work), quietly, ♥ by the download. The app's `start` of it
    # counts (it may play the file from its precache, which asked the stream as `X-HomeMusic-Prefetch`)
    r = await c.post("/api/plays/events", json={"events": [{
        "event_id": "s1", "ts": "2026-10-23T10:00:00Z", "kind": "start", "ref": "dz:100", "client": "app/1.6.0"}]})
    assert r.json()["accepted"] == 1
    [job] = await jobs("download_item")
    assert job.priority == 0
    [dl] = (await c.get("/api/downloads")).json()
    assert (dl["quiet"], dl["import_id"], dl["track_ref"]) == (True, imp["id"], "dz:100")
    assert {x["status"] for x in await items(c, imp) if x["title"] == "Группа крови" and x["kind"] == "track"} == {"queued"}

    # the download is over: the playlist is rewritten in order with it
    track_id = await add_library_track(KROVI, "ndC")
    async with session_scope() as s:
        item = await s.get(DownloadItem, dl["id"])
        item.status, item.track_id = "done", track_id
        await imports_done(s, [item])
    await run_imports()
    assert await playlist_tracks(c, "Дорога") == [("Группа крови", "ndC"), ("Город золотой", "ndA"), ("Нет такого", None), ("Осень", "ndB")]
    assert {x["status"] for x in await items(c, imp) if x["title"] == "Группа крови" and x["kind"] == "track"} == {"done"}
    # ♥ in Yandex's order: Группа крови was liked after Город золотой and before Осень, so Осень is starred again after it
    assert state["star_log"][-3:] == [("unstar", ["ndC", "ndB"]), ("star", ["ndC"]), ("star", ["ndB"])]
    assert state["stars"] == ["ndA", "ndC", "ndB"]

    # the user takes the ♥ off Город золотой in the app: the import leaves it alone from then on
    from app.imports.apply import sync_likes

    state["stars"].remove("ndA")
    async with session_scope() as s:
        await sync_likes(s, await s.get(Import, imp["id"]))
    assert state["stars"] == ["ndC", "ndB"]
    assert {x["status"] for x in await items(c, imp, collection_id=likes) if x["title"] == "Город золотой"} == {"skipped"}


async def test_what_waits_shows_in_its_playlist_and_extras_follow_the_tracks(env):
    from app.db import session_scope
    from app.jobs.downloads import imports_done
    from app.models import DownloadItem

    c, state = env["client"], env["state"]
    imp = await prepared(c)
    mine = (await c.post("/api/playlists", json={"name": "Моё"})).json()["id"]  # a playlist of the user's own
    await c.post(f"/api/playlists/{mine}/add", json={"track_ids": await tids("ndB")})  # Осень is in it
    tag = (await c.post("/api/tags", json={"name": "из Яндекса"})).json()["id"]
    r = await c.post(f"/api/imports/{imp['id']}/start", json={
        "collection_ids": [x["id"] for x in imp["collections"]], "mode": "economy",
        "star": True, "playlist_ids": [mine], "tag_ids": [tag]})
    assert r.status_code == 200, r.text
    await run_imports()

    # the playlist that came with the import: the track the library has no file of stands in its place
    # the song the catalog does not know stands in its place too: a track known by its name alone
    assert await playlist_tracks(c, "Дорога") == [("Группа крови", None), ("Город золотой", "ndA"), ("Нет такого", None), ("Осень", "ndB")]

    # Extra playlists get resolved tracks even before they have files.
    assert await playlist_tracks(c, "Моё") == [("Осень", "ndB"), ("Группа крови", None), ("Город золотой", "ndA"), ("Нет такого", None)]
    rows = (await c.get(f"/api/playlists/{mine}")).json()["tracks"]
    waiting_id = rows[1]["library_track_id"]
    tag_map = (await c.get("/api/tags")).json()["map"]
    assert tag not in tag_map.get(str(waiting_id), [])
    tags = await by_songs(tag_map)
    assert tag in tags["ndA"] and tag in tags["ndB"]
    assert {"ndA", "ndB"} <= set(state["stars"])
    assert (await c.post(f"/api/playlists/{mine}/remove", json={"track_ids": [rows[3]["library_track_id"]]})).status_code == 200

    # the track that waited gets its file: it gets the same; nothing is done twice
    await c.post("/api/plays/events", json={"events": [{
        "event_id": "s1", "ts": "2026-10-23T10:00:00Z", "kind": "start", "ref": "dz:100", "client": "app/1.6.0"}]})
    [dl] = (await c.get("/api/downloads")).json()
    track_id = await add_library_track(KROVI, "ndC")
    assert track_id == waiting_id
    async with session_scope() as s:
        item = await s.get(DownloadItem, dl["id"])
        item.status, item.track_id = "done", track_id
        await imports_done(s, [item])
    await run_imports()
    assert await playlist_tracks(c, "Дорога") == [("Группа крови", "ndC"), ("Город золотой", "ndA"), ("Нет такого", None), ("Осень", "ndB")]
    assert await playlist_tracks(c, "Моё") == [("Осень", "ndB"), ("Группа крови", "ndC"), ("Город золотой", "ndA")]
    assert tag in (await by_songs((await c.get("/api/tags")).json()["map"]))["ndC"] and "ndC" in state["stars"]


async def test_likes_are_downloaded_from_the_oldest(env):
    from app.catalog.models import CatalogTrack
    from app.imports.sources.base import SnapCollection, SnapItem, Snapshot
    from app.services import svc

    svc.deezer.more = [CatalogTrack(ref="dz:300", source="dz", id="300", title="Звезда по имени Солнце",
                                    artist="Кино", duration_s=225)]
    env["state"]["snapshot"] = Snapshot(account="ivan", collections=[SnapCollection("likes", "likes", "Мне нравится", [
        SnapItem("5", track("Кино", "Звезда по имени Солнце", 225)), SnapItem("1", KROVI)])])
    c = env["client"]
    imp = await prepared(c)
    await c.post(f"/api/imports/{imp['id']}/start", json={"collection_ids": [imp["collections"][0]["id"]], "mode": "full"})
    await run_imports()
    # a downloaded like lands on top of «Понравившиеся»: so the oldest goes first
    assert [d["title"] for d in reversed((await c.get("/api/downloads")).json())] == ["Группа крови", "Звезда по имени Солнце"]


async def test_full_import_downloads_in_the_background(env):
    from app.db import session_scope
    from app.models import DownloadItem

    c = env["client"]
    imp = await prepared(c)
    destination = (await c.post("/api/playlists", json={"name": "Копия"})).json()["id"]
    r = await c.post(f"/api/imports/{imp['id']}/start", json={
        "collection_ids": [x["id"] for x in imp["collections"]], "mode": "full", "keep_token": True,
        "playlist_ids": [destination]})
    assert r.status_code == 200, r.text
    await run_imports()
    imp = (await c.get(f"/api/imports/{imp['id']}")).json()
    assert imp["status"] == "running" and imp["has_token"]  # waits for the download; the sign-in is kept

    # Группа крови is in likes and in the playlist: one download, below the user's work, not shown as a download
    [job] = await jobs("download_item")
    assert job.priority < 0
    copy = (await c.get(f"/api/playlists/{destination}")).json()["tracks"]
    waiting = next(track for track in copy if track["title"] == "Группа крови")
    assert waiting["nd_song_id"] is None
    [dl] = (await c.get("/api/downloads")).json()
    assert dl["quiet"] and dl["import_id"] == imp["id"]
    async with session_scope() as s:
        download = await s.get(DownloadItem, dl["id"])
        assert download.query_json["meta"]["track_id"] == waiting["library_track_id"]
    assert {x["download_item_id"] for x in await items(c, imp) if x["title"] == "Группа крови" and x["kind"] == "track"} == {dl["id"]}
    assert imp["stats"]["downloading"] == 1
    # the liked album is only matched (download_albums is off), the playlist exists already
    [album] = await items(c, imp, collection_id=imp["collections"][2]["id"])
    assert (album["status"], album["ref"]) == ("waiting", "dz:10")

    # the user asks for the same track: no second download, it goes first in the queue and shows as a download
    r = await c.post("/api/downloads", json={"track_refs": ["dz:100"]})
    assert r.status_code == 200, r.text
    [job] = await jobs("download_item")
    assert job.priority == 0
    [dl] = (await c.get("/api/downloads")).json()
    assert not dl["quiet"]


async def test_second_import_reuses_the_matches(env):  # two whole imports: ~2x a scenario
    from app.services import svc

    c = env["client"]
    searched = []
    for _ in range(2):
        imp = await prepared(c)
        await c.post(f"/api/imports/{imp['id']}/start",
                     json={"collection_ids": [x["id"] for x in imp["collections"]]})
        await run_imports()
        searched.append(svc.deezer.searched)
    assert searched[0] > 0 and searched[1] == searched[0]  # the next import of the same songs does not search again
    assert len((await c.get("/api/imports")).json()) == 2


async def test_not_found_track_is_picked_by_hand(env, tmp_path):
    from app.db import session_scope
    from app.imports.apply import refresh_items
    from app.jobs.downloads import imports_done
    from app.jobs.queue import run_job
    from app.models import DownloadItem, Import
    from app.services import svc

    c = env["client"]
    imp = await prepared(c)
    await c.post(f"/api/imports/{imp['id']}/start", json={"collection_ids": [imp["collections"][1]["id"]]})
    await run_imports()
    [row] = await items(c, imp, match="not_found")
    assert row["title"] == "Нет такого"
    r = await c.post(f"/api/imports/items/{row['id']}/pick")
    assert r.status_code == 200, r.text
    dl = (await c.get(f"/api/downloads/{r.json()['download_item_id']}")).json()
    assert (dl["title"], dl["quiet"], dl["import_id"]) == ("Нет такого", False, imp["id"])  # a visible download
    assert dl["attach_to_track_id"] == row["track_id"]
    async with session_scope() as s:
        item = await s.get(DownloadItem, dl["id"])
        assert item.query_json["pick_only"] is True
    assert (await c.post(f"/api/imports/items/{row['id']}/pick")).json() == r.json()
    [job] = await jobs("download_item")
    assert job.priority == 0
    candidate = cand("manual", "https://hitmoz.test/file.mp3", title=NONE["title"]).model_copy(
        update={"artist": NONE["artist"], "duration_s": NONE["duration_s"]})
    fixture = tmp_path / "manual.mp3"
    fixture.write_bytes(b"selected audio fixture")
    svc.providers = {"hitmoz": FakeProvider("hitmoz", [candidate], src=fixture)}
    await run_job(job)
    found = (await c.get(f"/api/downloads/{dl['id']}")).json()
    assert found["status"] == "needs_review"
    assert [x["provider_id"] for x in found["candidates"]] == ["manual"]
    preview = await c.get("/api/listen", params={"ref": "file:hitmoz:manual"})
    assert preview.status_code == 200 and preview.json()["kind"] == "full"
    async with session_scope() as s:
        await refresh_items(s, await s.get(Import, imp["id"]))
    [unresolved] = await items(c, imp, status="failed")
    assert unresolved["id"] == row["id"]
    response = await c.post(f"/api/downloads/items/{dl['id']}/prepare", json={"candidate_ref": "file:hitmoz:manual"})
    assert response.status_code == 200, response.text
    assert (await c.post(f"/api/downloads/items/{dl['id']}/retry", json={"candidate_index": 0})).status_code == 200
    attached = await add_library_track(NONE, "ndPicked")
    assert attached == row["track_id"]
    async with session_scope() as s:
        item = await s.get(DownloadItem, dl["id"])
        item.status, item.track_id = "done", attached
        await imports_done(s, [item])
    await run_imports()
    [settled] = await items(c, imp, match="not_found")
    assert settled["status"] == "done" and settled["nd_song_id"] == "ndPicked"
    assert ("Нет такого", "ndPicked") in await playlist_tracks(c, "Дорога")


async def test_import_sync_does_not_restore_a_track_removed_by_the_listener(env):
    from app.db import session_scope
    from app.imports.apply import sync_playlists
    from app.models import Import, ImportItem

    c = env["client"]
    imp = await prepared(c)
    collection = imp["collections"][1]["id"]
    await c.post(f"/api/imports/{imp['id']}/start", json={"collection_ids": [collection]})
    await run_imports()
    pid = next(p["id"] for p in (await c.get("/api/playlists")).json() if p["name"] == "Дорога")
    second = (await c.get(f"/api/playlists/{pid}")).json()["tracks"][1]["library_track_id"]
    assert (await c.post(f"/api/playlists/{pid}/remove", json={"track_ids": [second]})).status_code == 200
    async with session_scope() as s:
        s.add(ImportItem(import_id=imp["id"], collection_id=collection, external_id="new",
                         position=4, raw_json={"title": "Новый трек", "artist": "Исполнитель"},
                         match="not_found", status="not_found"))
        await sync_playlists(s, await s.get(Import, imp["id"]))
    assert await playlist_tracks(c, "Дорога") == [
        ("Группа крови", None), ("Нет такого", None), ("Осень", "ndB"), ("Новый трек", None),
    ]


async def test_review_from_playlists_and_liked_survives_reload(env):
    from app.db import session_scope
    from app.models import ImportItem

    c = env["client"]
    extra = (await c.post("/api/playlists", json={"name": "Ещё один"})).json()
    imp = await prepared(c)
    likes, playlist = [x["id"] for x in imp["collections"][:2]]
    started = await c.post(f"/api/imports/{imp['id']}/start",
                           json={"collection_ids": [likes, playlist], "playlist_ids": [extra["id"]], "star": True})
    assert started.status_code == 200, started.text
    await run_imports()
    detail = (await c.get(f"/api/imports/{imp['id']}")).json()
    dest = {x["name"]: x["playlist_id"] for x in detail["destinations"]}
    assert dest == {"Дорога": detail["collections"][1]["playlist_id"], "Ещё один": extra["id"], "Любимое": None}

    playlist_url = f"/api/playlists/{dest['Дорога']}"
    async with session_scope() as s:
        rows = (await s.scalars(select(ImportItem).where(ImportItem.import_id == imp["id"],
                                                        ImportItem.external_id == "1"))).all()
        assert {x.collection_id for x in rows} == {likes, playlist}
        for row in rows:
            row.match = "probable"
            row.raw_json = {**row.raw_json, "duration_s": 255}

    async def entry(url, key, title=KROVI["title"]):
        r = await c.get(url)
        assert r.status_code == 200, r.text
        return next(x for x in r.json()[key] if x["title"] == title)

    # the doubt is a question of «Наведение порядка» (`test_a_transfers_doubt_is_a_question_of_tidy`)
    (p,) = (await c.get("/api/tidy/proposals", params={"section": "imports"})).json()
    assert p["show"]["source"] == "yandex" and p["show"]["duration_s"] == 255
    assert (await c.post(f"/api/tidy/proposals/{p['id']}/accept")).status_code == 200
    assert all(row["match"] == "probable" for row in await items(c, imp, match="probable"))
    assert (await entry(playlist_url, "tracks", NONE["title"]))["unknown"]

    liked_at = (await entry("/api/nd/liked", "songs"))["added_at"]
    assert (await c.delete(f"/api/imports/{imp['id']}")).status_code == 200
    assert (await entry(playlist_url, "tracks"))["title"] == KROVI["title"]
    assert (await entry("/api/nd/liked", "songs"))["added_at"] == liked_at


async def test_unliking_a_waiting_track(env):
    c = env["client"]
    imp = await prepared(c)
    await c.post(f"/api/imports/{imp['id']}/start", json={"collection_ids": [imp["collections"][0]["id"]]})
    await run_imports()
    assert (await c.get("/api/starred")).json()["refs"] == ["dz:100"]
    r = await c.post("/api/star", json={"refs": ["dz:100"], "unstar": True})
    assert r.status_code == 200, r.text
    assert (await c.get("/api/starred")).json()["refs"] == []


async def test_sign_in_by_code(env):
    c, mock = env["client"], env["mock"]
    mock.post("https://oauth.yandex.ru/device/code").mock(return_value=httpx.Response(200, json={
        "device_code": "dc", "user_code": "abcd1234", "verification_url": "https://ya.ru/device", "interval": 5,
        "expires_in": 300}))
    token = mock.post("https://oauth.yandex.ru/token")
    token.mock(return_value=httpx.Response(400, json={"error": "authorization_pending"}))
    r = await c.post("/api/imports", json={"method": "device"})
    assert r.status_code == 200, r.text
    imp = r.json()
    assert imp["status"] == "auth" and imp["auth"]["user_code"] == "abcd1234"
    assert imp["auth"]["kind"] == "device" and "device_code" not in imp["auth"]  # the device code stays in the Hub
    await run_imports()
    assert (await c.get(f"/api/imports/{imp['id']}")).json()["status"] == "auth"

    token.mock(return_value=httpx.Response(200, json={"access_token": "tok", "expires_in": 31536000}))
    await run_imports()
    imp = (await c.get(f"/api/imports/{imp['id']}")).json()
    assert imp["status"] == "preview" and imp["auth"] is None and imp["has_token"]
    assert env["state"]["fetches"] == [("tok", None)]


async def test_profile_link(env):
    c = env["client"]
    r = await c.post("/api/imports", json={"method": "profile", "url": "https://music.yandex.ru/album/1"})
    assert r.status_code == 400 and "профиль" in r.json()["detail"]
    r = await c.post("/api/imports", json={"method": "profile",
                                           "url": "https://music.yandex.ru/users/ivan/playlists/3"})
    assert r.status_code == 200, r.text
    await run_imports()
    assert env["state"]["fetches"] == [(None, "ivan")]


async def test_matching_holds_no_connection_while_the_catalog_answers(env, monkeypatch):
    from app import db
    from app.imports.resolve import resolve_track
    from app.services import svc

    held = []
    search = svc.deezer.search_tracks

    async def watched(*args, **kwargs):
        held.append([where for _, where in db._holders.values()])  # other catalogs' cache reads may run beside
        return await search(*args, **kwargs)

    monkeypatch.setattr(svc.deezer, "search_tracks", watched)
    assert (await resolve_track("yandex", "krovi", KROVI)).ref == "dz:100"
    assert held and not any("resolve_track" in where for now in held for where in now)


async def test_matching_verdicts(env):
    from app.catalog.models import CatalogTrack
    from app.catalog.lookup import CatalogUnavailable
    from app.imports.resolve import resolve_track
    from app.services import svc

    svc.deezer.more = [CatalogTrack(ref="dz:300", source="dz", id="300", title="Звезда по имени Солнце",
                                    artist="Кино", duration_s=225)]
    svc.providers["hitmoz"].cands = [cand("55", "https://cdn.test/x.mp3", title="Последний герой")]
    cases = {
        "exact": track("Кино", "Группа крови", 246),
        "probable": track("Кино", "Звезда по имени Солнце", 255),  # 30 s longer than the catalog's
        "owned": GOROD,
        "hitmoz": track("Кино", "Последний герой", 245),  # Cyrillic, Deezer lacks it
        "nothing": track("Кто-то", "Ничего общего", 100),
    }
    got = {k: await resolve_track("yandex", k, raw) for k, raw in cases.items()}
    assert (got["exact"].match, got["exact"].ref) == ("exact", "dz:100")
    assert (got["probable"].match, got["probable"].ref) == ("probable", "dz:300")
    assert got["owned"].match == "owned" and got["owned"].track is not None
    assert (got["hitmoz"].match, got["hitmoz"].ref) == ("probable", "hm:55")
    assert got["nothing"].match == "not_found"

    svc.deezer.down = True
    assert (await resolve_track("yandex", "exact", cases["exact"])).ref == "dz:100"  # remembered
    with pytest.raises(CatalogUnavailable):
        await resolve_track("yandex", "new", track("Кино", "Кукушка"))


@pytest.mark.parametrize(("starred", "want", "again"), [
    ([], ["a", "b"], ["a", "b"]),
    (["a", "b"], ["a", "b", "c"], ["c"]),
    (["a", "c"], ["a", "b", "c"], ["b", "c"]),
    (["a", "b"], ["a", "b"], []),
    (["b", "a"], ["a", "b"], ["a", "b"]),
], ids=["first", "newer-like", "older-like", "nothing-new", "reordered"])
def test_restar_plan(starred, want, again):
    from app.imports.apply import restar_plan

    assert restar_plan(starred, want) == again


def test_playlist_and_like_order():
    from app.imports.apply import like_order, playlist_order
    from app.models import ImportItem

    rows = [ImportItem(id=i, position=pos, status=st) for i, pos, st in
            [(1, 2, "done"), (2, 0, "done"), (3, 1, "waiting"), (4, 3, "done"), (5, 4, "skipped")]]
    songs = {1: "c", 2: "a", 4: "a", 5: "e"}  # 3 is not downloaded yet; 2 and 4 are the same song
    assert playlist_order(rows, songs) == ["a", "c", "a"]  # the service's order, a repeat kept, a removed row not
    assert like_order(rows, songs) == ["a", "c"]  # oldest like (the last position) first, each song once


async def test_dislikes_of_the_service_become_the_users_dislikes(env):
    from app.imports.sources.base import SnapCollection, SnapItem, Snapshot

    c, state = env["client"], env["state"]
    state["snapshot"] = Snapshot(account="ivan", collections=[SnapCollection("dislikes", "dislikes", "Не нравится", [
        SnapItem("1", KROVI), SnapItem("3", GOROD), SnapItem("9", NONE)])])
    imp = await prepared(c)
    r = await c.post(f"/api/imports/{imp['id']}/start",
                     json={"collection_ids": [x["id"] for x in imp["collections"]], "mode": "full"})
    assert r.status_code == 200, r.text
    await run_imports()
    # a catalog track by its ref, an owned one by its Navidrome song too; one not found is no dislike
    rows = {x["title"]: x for x in (await c.get("/api/dislikes")).json()}
    assert sorted(rows) == ["Город золотой", "Группа крови"]
    assert (rows["Группа крови"]["ref"], rows["Группа крови"]["source"]) == ("dz:100", "yandex_import")
    assert (rows["Город золотой"]["nd_song_id"], rows["Город золотой"]["source"]) == ("ndA", "yandex_import")
    assert await jobs("download_item") == [] and state["stars"] == []  # still never downloaded nor ♥


async def test_public_spotify_playlist_import_keeps_local_tracks_and_first_occurrence(env, monkeypatch):
    from types import SimpleNamespace
    from unittest.mock import AsyncMock

    from app.services import svc

    id_ = "37i9dQZF1DXcBWIGoYBM5M"
    uri = f"spotify:playlist:{id_}"
    artist = {"uri": "spotify:artist:DDDDDDDDDDDDDDDDDDDDDD", "profile": {"name": "Неизвестный"}}

    def entry(letter, title):
        return {"itemV2": {"data": {"uri": "spotify:track:" + letter * 22, "name": title,
                                   "artists": {"items": [artist]}, "trackDuration": {"totalMilliseconds": 180_000},
                                   "playcount": "42"}}}

    def local(raw):
        return {"itemV2": {"data": {"__typename": "LocalTrack", "uri": f"spotify:local:{raw['title']}",
                                   "name": raw["title"], "artistName": raw["artist"], "albumName": "Local album",
                                   "localTrackDuration": {"totalMilliseconds": raw["duration_s"] * 1000}}}}

    a, b, extra = entry("A", "Трек Spotify"), entry("B", "Второй трек"), entry("C", "Новый трек")
    rows = [a, local(NONE), b, a, local(KROVI), local(NONE), {"itemV2": {"data": None}}]
    reads = []

    async def playlist_items(operation, args, *, extract_page):
        from spotapi.catalogue import Operations

        assert operation == Operations.FETCH_PLAYLIST_CONTENTS.value
        reads.append(args["uri"])
        for batch in (rows[:2], rows[2:]):
            yield extract_page({"playlistV2": {"content": {"items": batch}}})

    info = SimpleNamespace(uri=uri, name="Публичный плейлист", images=None, owner_v2=None)
    client = SimpleNamespace(get_playlist=AsyncMock(return_value=info), queries=SimpleNamespace(paginate=playlist_items))
    monkeypatch.setattr(svc.spotify, "_get", AsyncMock(return_value=client))
    c = env["client"]
    r = await c.get(f"/api/search/spotify/playlist/{id_}")
    assert r.status_code == 200, r.text
    assert (len(r.json()["tracks"]), r.json()["local_tracks"], r.json()["skipped"]) == (3, 3, 1)
    r = await c.post("/api/imports", json={"source": "spotify", "method": "link",
                                           "url": f"https://open.spotify.com/playlist/{id_}"})
    assert r.status_code == 200, r.text
    ident = r.json()["id"]
    await run_imports()
    imp = (await c.get(f"/api/imports/{ident}")).json()
    assert imp["status"] == "preview" and not imp["has_token"]
    [coll] = imp["collections"]
    assert coll["count"] == 4
    r = await c.post(f"/api/imports/{ident}/start", json={"collection_ids": [coll["id"]], "mode": "economy"})
    assert r.status_code == 200, r.text
    await run_imports()
    resolved = await items(c, imp)
    assert [(row["title"], row["ref"]) for row in resolved] == [
        ("Трек Spotify", "sp:" + "A" * 22), (NONE["title"], None), ("Второй трек", "sp:" + "B" * 22),
        (KROVI["title"], "dz:100")]
    assert resolved[1]["status"] == "not_found" and resolved[1]["track_id"] is not None
    pid = next(p["id"] for p in (await c.get("/api/playlists")).json() if p["name"] == info.name)
    transferred = (await c.get(f"/api/playlists/{pid}")).json()["tracks"]
    assert [t["title"] for t in transferred] == [r["title"] for r in resolved]
    assert transferred[1]["unknown"] and transferred[1]["duration_s"] == NONE["duration_s"]
    client.get_playlist.assert_awaited_once_with(uri)
    assert reads == [uri]

    rows.append(extra)
    r = await c.post(f"/api/imports/{ident}/sync")
    assert r.status_code == 200, r.text
    await run_imports()
    updated = await items(c, imp)
    assert [row["id"] for row in updated[:-1]] == [row["id"] for row in resolved]
    assert updated[-1]["title"] == "Новый трек"
    assert reads == [uri, uri]
    r = await c.get(f"/api/search/spotify/playlist/{id_}")
    assert r.status_code == 200, r.text
    assert len(r.json()["tracks"]) == 4 and reads == [uri, uri]


async def test_transliterated_import_row_matches_by_isrc(env):
    from app.imports.resolve import catalog_track
    from app.services import svc

    result = await catalog_track({"artist": "キノ, Guest", "artists": ["キノ", "Guest"], "title": "血液型",
                                  "duration_s": 245, "isrc": "RUA1D8800001"})
    assert (result.match, result.ref) == ("exact", "dz:100")
    assert svc.deezer.by_isrc == 1


async def test_isrc_of_another_edit_falls_back_to_the_names(env):
    """A reused code on a recording of another length is not trusted: the names decide."""
    from app.imports.resolve import catalog_track

    r = await catalog_track({"artist": "キノ", "artists": ["キノ"], "title": "血液型", "duration_s": 400,
                             "isrc": "RUA1D8800001"})
    assert r.match == "not_found"
    r = await catalog_track({"artist": "Кино", "artists": ["Кино"], "title": "Группа крови", "duration_s": 245,
                             "isrc": "XX0000000000"})
    assert (r.match, r.ref) == ("exact", "dz:100")


async def test_isrc_with_the_same_title_under_another_artist_is_rejected(env, monkeypatch):
    from app.imports.resolve import catalog_track
    from app.services import svc

    candidate = (await svc.deezer.search_tracks("Кино", "Группа крови"))[0]

    async def search_tracks(*args, **kwargs):
        return [candidate]

    monkeypatch.setattr(svc.deezer, "search_tracks", search_tracks)
    result = await catalog_track({"artist": "Чужая группа", "title": "Группа крови", "duration_s": 245,
                                  "isrc": "RUA1D8800001"})
    assert result.ref is None


async def test_vk_sign_in_waits_for_the_code_typed_in_the_hub(env, monkeypatch):
    """VK: the login and the password start the sign-in, the code VK sends is typed in the Hub; the password is kept
    (encrypted) only while the code is awaited, and never shown."""
    from app.db import session_scope
    from app.imports.sources import vk
    from app.imports.sources.base import SnapCollection, SnapItem, Snapshot
    from app.models import Import

    c = env["client"]
    asked: list[dict] = []
    answers = [vk.NeedCode("ВК отправил код в SMS на +7 *** 12"), vk.NeedCode("Код не подошёл", wrong=True), "tok"]

    async def sign_in(login, password, *, code=None, captcha=None):
        asked.append({"login": login, "password": password, "code": code, "captcha": captcha})
        got = answers.pop(0)
        if isinstance(got, Exception):
            raise got
        return got

    async def fetch(token):
        assert token == "tok"
        return Snapshot(account="Алён К", collections=[SnapCollection(
            kind="playlist", external_id="audios", title="ВКонтакте", items=[SnapItem(external_id="7_1", raw=GOROD)])])

    monkeypatch.setattr(vk, "sign_in", sign_in)
    monkeypatch.setattr(vk, "fetch", fetch)

    assert (await c.post("/api/imports", json={"source": "vk", "method": "password", "login": "me"})).status_code == 400
    r = await c.post("/api/imports", json={"source": "vk", "method": "password", "login": " me ", "password": "pw"})
    assert r.status_code == 200, r.text
    imp = r.json()
    assert imp["status"] == "auth" and imp["auth"]["kind"] == "code" and "+7 *** 12" in imp["auth"]["hint"]
    assert "pw" not in r.text and "login" not in imp["auth"]
    async with session_scope() as s:
        kept = (await s.get(Import, imp["id"])).auth_json
        assert kept["password"] != "pw" and kept["login"] != "me"  # encrypted
    await run_imports()  # the job only watches the time
    assert (await c.get(f"/api/imports/{imp['id']}")).json()["status"] == "auth"

    imp = (await c.post(f"/api/imports/{imp['id']}/auth", json={"code": "000000"})).json()
    assert imp["status"] == "auth" and imp["auth"]["wrong"]
    imp = (await c.post(f"/api/imports/{imp['id']}/auth", json={"code": " 123456 "})).json()
    assert imp["status"] == "fetching" and imp["auth"] is None and imp["has_token"]
    assert asked == [{"login": "me", "password": "pw", "code": None, "captcha": None},
                     {"login": "me", "password": "pw", "code": "000000", "captcha": None},
                     {"login": "me", "password": "pw", "code": "123456", "captcha": None}]
    async with session_scope() as s:
        assert (await s.get(Import, imp["id"])).auth_json == {}  # the password is gone with the sign-in
    assert (await c.post(f"/api/imports/{imp['id']}/auth", json={"code": "1"})).status_code == 409

    await run_imports()
    imp = (await c.get(f"/api/imports/{imp['id']}")).json()
    assert imp["status"] == "preview" and imp["account"] == "Алён К"
    assert [(x["kind"], x["title"], x["count"]) for x in imp["collections"]] == [("playlist", "ВКонтакте", 1)]


async def test_vk_refusal_ends_the_import_and_a_captcha_is_passed_on(env, monkeypatch):
    from app.imports.sources import vk
    from app.imports.sources.base import SourceError

    c = env["client"]
    answers: list = [SourceError("ВК не принял логин или пароль")]

    async def sign_in(login, password, *, code=None, captcha=None):
        got = answers.pop(0)
        if isinstance(got, Exception):
            raise got
        return got

    monkeypatch.setattr(vk, "sign_in", sign_in)
    r = await c.post("/api/imports", json={"source": "vk", "method": "password", "login": "me", "password": "no"})
    assert r.status_code == 400 and "логин или пароль" in r.json()["detail"]

    answers += [vk.NeedCaptcha("https://id.vk.com/captcha?x=1"), SourceError("ВК временно закрыл вход")]
    imp = (await c.post("/api/imports", json={"source": "vk", "method": "password", "login": "me", "password": "pw"})).json()
    assert imp["auth"]["kind"] == "captcha" and imp["auth"]["verification_url"] == "https://id.vk.com/captcha?x=1"
    imp = (await c.post(f"/api/imports/{imp['id']}/auth", json={"captcha": "passed"})).json()
    assert imp["status"] == "failed" and "закрыл вход" in imp["error"] and imp["auth"] is None


async def test_a_playlist_of_an_import_deleted_by_the_user_is_not_made_again(env):
    """The import made the playlist; the user deleted it. A later rewrite (a track of it was downloaded) leaves it
    gone, and lets the collection go."""
    from app.db import session_scope
    from app.imports import apply
    from app.models import Import, ImportCollection

    c = env["client"]
    imp = await prepared(c)
    r = await c.post(f"/api/imports/{imp['id']}/start",
                     json={"collection_ids": [x["id"] for x in imp["collections"]], "mode": "economy"})
    assert r.status_code == 200, r.text
    await run_imports()
    made = next(x for x in (await c.get("/api/playlists")).json() if x["name"] == "Дорога")
    assert (await c.delete(f"/api/playlists/{made['id']}")).status_code == 200

    async with session_scope() as s:
        await apply.sync_playlists(s, await s.get(Import, imp["id"]))
    assert "Дорога" not in [x["name"] for x in (await c.get("/api/playlists")).json()]
    async with session_scope() as s:
        coll = (await s.execute(select(ImportCollection).where(ImportCollection.title == "Дорога"))).scalar_one()
        assert not coll.selected and coll.playlist_id is None


async def test_a_transfers_doubt_is_a_question_of_tidy(env):
    """A probable match of a transfer is the listener's question in «Наведение порядка» («Импорт»): «Совпадает» is
    its answer, written in the journal and taken back there; another listener does not see it."""
    from app.db import session_scope
    from app.models import ImportItem

    c = env["client"]
    imp = await prepared(c)
    likes, playlist = [x["id"] for x in imp["collections"][:2]]
    assert (await c.post(f"/api/imports/{imp['id']}/start", json={"collection_ids": [likes, playlist]})).status_code == 200
    await run_imports()
    async with session_scope() as s:
        rows = (await s.scalars(select(ImportItem).where(ImportItem.import_id == imp["id"],
                                                        ImportItem.external_id == "1"))).all()
        for row in rows:
            row.match = "probable"

    r = await c.get("/api/tidy/proposals", params={"section": "imports"})
    assert r.status_code == 200, r.text
    (p,) = r.json()
    assert p["kind"] == "import_match" and p["show"]["source"] == "yandex" and p["show"]["title"] == KROVI["title"]
    assert next(x for x in (await c.get("/api/tidy")).json()["sections"] if x["id"] == "imports")["count"] == 1
    assert [m["id"] for m in (await c.get("/api/tidy/marks")).json() if m["kind"] == "import_match"] == [p["id"]]

    r = await c.post(f"/api/tidy/proposals/{p['id']}/accept")  # «Совпадает»
    assert r.status_code == 200 and r.json()["state"] == "accepted", r.text
    async with session_scope() as s:
        got = (await s.scalars(select(ImportItem).where(ImportItem.import_id == imp["id"], ImportItem.external_id == "1",
                                                        ImportItem.track_id.is_not(None)))).all()
        assert got and all((x.raw_json or {}).get("match_reviewed") for x in got)
    assert (await c.get("/api/tidy/proposals", params={"section": "imports"})).json() == []
    # taken back: a question again
    assert (await c.post(f"/api/tidy/proposals/{p['id']}/review")).json()["state"] == "open"
    async with session_scope() as s:
        got = (await s.scalars(select(ImportItem).where(ImportItem.import_id == imp["id"], ImportItem.external_id == "1",
                                                        ImportItem.track_id.is_not(None)))).all()
        assert not any((x.raw_json or {}).get("match_reviewed") for x in got)
    # answered elsewhere (a file picked for it, which confirms the match): no question any more
    from app.imports import review

    from app.models import Import

    async with session_scope() as s:
        owner = (await s.get(Import, imp["id"])).user_id
        assert await review.acknowledge(s, owner, got[0].id)
    assert (await c.get("/api/tidy/proposals", params={"section": "imports"})).json() == []
