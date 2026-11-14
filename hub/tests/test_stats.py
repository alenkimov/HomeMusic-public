"""«Статистика»: the tops of a period, the «Прослушивания» numbers, the history of Spotify.

The library has «Кино — Группа крови» (dz:100, "ndK", Spotify id "spK", genre Rock) and «Кто-то — Своё» ("ndX").
"""

from __future__ import annotations

import io
import json
import zipfile
from datetime import UTC, datetime, timedelta

import pytest
from sqlalchemy import select

from tests.test_plays import add_track, ev, post
from tests.test_plays import env as env  # the fixture


@pytest.fixture
async def c(env):
    from app import tags
    from app.db import session_scope
    from app.models import Track, TrackLink

    async with session_scope() as s:
        t = (await s.execute(select(Track).where(Track.nd_song_id == "ndK"))).scalar_one()
        t.genre, t.album = "Rock", "Группа крови"
        s.add(TrackLink(track_id=t.id, service="spotify", external_id="spK"))
        await s.flush()
        await tags.sync(s)
    return env["client"]


def listen(song: str, ms: int, at: datetime, n: str) -> list[dict]:
    a = dict(nd_song_id=song, playback_id=f"p{n}", device_id="d1")
    return [ev("start", at=at, listened_ms=0, **a), ev("finish" if ms >= 30000 else "skip", at=at + timedelta(milliseconds=ms), listened_ms=ms, **a)]


async def test_tops_of_a_period_and_the_column(c):
    now = datetime.now(UTC)
    await post(c, *listen("ndK", 200_000, now - timedelta(hours=3), "1"), *listen("ndK", 60_000, now - timedelta(hours=2), "2"),
               *listen("ndX", 240_000, now - timedelta(hours=1), "3"), *listen("ndX", 5_000, now - timedelta(minutes=30), "4"),
               *listen("ndK", 100_000, now - timedelta(days=40), "5"),
               *listen("ndX", 900, now - timedelta(minutes=5), "6"))  # leafed through: not even a skip

    r = (await c.get("/api/stats", params={"period": "week"})).json()
    assert r["summary"] == {"ms": 505_000, "plays": 3, "tracks": 2, "streak": 1, "skips": 1, "attempts": 4}
    assert [(x["name"], x["sub"], x["plays"], x["nd_song_id"]) for x in r["tracks"]["items"]] == [
        ("Группа крови", "Кино", 2, "ndK"), ("Своё", "Кто-то", 1, "ndX")]
    assert [(x["name"], x["plays"]) for x in r["artists"]["items"]] == [("Кино", 2), ("Кто-то", 1)]
    assert [(x["name"], x["plays"]) for x in r["genres"]["items"]] == [("Rock", 2)]  # a track without a genre is not there
    assert [(x["name"], x["sub"]) for x in r["albums"]["items"]] == [("Группа крови", "Кино")]
    assert sum(r["hours"]) == sum(r["weekdays"]) == 3

    # by time the order is another, and a skip adds its seconds
    r = (await c.get("/api/stats", params={"period": "week", "by": "time", "limit": 1})).json()
    assert [(x["name"], x["ms"]) for x in r["tracks"]["items"]] == [("Группа крови", 260_000)] and r["tracks"]["total"] == 2
    assert sum(r["hours"]) == 505_000

    assert (await c.get("/api/stats", params={"period": "all"})).json()["summary"]["plays"] == 4
    # the last 8 weeks of a track, and its place in the period before (the listen of 40 days ago is week 2 of 0…7)
    top = r["tracks"]["items"][0]
    assert (top["weeks"], top["was"], top["fresh"]) == ([0, 0, 1, 0, 0, 0, 0, 2], None, True)
    month = (await c.get("/api/stats", params={"period": "month"})).json()["tracks"]["items"]
    assert [(x["name"], x["was"], x["fresh"]) for x in month] == [("Группа крови", 1, False), ("Своё", None, True)]
    one = (await c.get("/api/stats/track", params={"nd_song_id": "ndK"})).json()
    assert (one["plays"], one["ms"], one["weeks"]) == (3, 360_000, [0, 0, 1, 0, 0, 0, 0, 2]) and one["first_at"] < one["last_at"]
    assert (await c.get("/api/stats/track", params={"nd_song_id": "nope"})).json()["plays"] == 0
    assert (await c.get("/api/stats/counts")).json() == {"ndK": 3, "ndX": 1}


