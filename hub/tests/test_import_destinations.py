from tests.test_imports import NONE, env, jobs, playlist_tracks, prepared, run_imports, snapshot  # noqa: F401


async def test_source_likes_can_be_imported_as_a_playlist_and_rescanned(env):
    from app.imports.sources.base import SnapItem

    client, state = env["client"], env["state"]
    imp = await prepared(client)
    likes = next(col for col in imp["collections"] if col["kind"] == "likes")
    url = f"/api/imports/{imp['id']}"
    response = await client.post(f"{url}/start", json={
        "collection_ids": [likes["id"]], "copy_likes": False, "keep_token": True,
    })
    assert response.status_code == 200, response.text
    await run_imports()
    assert (await client.get(url)).json()["status"] == "done"
    expected = [("Осень", "ndB"), ("Группа крови", None), ("Город золотой", "ndA")]
    assert await playlist_tracks(client, likes["title"]) == expected
    assert await jobs("download_item") == []

    updated = snapshot()
    updated.collections[0].items.append(SnapItem("new-like", NONE))
    state["snapshot"] = updated
    assert (await client.post(f"{url}/sync")).status_code == 200
    await run_imports()
    assert await playlist_tracks(client, likes["title"]) == [*expected, ("Нет такого", None)]
    playlists = (await client.get("/api/playlists")).json()
    assert [p["name"] for p in playlists].count(likes["title"]) == 1
    assert state["star_log"] == []
    starred = (await client.get("/api/starred")).json()
    assert not starred["song_ids"] and not starred["refs"]


async def test_preview_can_be_deleted_without_starting_the_transfer(env):
    client = env["client"]
    imp = await prepared(client)
    url = f"/api/imports/{imp['id']}"
    assert (await client.delete(url)).status_code == 200
    assert (await client.get(url)).status_code == 404
    assert not await jobs("import_resolve")
    assert not await jobs("download_item")
    assert env["state"]["star_log"] == []


async def test_pause_holds_matching_and_preserves_the_download_queue(env, monkeypatch):
    from datetime import timedelta

    from sqlalchemy import update

    from app.db import session_scope
    from app.imports import core
    from app.jobs import queue
    from app.models import DownloadItem, Job, utcnow

    client = env["client"]
    imp = await prepared(client)
    url = f"/api/imports/{imp['id']}"
    pause_url = f"{url}/pause"
    assert (await client.put(pause_url, json={"paused": True})).status_code == 409
    [likes] = [col for col in imp["collections"] if col["kind"] == "likes"]
    response = await client.post(f"{url}/start", json={"collection_ids": [likes["id"]], "mode": "full"})
    assert response.status_code == 200, response.text
    claimed = await queue._claim("matching", imports=True)
    assert claimed is not None and claimed.kind == "import_resolve"

    # Pause while this batch is already being matched against the catalog.
    async def pause_during_match(*args):
        result = await resolve_track(*args)
        response = await client.put(pause_url, json={"paused": True})
        assert response.status_code == 200 and response.json()["status"] == "paused"
        return result

    resolve_track = core.resolve_track
    with monkeypatch.context() as patch:
        patch.setattr(core, "resolve_track", pause_during_match)
        await queue.run_job(claimed)
    assert not await jobs("download_item")
    assert await queue._claim("matching", imports=True) is None
    assert await queue._next_wakeup_in(imports=True) == 30.0
    assert await core.resume_orphaned() == 0
    assert (await client.get(url)).json()["has_token"]
    assert (await client.post(f"{url}/sync")).status_code == 409

    response = await client.put(pause_url, json={"paused": False})
    assert response.status_code == 200 and response.json()["status"] == "running"
    resumed = await queue._claim("matching", imports=True)
    assert resumed is not None and resumed.id == claimed.id
    await queue.run_job(resumed)
    await run_imports()
    [download_job] = await jobs("download_item")
    [finish] = await jobs("import_finish")
    item_id = download_job.payload_json["item_id"]
    async with session_scope() as s:
        # Postpone maintenance and playlist updates unrelated to these two jobs.
        await s.execute(update(Job).where(Job.status == "queued", Job.id.not_in((download_job.id, finish.id)))
                        .values(run_after=utcnow() + timedelta(minutes=5)))
        (await s.get(Job, finish.id)).run_after = utcnow()

    assert (await client.put(pause_url, json={"paused": True})).status_code == 200
    assert await queue._claim("general") is None
    assert await queue._next_wakeup_in() == 30.0
    async with session_scope() as s:
        assert (await s.get(DownloadItem, item_id)).status == "queued"

    response = await client.put(pause_url, json={"paused": False})
    assert response.status_code == 200 and response.json()["status"] == "running"
    # the short finish goes ahead of the download at the same priority, and the download right after it
    assert [(await queue._claim("general")).id for _ in range(2)] == [finish.id, download_job.id]
    assert len(env["state"]["fetches"]) == 1


