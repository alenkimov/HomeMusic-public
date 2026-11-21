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

    async def tracks_of(album_ref):
        from app.catalog.models import CatalogTrack

        n = album_ref.split(":")[1]
        return [CatalogTrack(ref=f"dz:{n}{i}", source="dz", id=f"{n}{i}", title=f"Трек {i}", artist="Кино", artist_ref="dz:7",
                             album_ref=album_ref) for i in (1, 2)]

    monkeypatch.setattr(releases, "tracks_of", tracks_of)

    # a liked track does not follow its artist
    await like(c, target_type="track", ref="dz:1001", target_data={"title": "Песня", "artist": "Кино", "artist_ref": "dz:7"})
    r = (await c.get("/api/releases")).json()
    assert (r["artists"], r["items"], asked) == (0, [], [])

    await like(c, target_type="artist", ref="dz:7", target_data={"name": "Кино"})
    r = (await c.get("/api/releases")).json()
    assert r["artists"] == 1 and r["unseen"] == 2
    assert [(x["title"], x["record_type"], x["new"]) for x in r["items"]] == [("Новый альбом", "album", True), ("Сингл", "single", True)]
    assert (await c.get("/api/releases/count")).json() == {"unseen": 2, "artists": 1}
    # each release comes with its tracks; the artist — with what they released last, however long ago it was
    assert [[x["title"] for x in i["tracks"]] for i in r["items"]] == [["Трек 1", "Трек 2"]] * 2
    assert r["items"][0]["tracks"][0]["ref"] == "dz:711" and r["items"][0]["tracks"][0]["owned"] is False
    assert [(f["ref"], f["name"], f["like_ref"], f["new"], f["last_release_title"]) for f in r["following"]] == [
        ("dz:7", "Кино", "dz:7", 2, "Новый альбом")]
    assert r["following"][0]["last_release_date"] == (date.today() - timedelta(days=3)).isoformat()

    # a release kept without its tracks (the catalog did not answer then) gets them at the next opening
    from sqlalchemy import update

    from app.db import session_scope
    from app.models import ArtistRelease
    async with session_scope() as s:
        await s.execute(update(ArtistRelease).where(ArtistRelease.album_ref == "dz:72").values(tracks_json=None))
    r = (await c.get("/api/releases")).json()
    assert [len(i["tracks"]) for i in r["items"]] == [2, 2]

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
    assert (await c.get("/api/releases/count")).json() == {"unseen": 0, "artists": 0}


async def test_saved_release_gets_complete_track_credits(env, monkeypatch):
    from sqlalchemy import select

    from app import releases
    from app.catalog.models import CatalogTrack
    from app.db import session_scope
    from app.models import ArtistRelease, utcnow
    from app.services import svc

    original = CatalogTrack(ref="dz:1000", id="1000", source="dz", title="БЕССЕРДЕШНЫЙ", artist="OSA", artists=["OSA"])
    full = original.model_copy(update={"artists": ["OSA", "ЛСП", "old honda", "Selfish"], "credits_complete": True})

    async def credited(id_):
        assert id_ == "71"
        return album("71", original.title, 1, "single").model_copy(update={"tracks": [full]})

    monkeypatch.setattr(svc.deezer, "get_album_credited", credited)
    stamp = utcnow()
    async with session_scope() as s:
        s.add(ArtistRelease(user_id=1, artist_ref="dz:7", artist="OSA", album_ref="dz:71", title=original.title,
                            record_type="single", release_date=date.today(), seen_at=stamp,
                            tracks_json=[original.model_dump(mode="json")]))

    assert await releases.fill_tracks(1, repair_credits=True) == 1
    assert await releases.fill_tracks(1, repair_credits=True) == 0
    async with session_scope() as s:
        row = await s.scalar(select(ArtistRelease).where(ArtistRelease.album_ref == "dz:71"))
        assert row.seen_at == stamp
        assert row.tracks_json[0]["artists"] == full.artists
