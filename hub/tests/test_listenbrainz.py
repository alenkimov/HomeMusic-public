"""ListenBrainz: an account a user may give — listens go there once each, its weekly playlist comes back."""

from __future__ import annotations

import json
from datetime import UTC, datetime, timedelta

import httpx
import respx

from tests.test_plays import env as env  # the fixture
from tests.test_plays import ev, post

API = "https://api.listenbrainz.org/1"


async def test_connect_send_once_and_the_weekly_playlist(env):
    from app import listenbrainz as lb

    c = env["client"]
    got: list[dict] = []

    def submit(request: httpx.Request) -> httpx.Response:
        assert request.headers["Authorization"] == "Token good-token"
        got.append(json.loads(request.content))
        return httpx.Response(200, json={"status": "ok"})

    with respx.mock(assert_all_called=False) as mock:
        mock.route(host="navidrome.test").pass_through()
        mock.get(f"{API}/validate-token").mock(side_effect=lambda r: httpx.Response(200, json=(
            {"valid": True, "user_name": "alen"} if r.headers["Authorization"] == "Token good-token" else {"valid": False})))
        mock.post(f"{API}/submit-listens").mock(side_effect=submit)
        mock.get(f"{API}/user/alen/playlists/createdfor").mock(return_value=httpx.Response(200, json={
            "playlist_count": 3, "count": 3, "offset": 0, "playlists": [
                {"playlist": {"identifier": "https://listenbrainz.org/playlist/old", "date": "2026-11-02T00:00:00+00:00",
                              "extension": {"https://musicbrainz.org/doc/jspf#playlist": {"additional_metadata": {"algorithm_metadata": {"source_patch": "weekly-exploration"}}}}}},
                {"playlist": {"identifier": "https://listenbrainz.org/playlist/new", "date": "2026-11-09T00:00:00+00:00",
                              "extension": {"https://musicbrainz.org/doc/jspf#playlist": {"additional_metadata": {"algorithm_metadata": {"source_patch": "weekly-exploration"}}}}}},
                {"playlist": {"identifier": "https://listenbrainz.org/playlist/jams", "date": "2026-11-10T00:00:00+00:00",
                              "extension": {"https://musicbrainz.org/doc/jspf#playlist": {"additional_metadata": {"algorithm_metadata": {"source_patch": "weekly-jams"}}}}}},
            ]}))
        mock.get(f"{API}/playlist/new").mock(return_value=httpx.Response(200, json={"playlist": {"track": [
            {"title": "Неизвестная", "creator": "Ноунейм", "duration": 201000,
             "identifier": ["https://musicbrainz.org/recording/11111111-2222-3333-4444-555555555555"]},
            {"title": "", "creator": "Без названия"}]}}))

        assert (await c.get("/api/listenbrainz")).json()["connected"] is False
        assert (await c.post("/api/listenbrainz/connect", json={"token": "wrong-token"})).status_code == 400
        r = (await c.post("/api/listenbrainz/connect", json={"token": "good-token"})).json()
        assert (r["connected"], r["user"], r["sent_until"]) == (True, "alen", None)

        # two listens: one long enough to count, one only touched
        at = datetime.now(UTC).replace(microsecond=0) - timedelta(hours=2)
        a = dict(nd_song_id="ndK", playback_id="p1", device_id="d1")
        b = dict(nd_song_id="ndK", playback_id="p2", device_id="d1")
        await post(c, *[ev("start", at=at, listened_ms=0, **a), ev("finish", at=at + timedelta(minutes=3), listened_ms=180000, **a),
                       ev("start", at=at + timedelta(minutes=5), listened_ms=0, **b),
                       ev("skip", at=at + timedelta(minutes=5, seconds=4), listened_ms=4000, **b)])

        r = (await c.post("/api/listenbrainz/send")).json()
        assert r["sent"] == 1 and r["sent_until"]
        (body,) = got
        assert body["listen_type"] == "import"
        (listen,) = body["payload"]
        assert listen["listened_at"] == int(at.timestamp())
        assert listen["track_metadata"]["artist_name"] and listen["track_metadata"]["track_name"]

        # nothing new: nothing is sent again; «again» sends the history anew
        assert (await c.post("/api/listenbrainz/send")).json()["sent"] == 0 and len(got) == 1
        assert (await c.post("/api/listenbrainz/send", params={"again": True})).json()["sent"] == 1 and len(got) == 2

        hits = await lb.weekly_exploration(1)
        assert [(h.artist, h.title, h.duration_s, h.source_id) for h in hits] == [
            ("Ноунейм", "Неизвестная", 201, "11111111-2222-3333-4444-555555555555")]

        r = (await c.delete("/api/listenbrainz")).json()
        assert r == {"connected": False, "user": None, "sent_until": None}
        assert await lb.weekly_exploration(1) == [] and await lb.sync_user(1) == 0
