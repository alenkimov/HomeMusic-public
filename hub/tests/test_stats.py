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
    assert (await c.get("/api/stats/counts")).json() == {"ndK": 3, "ndX": 1}


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
