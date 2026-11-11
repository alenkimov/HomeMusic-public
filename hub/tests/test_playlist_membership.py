"""«В плейлисты»: which of the user's playlists have a song, and putting it into some and out of others at once."""

from __future__ import annotations

import pytest

from tests.support import app_client


class FakeNd:
    def __init__(self, state: dict):
        self.state = state

    async def aclose(self) -> None:
        pass

    async def get_playlists(self):
        self.state["calls"].append("list")
        return [{"id": pid, "name": p["name"], "songCount": len(p["songs"]), "changed": p["changed"], "owner": p.get("owner")}
                for pid, p in self.state["playlists"].items()]

    async def get_playlist(self, pid):
        self.state["calls"].append(f"get:{pid}")
        p = self.state["playlists"][pid]
        return {"id": pid, "name": p["name"], "entry": [{"id": x} for x in p["songs"]]}

    async def update_playlist(self, pid, *, add=None, remove_indexes=None, **kw):
        p = self.state["playlists"][pid]
        p["songs"] += add or []
        for i in sorted(remove_indexes or [], reverse=True):
            del p["songs"][i]
        p["changed"] = "2026-11-11T12:00:00Z"


@pytest.fixture
async def env(hub_env, monkeypatch):
    import httpx
    import respx

    from app.api import medialib, playlists
    from app.main import create_app

    state = {"calls": [], "playlists": {
        "a": {"name": "Уважаемые", "songs": ["s1", "s2", "s1"], "changed": "2026-11-01T00:00:00Z"},
        "b": {"name": "JAPAN", "songs": ["s3"], "changed": "2026-11-10T00:00:00Z"},
        "c": {"name": "Чужой", "songs": ["s1"], "changed": "2026-11-11T00:00:00Z", "owner": "someone"},
    }}
    playlists._songs.clear()
    monkeypatch.setattr(medialib, "user_navidrome", lambda user: FakeNd(state))
    app = create_app()
    async with app.router.lifespan_context(app):
        with respx.mock(assert_all_called=False) as mock:
            mock.route(host="navidrome.test").mock(return_value=httpx.Response(200, json={"subsonic-response": {
                "status": "ok", "version": "1.16.1", "user": {"adminRole": True}}}))
            async with app_client(app) as client:
                yield client, state


async def test_where_a_song_is_and_changing_it_at_once(env):
    c, state = env
    r = (await c.get("/api/playlists/of-song", params={"nd_song_id": "s1"})).json()
    # the user's own playlists, the one changed last first
    assert [(x["name"], x["has"], x["song_count"]) for x in r] == [("JAPAN", False, 1), ("Уважаемые", True, 3)]
    state["calls"].clear()
    await c.get("/api/playlists/of-song", params={"nd_song_id": "s2"})
    assert state["calls"] == ["list"]  # an unchanged playlist is not read again

    r = (await c.post("/api/playlists/membership", json={"nd_song_id": "s1", "add": ["b", "b"], "remove": ["a"]})).json()
    assert [(x["name"], x["has"]) for x in r] == [("Уважаемые", False), ("JAPAN", True)]
    assert state["playlists"]["a"]["songs"] == ["s2"]  # both places of the song are gone
    assert state["playlists"]["b"]["songs"] == ["s3", "s1"]
    await c.post("/api/playlists/membership", json={"nd_song_id": "s1", "add": ["b"]})
    assert state["playlists"]["b"]["songs"] == ["s3", "s1"]  # not added twice