async def test_auto_playlists(c):
    now = datetime.now(UTC)
    old = [x for i in range(3) for x in listen("ndX", 100_000, now - timedelta(days=200 + i), f"o{i}")]
    await post(c, *old, *listen("ndK", 100_000, now - timedelta(days=100), "a"),
               *listen("ndK", 100_000, now - timedelta(days=2), "b"), *listen("ndK", 100_000, now - timedelta(days=1), "c"))

    async def auto(kind: str) -> list:
        return [(x["nd_song_id"], x["plays"]) for x in (await c.get(f"/api/stats/auto/{kind}")).json()["items"]]

    assert await auto("top-month") == [("ndK", 2)]
    assert await auto("top-year") == [("ndK", 3), ("ndX", 3)]  # as many: the one listened to later is first
    assert await auto("top-all") == [("ndK", 3), ("ndX", 3)]
    assert await auto("forgotten") == [("ndX", 3)]  # much, and not once in three months
    assert (await c.get("/api/stats/auto/nope")).status_code == 404


def entry(ts: str, ms: int, title: str, artist: str, uri: str | None, album: str = "A") -> dict:
    return {"ts": ts, "ms_played": ms, "master_metadata_track_name": title, "master_metadata_album_artist_name": artist,
            "master_metadata_album_album_name": album, "spotify_track_uri": uri}


async def test_the_history_of_spotify(c):
    year = datetime.now(UTC).year
    rows = [
        entry(f"{year - 1}-03-01T10:00:00Z", 200_000, "Gruppa krovi", "Kino", "spotify:track:spK"),  # by its Spotify id
        entry(f"{year - 1}-03-01T10:00:40Z", 200_000, "Gruppa krovi", "Kino", "spotify:track:spK"),  # the same listen twice
        entry(f"{year - 1}-03-02T10:00:00Z", 100_000, "Своё", "Кто-то", "spotify:track:other"),  # no link: not the library's
        entry(f"{year - 1}-03-03T10:00:00Z", 9_000, "Elsewhere", "Nobody", "spotify:track:zz"),  # a skip
        {"ts": f"{year - 1}-03-04T10:00:00Z", "ms_played": 50_000, "master_metadata_track_name": None,
         "episode_name": "A podcast", "spotify_episode_uri": "spotify:episode:1"},
    ]
    short = [{"endTime": f"{year - 1}-03-05 11:00", "artistName": "Кто-то", "trackName": "Своё", "msPlayed": 90_000}]
    archive = io.BytesIO()
    with zipfile.ZipFile(archive, "w") as z:
        z.writestr("Spotify Extended Streaming History/Streaming_History_Audio_2024_0.json", json.dumps(rows))
        z.writestr("Spotify Extended Streaming History/Streaming_History_Video_2024.json", json.dumps(rows))
        z.writestr("Spotify Extended Streaming History/ReadMeFirst.pdf", b"%PDF")
    files = [("files", ("my_spotify_data.zip", archive.getvalue(), "application/zip")),
             ("files", ("StreamingHistory_music_0.json", json.dumps(short).encode(), "application/json"))]

    r = await c.post("/api/stats/import", files=files)
    assert r.status_code == 200, r.text
    assert r.json() == {"found": 5, "added": 4, "duplicates": 1, "linked": 2, "outside": 2, "listens": 3}
    # again: nothing new
    assert (await c.post("/api/stats/import", files=files)).json()["added"] == 0

    r = (await c.get("/api/stats", params={"period": "all"})).json()
    assert r["imported"] == 4
    assert {(x["name"], x["plays"], x["nd_song_id"]) for x in r["tracks"]["items"]} == {
        ("Группа крови", 1, "ndK"),  # shown as the library has it
        ("Своё", 1, "ndX"),  # the short export has no ids: by artist and title
        ("Своё", 1, None)}  # an id the library has no link for: only counted
    assert (await c.get("/api/stats", params={"period": "month"})).json()["summary"]["attempts"] == 0
    assert (await c.get("/api/stats/counts")).json() == {"ndK": 1, "ndX": 1}

    assert (await c.post("/api/stats/import", files=[("files", ("x.json", b"{\"a\": 1}", "application/json"))])).status_code == 400
    assert (await c.delete("/api/stats/import")).json()["removed"] == 4
    assert (await c.get("/api/stats/counts")).json() == {}