async def test_activity_shows_current_downloads_and_indexing_for_this_transfer(env):
    from sqlalchemy import select

    from app.api.imports import ACTIVITY_LIMIT
    from app.db import session_scope
    from app.models import DownloadBatch, DownloadItem, Import, Job, Track, User

    client = env["client"]
    import_id = (await prepared(client))["id"]
    async with session_scope() as s:
        imp = await s.get(Import, import_id)
        another = User(nd_username="another", nd_password_enc="unused")
        s.add(another)
        await s.flush()
        private_transfer = Import(user_id=another.id, source="spotify", method="profile", status="running")
        batch = DownloadBatch(user_id=imp.user_id, origin="import")
        s.add_all([private_transfer, batch])
        await s.flush()
        track_id = await s.scalar(select(Track.id).order_by(Track.id).limit(1))

        def make_item(title, status, transfer=import_id, **kwargs):
            return DownloadItem(batch_id=batch.id, user_id=batch.user_id, status=status, import_id=transfer, query_json={
                "query": {"title": title, "artist": "Artist"}, "import": {"id": transfer},
            }, **kwargs)

        search = make_item("Search", "searching", search_sources_json={"hitmoz": "empty", "ytdlp": "searching"})
        download = make_item("Download", "downloading", progress=0.62, provider="hitmoz")
        other = make_item("Other transfer", "downloading", transfer=private_transfer.id)
        stale = make_item("Waits for a retry", "tagging", progress=1, provider="hitmoz")
        s.add_all([
            make_item("Saved", "importing", provider="soundcloud", track_id=track_id),
            search, download, other, stale,
            make_item("In the queue", "queued"),
            make_item("Finished", "done"),
        ])
        await s.flush()
        s.add_all([Job(kind="download_item", payload_json={"item_id": it.id}, status="running")
                   for it in (search, download, other)])
        s.add(Job(kind="download_item", payload_json={"item_id": stale.id}, status="queued"))

    url = f"/api/imports/{import_id}/activity"
    response = await client.get(url)
    assert response.status_code == 200, response.text
    data = response.json()
    assert data["more"] == 0
    search_row, download_row, saved_row = data["items"]
    assert (search_row["title"], search_row["artist"], search_row["searching"]) == ("Search", "Artist", ["ytdlp"])
    assert (download_row["title"], download_row["provider"], download_row["progress"]) == ("Download", "hitmoz", 0.62)
    assert (saved_row["title"], saved_row["status"], saved_row["file_saved"]) == ("Saved", "importing", True)
    assert (await client.get(f"/api/imports/{private_transfer.id}/activity")).status_code == 404

    async with session_scope() as s:
        (await s.get(DownloadItem, search.id)).status = "done"
        batch = await s.get(DownloadBatch, batch.id)
        s.add_all([make_item(f"Saved {i}", "importing", track_id=track_id) for i in range(ACTIVITY_LIMIT)])
    data = (await client.get(url)).json()
    assert data["more"] == 2
    assert len(data["items"]) == ACTIVITY_LIMIT
    assert data["items"][0]["title"] == "Download"


async def test_downloads_overview_groups_a_transfer_and_retries_it_whole(env):
    """«Загрузки» by group: a transfer's downloads are one group, a mix's show only while under way, the user's own
    (an asked-for one of a transfer too) are «own»; a group's list goes by pages and «Повторить все» retries it."""
    from app.db import session_scope
    from app.models import DownloadBatch, DownloadItem, Import

    client = env["client"]
    import_id = (await prepared(client))["id"]
    async with session_scope() as s:
        imp = await s.get(Import, import_id)
        batch = DownloadBatch(user_id=imp.user_id, origin="import")
        s.add(batch)
        await s.flush()

        def make(title, status, transfer=import_id, **qj):
            extra = {"import": {"id": transfer}} if transfer else {}
            return DownloadItem(batch_id=batch.id, user_id=imp.user_id, status=status, import_id=transfer,
                                query_json={"query": {"title": title, "artist": "A"}, **extra, **qj})

        s.add_all([
            make("t1", "failed"), make("t2", "needs_review"), make("t3", "done"), make("t4", "queued"),
            make("asked", "needs_review", requested=True),
            make("mix-q", "queued", transfer=None, mix={"id": 1}), make("mix-f", "failed", transfer=None, mix={"id": 1}),
            make("own", "failed", transfer=None),
        ])
    r = await client.get("/api/downloads/overview")
    assert r.status_code == 200, r.text
    o = r.json()
    groups = {g["key"]: (g["now"], g["review"], g["done"]) for g in o["groups"]}
    assert groups == {f"import:{import_id}": (1, 2, 1), "mix": (1, 0, 0), "own": (0, 2, 0)}
    assert (o["now"], o["review"], o["done"]) == (2, 4, 1)
    first = o["groups"][0]
    assert (first["key"], first["source"], first["import_status"]) == (f"import:{import_id}", "yandex", "preview")

    page = (await client.get("/api/downloads", params={"group": f"import:{import_id}", "status": "failed,needs_review", "limit": 1})).json()
    assert [(x["title"], x["origin"]) for x in page] == [("t2", "import")]
    rest = (await client.get("/api/downloads", params={"group": f"import:{import_id}", "status": "failed,needs_review", "before": page[0]["id"]})).json()
    assert [x["title"] for x in rest] == ["t1"]
    own = (await client.get("/api/downloads", params={"group": "own"})).json()
    assert sorted((x["title"], x["origin"]) for x in own) == [("asked", None), ("own", None)]
    assert (await client.get("/api/downloads", params={"group": "nope"})).status_code == 400

    r = await client.post("/api/downloads/retry-group", json={"group": f"import:{import_id}"})
    assert r.status_code == 200 and r.json() == {"queued": 2}
    o = (await client.get("/api/downloads/overview")).json()
    assert {g["key"]: g["review"] for g in o["groups"]}[f"import:{import_id}"] == 0
