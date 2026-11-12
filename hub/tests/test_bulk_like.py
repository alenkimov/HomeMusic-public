"""♥ for many tracks in one request (the selection bar): quick, and each track is journaled once."""

from __future__ import annotations

import time

from tests.test_plays import env as env  # the fixture


async def test_a_hundred_catalog_tracks_are_liked_in_one_request(env):
    c = env["client"]
    targets = [{"target_type": "track", "ref": f"dz:{9000 + i}", "target_data": {"title": f"T{i}", "artist": "A"}} for i in range(120)]
    targets.append({"target_type": "track", "nd_song_id": "ndK"})
    started = time.monotonic()
    r = await c.post("/api/star", json={"targets": targets, "event_id": "bulk-1", "client": "pwa", "context": "playlist"})
    took = time.monotonic() - started
    assert r.status_code == 200, r.text
    assert len(set(r.json()["event_ids"])) == 121
    stars = (await c.get("/api/starred")).json()
    assert len([s for s in stars["subjects"] if s["liked"]]) == 121
    assert took < 10, f"121 likes took {took:.1f} s"
    # the same request again (a retry) changes nothing
    r = await c.post("/api/star", json={"targets": targets, "event_id": "bulk-1", "client": "pwa", "context": "playlist"})
    assert r.status_code == 200 and len([s for s in (await c.get("/api/starred")).json()["subjects"] if s["liked"]]) == 121
    print(f"121 likes: {took:.2f} s")