async def test_a_navidrome_scrobble_is_a_listen(c):
    from app.db import session_scope
    from app.models import PlayEvent, User, utcnow

    async with session_scope() as s:
        user = (await s.execute(select(User))).scalars().first()
        s.add(PlayEvent(user_id=user.id, event_id="nd:1", ts=utcnow() - timedelta(hours=1), kind="finish", nd_song_id="ndK",
                        client="navidrome", duration_s=245))
    r = (await c.get("/api/stats", params={"period": "week"})).json()
    assert (r["summary"]["plays"], r["summary"]["ms"]) == (1, 245_000)
    assert await add_track("Новый", "Трек", "ndN") and (await c.get("/api/stats/counts")).json() == {"ndK": 1}


async def test_a_catalog_track_listened_to_becomes_a_track_and_counts_as_one(c):
    """30 seconds of a catalog track the Hub had not met: it is a track from then on — in the statistics, and one
    to tag. A track only leafed through is not."""
    from sqlalchemy import select

    from app.db import session_scope
    from app.models import Track

    at = datetime.now(UTC).replace(microsecond=0) - timedelta(hours=1)
    meta = {"title": "Новая песня", "artist_credit": "Ноунейм", "album": "Альбом", "duration_s": 200}
    a = dict(ref="hm:777", playback_id="pa", device_id="d1", track_metadata=meta)
    b = dict(ref="hm:888", playback_id="pb", device_id="d1", track_metadata={**meta, "title": "Пролистал"})
    await post(c, ev("start", at=at, listened_ms=0, **a), ev("finish", at=at + timedelta(seconds=200), listened_ms=200000, **a),
               ev("start", at=at + timedelta(minutes=5), listened_ms=0, **b),
               ev("skip", at=at + timedelta(minutes=5, seconds=5), listened_ms=5000, **b))
    async with session_scope() as s:
        tracks = {t.ref: t for t in (await s.execute(select(Track).where(Track.ref.in_(["hm:777", "hm:888"])))).scalars()}
    assert set(tracks) == {"hm:777"} and (tracks["hm:777"].title, tracks["hm:777"].artist) == ("Новая песня", "Ноунейм")
    assert tracks["hm:777"].path is None  # known, with no file

    top = (await c.get("/api/stats", params={"period": "week", "limit": 5})).json()["tracks"]["items"]
    assert [(x["name"], x["plays"]) for x in top if x["name"] == "Новая песня"] == [("Новая песня", 1)]
    tag = (await c.post("/api/tags", json={"name": "Услышал"})).json()["id"]
    r = await c.post("/api/tags/assign", json={"track_ids": [tracks["hm:777"].id], "add": [tag], "remove": []})
    assert tag in r.json()["map"][str(tracks["hm:777"].id)]


