"""Lyrics: the formats, the order of the sources, the cache in the database, the API. The sources are fakes
(`providers.PROVIDERS`); what each real one parses is checked on samples of its format."""

from __future__ import annotations

import uuid

import httpx
import pytest
import respx

from tests.support import app_client

LRC = "[ti:Song]\n[00:01.50]первая\n[00:03.00][00:09.00]припев\n[00:05.25]<00:05.25>два <00:05.90>слова\n"


def test_lrc_lines_repeats_and_word_stamps():
    from app.lyrics import parse

    lines = parse.parse_lrc(LRC)
    assert [(x["t"], x["text"]) for x in lines] == [(1500, "первая"), (3000, "припев"), (5250, "два слова"), (9000, "припев")]
    assert lines[2]["words"] == [{"t": 5250, "text": "два "}, {"t": 5900, "text": "слова"}]
    assert parse.kind_of(lines, None) == "word" and parse.kind_of(lines[:2], None) == "synced"
    assert parse.kind_of(None, "text") == "plain" and parse.kind_of(None, " ") == "none"
    assert parse.plain_of(lines[:2], None) == "первая\nприпев"
    assert not parse.has_timestamps("just a text") and parse.has_timestamps(LRC)


def test_netease_and_lrclib_formats():
    from app.lyrics import parse

    # NetEase: JSON credit lines and «作词 : …» lines head the text and are not sung
    lrc = '{"t":0,"c":[{"tx":"作词: "},{"tx":"x"}]}\n[00:00.00]作词 : A\n[00:01.00]作曲 : B\n[00:07.71]ちょけろ\n[00:08.81]ちょけろ'
    assert [x["t"] for x in parse.parse_lrc(lrc)] == [7710, 8810]
    yrc = "[100,900](100,400,0)He(500,500,0)llo\n[2000,500](2000,500,0)x"
    lines = parse.parse_yrc(yrc)
    assert lines[0] == {"t": 100, "end": 1000, "text": "Hello", "words": [{"t": 100, "text": "He"}, {"t": 500, "text": "llo"}]}
    assert "words" not in lines[1]
    doc = {"lines": [{"text": "a b", "start_ms": 10, "end_ms": 90, "words": [{"text": "a ", "start_ms": 10}, {"text": "b", "start_ms": 50}]},
                     {"text": "one", "start_ms": 100, "words": [{"text": "one", "start_ms": 100}]}, {"text": " ", "start_ms": 5}]}
    lines = parse.parse_lyricsfile(doc)
    assert [x["text"] for x in lines] == ["a b", "one"] and len(lines[0]["words"]) == 2 and "words" not in lines[1]


def test_a_long_text_follows_the_audio_as_collapsed_quotes():
    from app.telegram import lyrics_messages

    assert lyrics_messages("") == (None, [])
    caption, after = lyrics_messages("a <b>\nline")
    assert caption == "<blockquote expandable>a &lt;b&gt;\nline</blockquote>" and after == []
    caption, after = lyrics_messages("\n".join(f"line {i} " + "x" * 60 for i in range(400)))
    assert caption is None and len(after) == 3 and all(len(m) <= 4096 for m in after) and after[-1].endswith("…</blockquote>")


@pytest.fixture
async def env(hub_env, monkeypatch):
    from app.lyrics import providers
    from app.main import create_app

    calls: list[str] = []
    answers: dict[str, object] = {}

    def fake(source):
        async def ask(q):
            calls.append(source)
            a = answers.get(source)
            if isinstance(a, Exception):
                raise a
            return a
        return ask

    monkeypatch.setattr(providers, "PROVIDERS", {s: fake(s) for s in providers.ORDER})
    app = create_app()
    async with app.router.lifespan_context(app):
        with respx.mock(assert_all_called=False) as mock:
            mock.route(host="navidrome.test").mock(return_value=httpx.Response(200, json={"subsonic-response": {
                "status": "ok", "version": "1.16.1", "user": {"adminRole": True}}}))
            async with app_client(app) as client:
                yield {"client": client, "calls": calls, "answers": answers}


async def add_track(nd_song_id: str = "nd1") -> int:
    from app.db import session_scope
    from app.matching.normalize import fingerprint
    from app.models import Track

    async with session_scope() as s:
        t = Track(uid=str(uuid.uuid4()), path=f"Кино/{nd_song_id}.mp3", nd_song_id=nd_song_id, artist="Кино, Гость",
                  title="Группа крови", duration_s=245, fingerprint=fingerprint("Кино", nd_song_id))
        s.add(t)
        await s.flush()
        return t.id


