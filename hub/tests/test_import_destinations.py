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
