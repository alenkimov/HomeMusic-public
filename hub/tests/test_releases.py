"""«Релизы»: what followed artists have put out; following is the ♥ of an artist, not of a track."""

from __future__ import annotations

from datetime import date, timedelta

from tests.test_plays import env as env  # the fixture


def album(id_: str, title: str, days_ago: int, kind: str = "album"):
    from app.catalog.models import CatalogAlbum

    return CatalogAlbum(ref=f"dz:{id_}", source="dz", id=id_, title=title, artist="Кино", artist_ref="dz:7",
                        record_type=kind, release_date=date.today() - timedelta(days=days_ago), nb_tracks=3)


async def like(c, **target):
    r = await c.post("/api/star", json={"targets": [target], "event_id": f"e-{target.get('ref')}-{target.get('unstar', '')}",
                                        "unstar": bool(target.pop("unstar", False)), "client": "pwa"})
    assert r.status_code == 200, r.text


async def test_the_feed_of_followed_artists(env, monkeypatch):
    from app import releases

    c = env["client"]
    asked: list[str] = []
    shelf = {"dz:7": [album("71", "Новый альбом", 3), album("72", "Сингл", 10, "single"), album("73", "Старое", 400)]}

    async def albums_of(ref, name):
        asked.append(ref)
        return shelf.get(ref, [])

    monkeypatch.setattr(releases, "albums_of", albums_of)

    # a liked track does not follow its artist
    await like(c, target_type="track", ref="dz:1001", target_data={"title": "Песня", "artist": "Кино", "artist_ref": "dz:7"})
    r = (await c.get("/api/releases")).json()
    assert (r["artists"], r["items"], asked) == (0, [], [])

    await like(c, target_type="artist", ref="dz:7", target_data={"name": "Кино"})
    r = (await c.get("/api/releases")).json()
    assert r["artists"] == 1 and r["unseen"] == 2
    assert [(x["title"], x["record_type"], x["new"]) for x in r["items"]] == [("Новый альбом", "album", True), ("Сингл", "single", True)]
    assert (await c.get("/api/releases/count")).json() == {"unseen": 2}

    # opened: nothing is new any more; the artist is not looked up again before its time
    assert (await c.post("/api/releases/seen")).status_code == 200
    r = (await c.get("/api/releases")).json()
    assert r["unseen"] == 0 and asked == ["dz:7"]

    # the next look finds what has come out since, once
    shelf["dz:7"].insert(0, album("74", "Вчерашний", 1, "ep"))
    monkeypatch.setattr(releases, "CHECK_EVERY", timedelta(0))
    assert await releases.refresh(1) == 1
    assert await releases.refresh(1) == 0
    r = (await c.get("/api/releases")).json()
    assert [(x["title"], x["new"]) for x in r["items"]] == [("Вчерашний", True), ("Новый альбом", False), ("Сингл", False)]

    # not followed any more: out of the feed
    await like(c, target_type="artist", ref="dz:7", target_data={"name": "Кино"}, unstar=True)
    r = (await c.get("/api/releases")).json()
    assert (r["artists"], r["items"]) == (0, [])
    assert (await c.get("/api/releases/count")).json() == {"unseen": 0}