async def test_sources_in_order_then_the_database(env):
    from app.lyrics.providers import Found

    c, calls, answers = env["client"], env["calls"], env["answers"]
    track_id = await add_track()
    assert (await c.get("/api/lyrics", params={"ref": "dz:404"})).json()["track_id"] is None  # not a library track

    # a plain text is passed over while a later source has a timed one; a failing source is skipped
    answers.update(lrclib=RuntimeError("down"), netease=Found("netease", lines=[{"t": 1000, "text": "строка"}]),
                   ovh=Found("ovh", plain="просто текст"))
    r = (await c.get("/api/lyrics", params={"nd_song_id": "nd1"})).json()
    assert (r["track_id"], r["kind"], r["source"], r["source_title"]) == (track_id, "synced", "netease", "NetEase")
    assert r["lines"] == [{"t": 1000, "text": "строка"}] and calls == ["file", "lrclib", "netease"]

    calls.clear()
    assert (await c.get("/api/lyrics", params={"nd_song_id": "nd1"})).json()["kind"] == "synced"
    assert calls == []  # the second time it comes from the database

    # the search inside the texts: any case, the line that has the words; `%` is a character, not a wildcard
    hits = (await c.get("/api/lyrics/search", params={"q": "СТРОК"})).json()
    assert hits == [{"track_id": track_id, "nd_song_id": "nd1", "ref": None, "line": "строка"}]
    assert (await c.get("/api/lyrics/search", params={"q": "нет такого"})).json() == []
    assert (await c.get("/api/lyrics/search", params={"q": "%"})).json() == []
    assert (await c.get("/api/lyrics/search", params={"q": "с"})).json() == []  # one letter is not a search

    r = (await c.post("/api/lyrics/offset", json={"track_id": track_id, "offset_ms": -400})).json()
    assert r["offset_ms"] == -400
    assert (await c.post("/api/lyrics/offset", json={"track_id": track_id, "offset_ms": 99_999})).status_code == 422


async def test_not_found_is_remembered_and_a_choice_is_kept(env):
    from app.lyrics.providers import Found

    c, calls, answers = env["client"], env["calls"], env["answers"]
    track_id = await add_track()
    r = (await c.get("/api/lyrics", params={"nd_song_id": "nd1"})).json()
    assert r["kind"] == "none" and len(calls) == 4
    calls.clear()
    assert (await c.get("/api/lyrics", params={"nd_song_id": "nd1"})).json()["kind"] == "none" and calls == []

    answers.update(lrclib=Found("lrclib", lines=[{"t": 0, "text": "a"}]), ovh=Found("ovh", plain="b\nc"))
    found = (await c.get("/api/lyrics/candidates", params={"track_id": track_id})).json()
    assert [(x["source"], x["kind"], x["preview"]) for x in found] == [("lrclib", "synced", "a"), ("ovh", "plain", "b\nc")]

    # «Другой вариант» asks each source on its own: what it has, or that it failed, and how long it took
    assert [x["id"] for x in (await c.get("/api/lyrics/sources")).json()] == ["file", "lrclib", "netease", "ovh"]
    r = (await c.get("/api/lyrics/probe", params={"track_id": track_id, "source": "ovh"})).json()
    assert (r["status"], r["kind"], r["preview"], r["source_title"]) == ("found", "plain", "b\nc", "lyrics.ovh") and r["seconds"] >= 0
    assert (await c.get("/api/lyrics/probe", params={"track_id": track_id, "source": "netease"})).json()["status"] == "none"
    answers["netease"] = RuntimeError("down")
    assert (await c.get("/api/lyrics/probe", params={"track_id": track_id, "source": "netease"})).json()["status"] == "error"
    answers.pop("netease")
    assert (await c.get("/api/lyrics/probe", params={"track_id": track_id, "source": "bogus"})).status_code == 400

    r = (await c.post("/api/lyrics/choose", json={"track_id": track_id, "source": "ovh"})).json()
    assert (r["kind"], r["source"], r["plain"], r["pinned"]) == ("plain", "ovh", "b\nc", True)
    assert (await c.post("/api/lyrics/choose", json={"track_id": track_id, "source": "netease"})).status_code == 404
    r = (await c.post("/api/lyrics/choose", json={"track_id": track_id, "source": None})).json()
    assert (r["kind"], r["pinned"]) == ("none", True)  # «no lyrics for this track» stays
    r = (await c.post("/api/lyrics/reset", json={"track_id": track_id})).json()
    assert (r["kind"], r["source"], r["pinned"]) == ("synced", "lrclib", False)  # «Вернуть текст»: found again


async def test_settings_order_and_the_background_scan(env):
    from sqlalchemy import select

    from app.db import session_scope
    from app.jobs.queue import run_job
    from app.lyrics.providers import Found
    from app.models import Job

    c, calls, answers = env["client"], env["calls"], env["answers"]
    await add_track("nd1")
    await add_track("nd2")
    r = (await c.get("/api/lyrics/settings")).json()
    assert [x["id"] for x in r["sources"]] == ["file", "lrclib", "netease", "ovh"] and r["on_add"] and r["retry_days"] == 7
    assert r["stats"] == {"total": 2, "word": 0, "synced": 0, "plain": 0, "none": 0, "unchecked": 2}

    r = (await c.put("/api/lyrics/settings", json={"sources": [{"id": "ovh", "on": True}, {"id": "lrclib", "on": False},
                                                              {"id": "bogus", "on": True}], "retry_days": 30, "on_add": False})).json()
    assert [(x["id"], x["on"]) for x in r["sources"]] == [("ovh", True), ("lrclib", False), ("file", True), ("netease", True)]
    assert r["retry_days"] == 30 and r["on_add"] is False

    answers["ovh"] = Found("ovh", plain="text")
    assert (await c.post("/api/lyrics/scan")).status_code == 200  # the admin's own scan runs with `on_add` off
    async with session_scope() as s:
        job = (await s.execute(select(Job).where(Job.kind == "lyrics_scan"))).scalar_one()
        job.status = "running"
    await run_job(job)
    assert "lrclib" not in calls and calls.count("ovh") == 2  # a source switched off is not asked
    assert (await c.get("/api/lyrics/settings")).json()["stats"] == {"total": 2, "word": 0, "synced": 0, "plain": 2, "none": 0, "unchecked": 0}
