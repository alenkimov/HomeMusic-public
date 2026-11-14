"""«Это другой TAK»: releases the catalog files under an artist that are a namesake's leave that artist, for
everyone, and come back when brought back."""

from __future__ import annotations

from datetime import date, timedelta

from tests.test_plays import env as env  # the fixture


def album(id_: str, title: str, days_ago: int = 3):
    from app.catalog.models import CatalogAlbum

    return CatalogAlbum(ref=f"dz:{id_}", source="dz", id=id_, title=title, artist="TAK", artist_ref="dz:7",
                        record_type="album", release_date=date.today() - timedelta(days=days_ago), nb_tracks=2)


def track(id_: str, album_ref: str):
    from app.catalog.models import CatalogTrack

    return CatalogTrack(ref=f"dz:{id_}", source="dz", id=id_, title=id_, artist="TAK", artist_ref="dz:7", album_ref=album_ref)


async def test_a_namesakes_release_leaves_the_artist_and_comes_back(env):
    from sqlalchemy import select

    from app.catalog import namesakes
    from app.db import session_scope
    from app.models import ArtistRelease, User

    c = env["client"]
    own, others = album("71", "numb numb"), album("72", "Smoke and Beers")
    async with session_scope() as s:
        uid = (await s.execute(select(User.id))).scalar_one()
        for a in (own, others):  # both were news in the feed of a follower
            s.add(ArtistRelease(user_id=uid, artist_ref="dz:7", artist="TAK", album_ref=a.ref, title=a.title,
                                record_type="album", release_date=a.release_date))
    assert namesakes.own_albums("dz:7", [own, others]) == [own, others]

    async with session_scope() as s:
        assert await namesakes.exclude(s, uid, "dz:7", "TAK", [others]) == 1
        assert await namesakes.exclude(s, uid, "dz:7", "TAK", [others]) == 0  # said already
    # the catalog's client leaves it out of the artist's releases and popular tracks; another artist is untouched
    assert namesakes.own_albums("dz:7", [own, others]) == [own]
    assert [t.ref for t in namesakes.own_tracks("dz:7", [track("1", own.ref), track("2", others.ref)])] == ["dz:1"]
    assert namesakes.own_albums("dz:8", [own, others]) == [own, others]
    async with session_scope() as s:
        assert list((await s.execute(select(ArtistRelease.album_ref))).scalars()) == [own.ref]  # out of the feed

    listed = (await c.get("/api/catalog/namesakes")).json()
    assert [(x["artist"], x["album_ref"], x["title"], x["year"]) for x in listed] == [
        ("TAK", "dz:72", "Smoke and Beers", others.release_date.year)]

    # what is kept is what is read at start
    namesakes.forget()
    await namesakes.load()
    assert namesakes.of("dz:7") == {"dz:72"}

    r = await c.post("/api/catalog/namesakes/restore", json={"artist_ref": "dz:7", "album_refs": ["dz:72"]})
    assert r.json() == {"restored": 1, "total": 0}
    assert namesakes.own_albums("dz:7", [own, others]) == [own, others]
    assert (await c.get("/api/catalog/namesakes")).json() == []
