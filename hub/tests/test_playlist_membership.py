"""Playlists of the Hub's own (app/playlists.py, app/api/playlists.py): where a song is, tracks without a file in
their places, and the mirror Navidrome gets (app/playlists_mirror.py)."""

from __future__ import annotations

import pytest
from sqlalchemy import select

from app.catalog.models import CatalogTrack
from app.library.navidrome import SubsonicError
from tests.test_plays import add_track
from tests.test_plays import env as env  # the fixture: «Кино — Группа крови» (ndK, dz:100) and «Кто-то — Своё» (ndX)


class FakeNd:
    """Navidrome's playlists, as much of them as the mirror touches."""

    def __init__(self, state: dict):
        self.state = state

    async def aclose(self) -> None:
        pass

    async def get_playlists(self):
        return [{"id": pid, "name": p["name"], "owner": p.get("owner")} for pid, p in self.state["playlists"].items()]

    async def get_playlist(self, pid):
        if pid not in self.state["playlists"]:
            raise SubsonicError(70, "not found")
        p = self.state["playlists"][pid]
        return {"id": pid, "name": p["name"], "coverArt": f"pl-{pid}", "entry": [{"id": x} for x in p["songs"]]}

    async def create_playlist(self, name, song_ids=None):
        self.state["made"] = self.state.get("made", 0) + 1
        pid = f"nd{self.state['made']}"
        self.state["playlists"][pid] = {"name": name, "songs": list(song_ids or [])}
        return {"id": pid, "name": name}

    async def replace_playlist_songs(self, pid, song_ids):
        self.state["playlists"][pid]["songs"] = list(song_ids)
        self.state["writes"] += 1

    async def update_playlist(self, pid, *, name=None, **kw):
        if name:
            self.state["playlists"][pid]["name"] = name

    async def delete_playlist(self, pid):
        self.state["playlists"].pop(pid, None)


@pytest.fixture
def nd(monkeypatch):
    from app import playlists_mirror

    state = {"playlists": {}, "writes": 0}
    monkeypatch.setattr(playlists_mirror, "user_navidrome", lambda user: FakeNd(state))
    return state


async def new_playlist(c, name: str) -> str:
    r = await c.post("/api/playlists", json={"name": name})
    assert r.status_code == 200, r.text
    return r.json()["id"]


async def catalog_track(n: int, title: str) -> int:
    """A track the Hub knows from the catalog and has no file of."""
    from app.db import session_scope
    from app.library import tracks

    async with session_scope() as s:
        return (await tracks.of_catalog(s, CatalogTrack(
            ref=f"dz:{n}", source="dz", id=str(n), title=title, artist="Ноунейм", duration_s=180,
            cover_url=f"https://cover/{n}.jpg", album_title="Альбом"))).id


async def test_where_a_song_is_and_changing_it_at_once(env):
    c = env["client"]
    a, b = await new_playlist(c, "Уважаемые"), await new_playlist(c, "JAPAN")
    assert (await c.post(f"/api/playlists/{a}/add", json={"song_ids": ["ndK", "ndX"]})).json() == {"ok": True, "added": 2}
    r = (await c.get("/api/playlists/of-song", params={"nd_song_id": "ndK"})).json()
    assert {x["name"]: (x["has"], x["song_count"]) for x in r} == {"Уважаемые": (True, 2), "JAPAN": (False, 0)}
    assert r[0]["name"] == "Уважаемые"  # the one changed last first

    r = (await c.post("/api/playlists/membership", json={"nd_song_id": "ndK", "add": [b, b], "remove": [a]})).json()
    assert {x["name"]: x["has"] for x in r} == {"Уважаемые": False, "JAPAN": True}
    await c.post("/api/playlists/membership", json={"nd_song_id": "ndK", "add": [b]})
    assert [t["nd_song_id"] for t in (await c.get(f"/api/playlists/{b}")).json()["tracks"]] == ["ndK"]  # not added twice
    assert (await c.post("/api/playlists/membership", json={"nd_song_id": "nope", "add": [b]})).status_code == 404
    assert (await c.get("/api/playlists/members")).json() == {"song_ids": ["ndK", "ndX"]}


async def test_several_songs_at_once(env):
    c = env["client"]
    await add_track("Третий", "Трек", "ndZ")
    a, b = await new_playlist(c, "Уважаемые"), await new_playlist(c, "JAPAN")
    await c.post(f"/api/playlists/{a}/add", json={"song_ids": ["ndK", "ndX"]})
    await c.post(f"/api/playlists/{b}/add", json={"song_ids": ["ndZ"]})
    r = (await c.post("/api/playlists/of-songs", json={"nd_song_ids": ["ndK", "ndX", "ndZ", "nope"]})).json()
    assert {x["name"]: x["count"] for x in r} == {"JAPAN": 1, "Уважаемые": 2}
    r = (await c.post("/api/playlists/membership-many",
                      json={"nd_song_ids": ["ndK", "ndZ"], "add": [b], "remove": [a]})).json()
    assert {x["name"]: x["count"] for x in r} == {"JAPAN": 2, "Уважаемые": 0}
    assert [t["nd_song_id"] for t in (await c.get(f"/api/playlists/{b}")).json()["tracks"]] == ["ndZ", "ndK"]
    assert [t["nd_song_id"] for t in (await c.get(f"/api/playlists/{a}")).json()["tracks"]] == ["ndX"]


