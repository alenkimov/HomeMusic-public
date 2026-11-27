"""«Проверка сервисов»: what the real work says of each outside service, the nightly check of the rest, the fields
their answers stop coming with."""

from __future__ import annotations

from datetime import datetime, timedelta

import pytest

from tests.test_plays import env as env  # the fixture


def test_the_fields_of_an_answer():
    from app.checks import paths

    album = {"title": "Начальник Камчатки", "year": None, "cover": "", "tracks": [
        {"title": "Последний герой", "isrc": "RU1", "artists": [{"name": "Кино", "ref": None}]},
        {"title": "Бездельник", "isrc": None, "plays": 7},
    ]}
    assert paths(album) == {"title", "tracks", "tracks[].title", "tracks[].isrc", "tracks[].artists",
                            "tracks[].artists[].name", "tracks[].plays"}


def test_a_catalogs_record_is_the_check_of_its_catalog():
    from app.checks import check_of_cache_key as of

    assert of("dz:album3:302127") == "deezer.album"
    assert of("dz:search:track:10:кино") == "deezer.search"
    assert of("sp:album:abc") == "spotify.album" and of("sp:track:x") == "spotify.track"
    assert of("ym:search:кино") == "yandex.search" and of("mb:recordings:5:кино:группа") == "musicbrainz.recordings"
    assert of("mix:lfm_artist_tags:кино") == "lastfm.api" and of("lfm:artist_top:x") == "lastfm.api"
    assert of("hm:album:1") == "hitmoz.page"
    assert of("dz:chart_artists:30") is None and of("acoustic:doubles") is None and of("nothing") is None


def test_a_checks_state_by_its_latest_results():
    from app.api.checks import state_of

    assert state_of([]) == "none"
    assert state_of([(True, "live"), (False, "live"), (False, "live")]) == "ok"
    assert state_of([(False, "probe"), (True, "live")]) == "broken"  # the nightly check asked and it failed
    assert state_of([(False, "live"), (True, "live")]) == "flaky"  # one video's failure, maybe
    assert state_of([(False, "live")] * 3 + [(True, "live")]) == "broken"


def test_a_field_no_answer_has_had_for_a_while_is_gone():
    from app.api.checks import missing_of
    from app.models import ServiceShape

    now = datetime(2026, 11, 27, 12)
    old, recent = (now - timedelta(days=2)).isoformat(), (now - timedelta(hours=1)).isoformat()
    shape = ServiceShape(check="spotify.album", seen=100, paths={
        "title": [recent, 100],
        "tracks[].plays": [old, 60],  # 40 answers without it, for two days: gone
        "tracks[].plays.text": [old, 60],  # said by its parent
        "tracks[].isrc": [old, 95],  # only 5 answers since: an album without codes, maybe
        "label": [recent, 70],  # there an hour ago
    })
    assert missing_of(shape, now) == ["tracks[].plays"]
    assert missing_of(None, now) == []


class _Source:
    name = "hitmoz"

    def __init__(self, found=None, fail: Exception | None = None):
        self.found, self.fail = found or [], fail

    async def search(self, q, limit=10):
        if self.fail:
            raise self.fail
        return self.found

    async def fetch(self, c, dest_dir, on_progress=None):
        raise RuntimeError("HTTP 403")

    async def enrich(self, c):
        return c


async def _rows():
    from sqlalchemy import select

    from app.db import session_scope
    from app.models import ServiceCheck

    async with session_scope() as s:
        return list((await s.execute(select(ServiceCheck).order_by(ServiceCheck.id))).scalars())


@pytest.mark.asyncio
async def test_the_real_work_is_written_down_and_tells_the_state(env):
    from app import checks
    from app.providers.base import TrackQuery
    from tests.support import cand

    found = [cand("1", "https://hitmoz.test/f/1.mp3")]
    source = checks.watch(_Source(found))
    assert source.name == "hitmoz" and await source.enrich(found[0]) is found[0]  # anything else is the source's
    q = TrackQuery(artist="Кино", title="Группа крови")
    await source.search(q)
    await source.search(q)
    assert await checks.watch(_Source([])).search(q) == []  # nothing found tells nothing
    for _ in range(3):
        with pytest.raises(RuntimeError):
            await source.fetch(found[0], "/tmp")
    await checks.flush()
    rows = await _rows()
    oks = [r for r in rows if r.ok]
    assert [(r.check, r.count, r.source) for r in oks] == [("hitmoz.find", 2, "live")]  # a minute's successes: one row
    assert [(r.check, r.detail) for r in rows if not r.ok] == [("hitmoz.file", "HTTP 403")] * 3

    r = await env["client"].get("/api/admin/checks")
    assert r.status_code == 200, r.text
    body = r.json()
    hitmoz = next(x for x in body["services"] if x["key"] == "hitmoz")
    by = {c["key"]: c for c in hitmoz["checks"]}
    assert by["hitmoz.find"]["state"] == "ok" and by["hitmoz.find"]["source"] == "live"
    assert by["hitmoz.file"]["state"] == "broken" and by["hitmoz.file"]["detail"] == "HTTP 403"
    assert hitmoz["state"] == "broken" and hitmoz["nights"][-1] == "broken" and len(hitmoz["nights"]) == 14
    assert body["services"][0]["key"] == "hitmoz"  # the broken first
    assert body["attention"] == 1
    telegram = next(x for x in body["services"] if x["key"] == "telegram")
    assert telegram["state"] == "off" and telegram["checks"][0]["off"] == "бот не настроен"


