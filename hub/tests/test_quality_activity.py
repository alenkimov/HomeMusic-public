import asyncio
from types import SimpleNamespace

from app.db import session_scope
from app.jobs.downloads import search_download_sources, update_item
from app.models import DownloadBatch, DownloadItem, Track, Upgrade
from app.providers.base import TrackQuery
from tests.test_plays import env as env


async def test_quality_reports_search_and_download_source(env):
    q = TrackQuery(artist="Исполнитель", title="Трек")
    async with session_scope() as s:
        track = Track(artist=q.artist, title=q.title, fingerprint="test")
        batch = DownloadBatch(user_id=1, origin="upgrade", title="Качество звука")
        s.add_all([track, batch])
        await s.flush()
        item = DownloadItem(user_id=1, batch_id=batch.id, query_json={"query": q.model_dump()})
        s.add(item)
        await s.flush()
        item_id = item.id
        s.add(Upgrade(user_id=1, track_id=track.id, item_id=item_id, state="searching"))

    found = asyncio.Event()
    release = asyncio.Event()

    async def search(_query, *, limit):
        found.set()
        await release.wait()
        return []

    client = env["client"]
    states = {"hitmoz": "empty"}
    task = asyncio.create_task(search_download_sources([SimpleNamespace(name="ytdlp", search=search)], q, item_id, states))
    try:
        await asyncio.wait_for(found.wait(), 3)
        row = (await client.get("/api/quality")).json()["rows"][0]
        assert row["phase"] == "searching"
        assert row["search_sources"] == {"hitmoz": "empty", "ytdlp": "searching"}
    finally:
        release.set()
        await task

    await update_item(item_id, status="downloading", provider="soundcloud", progress=0)
    row = (await client.get("/api/quality")).json()["rows"][0]
    assert (row["phase"], row["provider"], row["progress"]) == ("downloading", "soundcloud", 0)
    assert row["search_sources"] == {}