async def test_a_track_without_a_file_stands_in_its_place(env):
    c = env["client"]
    pid = await new_playlist(c, "Дорога")
    waiting = await catalog_track(555, "Ещё не скачан")
    await c.post(f"/api/playlists/{pid}/add", json={"song_ids": ["ndK"]})
    await c.post(f"/api/playlists/{pid}/add", json={"library_track_ids": [waiting]})
    await c.post(f"/api/playlists/{pid}/add", json={"song_ids": ["ndX"]})

    p = (await c.get(f"/api/playlists/{pid}")).json()
    assert (p["song_count"], p["pending_count"]) == (2, 1)
    assert [(t["title"], t["owned"]) for t in p["tracks"]] == [("Группа крови", True), ("Ещё не скачан", False), ("Своё", True)]
    mid = p["tracks"][1]
    assert (mid["ref"], mid["library_track_id"], mid["cover_url"], mid["nd_song_id"]) == ("dz:555", waiting, "https://cover/555.jpg", None)
    assert all(t["added_at"] for t in p["tracks"])
    listed = next(x for x in (await c.get("/api/playlists")).json() if x["id"] == pid)
    assert (listed["song_count"], listed["pending_count"]) == (2, 1)

    # taken out by its place, like any other
    assert (await c.post(f"/api/playlists/{pid}/remove", json={"indexes": [1, 1]})).json() == {"ok": True}
    assert [t["title"] for t in (await c.get(f"/api/playlists/{pid}")).json()["tracks"]] == ["Группа крови", "Своё"]
    assert (await c.post(f"/api/playlists/{pid}/remove", json={"indexes": []})).status_code == 422


async def test_a_playlist_is_renamed_given_a_picture_and_deleted(env):
    import io

    from PIL import Image

    c = env["client"]
    pid = await new_playlist(c, "Дорога")
    r = (await c.put(f"/api/playlists/{pid}", json={"name": "  Любимые  дороги "})).json()
    assert r["name"] == "Любимые дороги" and r["cover_url"] is None
    assert (await c.put(f"/api/playlists/{pid}", json={"name": "   "})).status_code in (400, 422)
    assert (await c.put("/api/playlists/99999", json={"name": "x"})).status_code == 404
    assert (await c.get("/api/playlists/not-a-number")).status_code == 404

    png = io.BytesIO()
    Image.new("RGB", (900, 500), "red").save(png, "PNG")
    r = (await c.put(f"/api/playlists/{pid}/cover", files={"file": ("x.png", png.getvalue(), "image/png")})).json()
    assert r["cover_url"].startswith(f"/api/playlists/{pid}/cover?v=")
    got = await c.get(r["cover_url"])
    assert got.headers["content-type"] == "image/jpeg" and Image.open(io.BytesIO(got.content)).size == (500, 500)
    assert (await c.put(f"/api/playlists/{pid}/cover", files={"file": ("x.txt", b"not a picture", "text/plain")})).status_code == 400
    assert (await c.delete(f"/api/playlists/{pid}/cover")).json()["cover_url"] is None
    assert (await c.get(f"/api/playlists/{pid}/cover")).status_code == 404

    assert (await c.delete(f"/api/playlists/{pid}")).json() == {"ok": True}
    assert (await c.get(f"/api/playlists/{pid}")).status_code == 404
    assert (await c.get("/api/playlists")).json() == []


async def test_the_mirror_gets_the_songs_and_what_other_apps_did_comes_back(env, nd):
    from app import playlists
    from app.db import session_scope
    from app.playlists_mirror import mirror

    c = env["client"]
    await add_track("Третий", "Трек", "ndZ")
    pid = int(await new_playlist(c, "Дорога"))
    waiting = await catalog_track(555, "Ещё не скачан")
    await c.post(f"/api/playlists/{pid}/add", json={"song_ids": ["ndK"]})
    await c.post(f"/api/playlists/{pid}/add", json={"library_track_ids": [waiting]})
    await c.post(f"/api/playlists/{pid}/add", json={"song_ids": ["ndX"]})

    await mirror(pid)
    assert nd["playlists"] == {"nd1": {"name": "Дорога", "songs": ["ndK", "ndX"]}}  # only what has a file
    writes = nd["writes"]
    await mirror(pid)
    assert nd["writes"] == writes  # nothing changed: nothing is written

    # another app adds a song and takes one out: both come back, the track without a file stays in its place
    nd["playlists"]["nd1"]["songs"] = ["ndX", "ndZ"]
    await mirror(pid)
    async with session_scope() as s:
        assert [t.title for _, t in await playlists.items(s, pid)] == ["Ещё не скачан", "Своё", "Трек"]
    assert nd["playlists"]["nd1"]["songs"] == ["ndX", "ndZ"]

    # a new name here goes there; a mirror deleted there is made again
    await c.put(f"/api/playlists/{pid}", json={"name": "Путь"})
    await mirror(pid)
    assert nd["playlists"]["nd1"]["name"] == "Путь"
    del nd["playlists"]["nd1"]
    await mirror(pid)
    assert nd["playlists"] == {"nd2": {"name": "Путь", "songs": ["ndX", "ndZ"]}}
    p = (await c.get(f"/api/playlists/{pid}")).json()
    assert p["cover_url"] == "/api/nd/cover/pl-nd2?v=2"  # the picture Navidrome makes of the mirror's albums


