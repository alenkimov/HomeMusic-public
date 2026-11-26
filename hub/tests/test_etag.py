"""A read of the Hub says its version; asked again unchanged, it is a bare 304."""

from __future__ import annotations

from tests.test_flow import env as env  # the fixture
from tests.test_flow import src_mp3 as src_mp3  # its file


async def test_an_unchanged_list_is_not_sent_again(env):
    c = env["client"]
    r = await c.get("/api/playlists")
    assert r.status_code == 200 and r.headers["etag"].startswith('W/"')
    assert r.headers["cache-control"] == "no-cache, private"  # always asked, never taken stale
    again = await c.get("/api/playlists", headers={"If-None-Match": r.headers["etag"]})
    assert again.status_code == 304 and again.content == b"" and again.headers["etag"] == r.headers["etag"]
    assert again.headers["x-homemusic-hub"] == "1"  # still the Hub's answer: the page knows it is there
    await c.post("/api/playlists", json={"name": "Новый"})
    changed = await c.get("/api/playlists", headers={"If-None-Match": r.headers["etag"]})
    assert changed.status_code == 200 and changed.headers["etag"] != r.headers["etag"]
    # what is not a JSON read passes as it is
    assert "etag" not in (await c.get("/api/ping")).headers
