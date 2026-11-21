"""Concurrent Spotify calls share a fetch and store its answer in the Hub's cache."""

import asyncio
from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest

from app import db
from app.catalog.spotify import SpotifyCatalog
from app.models import CatalogCache


@pytest.fixture
async def catalog(db_url):
    db.init_engine(db_url)
    client = SpotifyCatalog()
    try:
        yield client
    finally:
        await client.aclose()
        await db.dispose()


async def test_track_requests_share_the_read_across_a_caller_cancellation(catalog, monkeypatch):
    id_ = "2Foc5Q5nqNiosCNqttzHof"
    uri = f"spotify:track:{id_}"
    started, release = asyncio.Event(), asyncio.Event()

    async def get_track(requested):
        started.set()
        await release.wait()
        return SimpleNamespace(uri=requested, name="Get Lucky", duration=None, playcount="5000")

    client = SimpleNamespace(get_track=AsyncMock(side_effect=get_track))
    monkeypatch.setattr(catalog, "_get", AsyncMock(return_value=client))
    cancelled = asyncio.create_task(catalog.track(id_))
    await asyncio.wait_for(started.wait(), 2)
    waiting = asyncio.gather(*(catalog.track(id_) for _ in range(5)))
    cancelled.cancel()
    with pytest.raises(asyncio.CancelledError):
        await cancelled
    release.set()
    assert [track.spotify_plays for track in await asyncio.wait_for(waiting, 2)] == [5000] * 5
    async with db.session_scope() as s:
        row = await s.get(CatalogCache, f"sp:track:{id_}")
        assert row is not None and row.ttl_s == 86_400
    assert (await SpotifyCatalog().track(id_)).spotify_plays == 5000
    client.get_track.assert_awaited_once_with(uri)


async def test_track_can_retry_after_shared_fetch_fails(catalog, monkeypatch):
    id_ = "2Foc5Q5nqNiosCNqttzHof"
    uri = f"spotify:track:{id_}"
    row = SimpleNamespace(uri=uri, name="Get Lucky", duration=None, playcount="5000")
    get_track = AsyncMock(side_effect=[OSError("Spotify unavailable"), row])
    monkeypatch.setattr(catalog, "_get", AsyncMock(return_value=SimpleNamespace(get_track=get_track)))
    outcomes = await asyncio.gather(catalog.track(id_), catalog.track(id_), return_exceptions=True)
    assert all(isinstance(outcome, OSError) for outcome in outcomes)
    get_track.assert_awaited_once_with(uri)
    assert (await catalog.track(id_)).spotify_plays == 5000
    assert get_track.await_count == 2