@pytest.mark.asyncio
async def test_a_catalogs_answer_read_anew_is_its_check_with_its_fields(env):
    from app import checks
    from app.catalog.cache import cached, fresh
    from app.catalog.models import CatalogNotFound

    async def album():
        return {"title": "Группа крови", "tracks": [{"title": "Кукушка", "isrc": "RU1"}]}

    async def gone():
        raise CatalogNotFound("dz:album3:2")

    async def broken():
        raise RuntimeError("Deezer: invalid JSON")

    assert (await cached("dz:album3:1", 60, album))["title"] == "Группа крови"
    assert (await cached("dz:album3:1", 60, broken))["title"] == "Группа крови"  # from the cache: nothing asked
    with pytest.raises(CatalogNotFound):  # a record the catalog has not got: no failure of it
        await cached("dz:album3:2", 60, gone)
    token = fresh.set(True)
    try:
        with pytest.raises(RuntimeError):  # read anew past the cache: the catalog fails
            await cached("dz:album3:1", 60, broken)
    finally:
        fresh.reset(token)
    await checks.flush()
    rows = await _rows()
    assert [(r.check, r.ok, r.detail) for r in rows] == [("deezer.album", True, None), ("deezer.album", False, "Deezer: invalid JSON")]

    from sqlalchemy import select

    from app.db import session_scope
    from app.models import ServiceShape

    async with session_scope() as s:
        shape = (await s.execute(select(ServiceShape))).scalar_one()
    assert shape.check == "deezer.album" and set(shape.paths) == {"title", "tracks", "tracks[].title", "tracks[].isrc"}


@pytest.mark.asyncio
async def test_the_nightly_check_asks_only_what_the_day_did_not_show(env, monkeypatch):
    from app import checks
    from app.checks import probes

    asked: list[set[str]] = []

    async def deezer(rec):
        asked.append(set(rec.todo))
        await rec("deezer.search", True, "нашлось 5")
        await rec("deezer.album", False, "HTTP 500")
        checks.note("deezer.track", True)  # what the check itself reads is not the real work

    async def lyrics(rec):
        raise RuntimeError("lrclib: boom")

    monkeypatch.setattr(probes, "GROUPS", ((("deezer.search", "deezer.album", "deezer.track"), deezer),
                                           (("lyrics.lrclib",), lyrics)))

    async def none():
        return {}

    monkeypatch.setattr(probes, "unavailable", none)
    checks.note("deezer.track", True)  # the day showed it working
    await checks.flush()
    status = await probes.run()
    assert asked == [{"deezer.search", "deezer.album"}] and status["asked"] == 3 and status["force"] is False
    rows = [(r.check, r.ok, r.source, r.detail) for r in await _rows()]
    assert rows == [("deezer.track", True, "live", None), ("deezer.search", True, "probe", "нашлось 5"),
                    ("deezer.album", False, "probe", "HTTP 500"), ("lyrics.lrclib", False, "probe", "lrclib: boom")]
    await probes.run(force=True)  # «Проверить сейчас»: everything
    assert asked[-1] == {"deezer.search", "deezer.album", "deezer.track"}

    body = (await env["client"].get("/api/admin/checks")).json()
    assert body["checked_at"] and body["running"] is False
    deezer_ = next(x for x in body["services"] if x["key"] == "deezer")
    assert {c["key"]: c["state"] for c in deezer_["checks"]}["deezer.album"] == "broken"


def test_the_next_night():
    from datetime import UTC
    from zoneinfo import ZoneInfo

    from app.checks.probes import next_night

    zone = ZoneInfo("Europe/Moscow")
    assert next_night(datetime(2026, 11, 27, 0, 30, tzinfo=UTC), zone) == 30 * 60  # 03:30 in Moscow: half an hour
    assert next_night(datetime(2026, 11, 27, 2, 0, tzinfo=UTC), zone) == 23 * 3600  # 05:00: tomorrow