async def test_lists_of_a_rule_are_playlists_of_navidrome(c, monkeypatch):
    """«Для вас»: the tops and «Давно не слушал» are written to Navidrome as playlists — made once there are songs,
    rewritten only when the songs change."""
    from app import for_you
    from app.db import session_scope
    from app.models import ForYouList, User

    now = datetime.now(UTC)
    await post(c, *listen("ndK", 100_000, now - timedelta(days=2), "a"), *listen("ndK", 100_000, now - timedelta(days=1), "b"))
    put: list[tuple] = []

    async def fake_put(user, playlist_id, kind, songs):
        put.append((playlist_id, kind, list(songs)))
        return playlist_id or f"pl-{kind}"

    monkeypatch.setattr(for_you, "put", fake_put)
    async with session_scope() as s:
        user_id = (await s.execute(select(User.id))).scalars().first()
    assert await for_you.refresh(user_id) == 3
    assert put == [(None, "top-month", ["ndK"]), (None, "top-year", ["ndK"]), (None, "top-all", ["ndK"])]  # «Давно не слушал» is empty: no playlist
    async with session_scope() as s:
        rows = {r.kind: (r.nd_playlist_id, r.songs_json) for r in (await s.execute(select(ForYouList))).scalars()}
        assert rows["top-month"] == ("pl-top-month", ["ndK"]) and rows["forgotten"] == (None, [])
        assert await for_you.playlist_ids(s) == {"pl-top-month", "pl-top-year", "pl-top-all"}
    assert await for_you.refresh(user_id) == 0 and len(put) == 3  # nothing changed: nothing is written

    await post(c, *listen("ndX", 100_000, now - timedelta(hours=1), "x"))
    assert await for_you.refresh(user_id) == 3
    assert put[3] == ("pl-top-month", "top-month", ["ndK", "ndX"])  # the same playlist, its songs replaced


async def test_an_issue_of_discoveries_is_a_mix_of_the_week(c, monkeypatch):
    """«Открытия недели»: the issue's tracks are downloaded quietly and written to a playlist of their own; a new
    issue closes the previous one and leaves the daily mix alone."""
    from types import SimpleNamespace

    from app import for_you
    from app.catalog.models import CatalogTrack
    from app.db import session_scope
    from app.library.owned import quiet
    from app.mixes import core
    from app.models import DownloadItem, Mix, Track, User

    put: list[tuple] = []

    async def fake_put(user, playlist_id, kind, songs):
        put.append((kind, list(songs)))
        return "pl-weekly"

    monkeypatch.setattr(for_you, "put", fake_put)

    def cand(i: int, song: str | None = None, track_id: int | None = None):
        t = CatalogTrack(ref=f"dz:{900 + i}", source="dz", id=str(900 + i), title=f"Новое {i}", artist="Некто", duration_s=200)
        return SimpleNamespace(track=t, nd_song_id=song, library_track_id=track_id, generator="ytm_radio", score=0.5,
                               features=lambda t=t: {"track": t.model_dump(mode="json")})

    async with session_scope() as s:
        user_id = (await s.execute(select(User.id))).scalars().first()
        owned = (await s.execute(select(Track.id).where(Track.nd_song_id == "ndK"))).scalar_one()
        daily = Mix(user_id=user_id, title="Микс", mode="normal", status="active")
        s.add(daily)
        await s.flush()
        daily_id = daily.id

    first = await core.save_weekly(user_id, [cand(1), cand(2, "ndK", owned)], issue_id=1, description="Неделя")
    assert put == [("weekly", ["ndK"])]  # what the library has is in the playlist at once
    async with session_scope() as s:
        mix = await s.get(Mix, first)
        assert (mix.kind, mix.nd_playlist_id, mix.status) == ("weekly", "pl-weekly", "active")
        dls = list((await s.execute(select(DownloadItem))).scalars())
        assert [d.query_json["meta"]["track_ref"] for d in dls] == ["dz:901"] and quiet(dls[0].query_json)
        assert (await core.current_mix(s, user_id)).id == daily_id  # «Микс дня» is still the daily one

    second = await core.save_weekly(user_id, [cand(3)], issue_id=2, description="Неделя")
    async with session_scope() as s:
        assert (await s.get(Mix, first)).status == "closed" and (await s.get(Mix, second)).status == "active"
        assert (await s.get(Mix, daily_id)).status == "active"  # a new issue closes issues, not the daily mix
        left = {d.query_json["meta"]["track_ref"]: d.status for d in (await s.execute(select(DownloadItem))).scalars()}
        assert left["dz:901"] == "cancelled" and left["dz:903"] == "queued"  # the old issue's download not started is dropped