async def test_a_track_without_a_file_is_tagged_like_any_other(env):
    """What the whole of 2.0 was started for: a tag on a playlist's track the library has no file of yet."""
    c = env["client"]
    waiting = await catalog_track(555, "Ещё не скачан")
    tag = (await c.post("/api/tags", json={"name": "Пляжный"})).json()["id"]
    r = await c.post("/api/tags/assign", json={"nd_song_ids": [f"t:{waiting}", "ndK"], "add": [tag], "remove": []})
    assert r.status_code == 200, r.text
    tags = r.json()["map"]
    assert tag in tags[f"t:{waiting}"] and tag in tags["ndK"]  # one by the Hub's id, one by its Navidrome song

    # the file arrives: the tag is on the same track, now named by its song
    from app.db import session_scope
    from app.models import TrackFile

    async with session_scope() as s:
        s.add(TrackFile(track_id=waiting, uid="u-555", path="a/555.mp3", nd_song_id="nd555"))
    tags = (await c.get("/api/tags")).json()["map"]
    assert tag in tags["nd555"] and f"t:{waiting}" not in tags


async def test_liked_is_a_playlist_and_a_liked_track_without_a_file_is_a_track(env):
    from app import playlists
    from app.db import session_scope

    c = env["client"]
    target = {"ref": "dz:999", "target_data": {"title": "WHAT TO DO?", "artist": "Jackboys", "duration_s": 250,
                                               "cover_url": "https://cover/999.jpg"}}
    assert (await c.post("/api/star", json={"targets": [target]})).status_code == 200
    assert (await c.post("/api/star", json={"song_ids": ["ndK"]})).status_code == 200
    songs = (await c.get("/api/nd/liked")).json()["songs"]
    assert [(x["title"], x["owned"]) for x in songs] == [("Группа крови", True), ("WHAT TO DO?", False)]  # newest on top
    waiting = songs[1]
    assert waiting["library_track_id"] and waiting["cover_url"] == "https://cover/999.jpg" and waiting["added_at"]

    async with session_scope() as s:  # it is the playlist of kind "liked", not something told anew from the journal
        liked = await playlists.liked(s, 1)
        assert [t.title for _, t in await playlists.items(s, liked.id)] == ["Группа крови", "WHAT TO DO?"]
        assert (await playlists.kept(s, 1))[0] == {t.id for _, t in await playlists.items(s, liked.id)}

    tag = (await c.post("/api/tags", json={"name": "На потом"})).json()["id"]
    r = await c.post("/api/tags/assign", json={"nd_song_ids": [f"t:{waiting['library_track_id']}"], "add": [tag], "remove": []})
    assert tag in r.json()["map"][f"t:{waiting['library_track_id']}"]

    stars = (await c.get("/api/starred")).json()
    assert (stars["song_ids"], stars["refs"]) == (["ndK"], ["dz:999"])
    assert (await c.post("/api/star", json={"targets": [target], "unstar": True})).status_code == 200
    assert [x["title"] for x in (await c.get("/api/nd/liked")).json()["songs"]] == ["Группа крови"]


async def test_a_track_without_a_file_is_put_into_playlists_like_any_other(env):
    c = env["client"]
    waiting = await catalog_track(555, "Ещё не скачан")
    a, b = await new_playlist(c, "Дорога"), await new_playlist(c, "Вечер")
    key = f"t:{waiting}"
    r = (await c.post("/api/playlists/membership", json={"nd_song_id": key, "add": [a, b]})).json()
    assert {x["name"]: x["has"] for x in r} == {"Дорога": True, "Вечер": True}
    assert {x["name"]: x["has"] for x in (await c.get("/api/playlists/of-song", params={"nd_song_id": key})).json()} \
        == {"Дорога": True, "Вечер": True}
    r = (await c.post("/api/playlists/membership-many", json={"nd_song_ids": [key, "ndK"], "add": [], "remove": [b]})).json()
    assert {x["name"]: x["count"] for x in r} == {"Дорога": 1, "Вечер": 0}
    assert (await c.get("/api/playlists/members")).json() == {"song_ids": [key]}
