"""Lyrics: the formats, the order of the sources, the cache in the database, the API. The sources are fakes
(`providers.PROVIDERS`); what each real one parses is checked on samples of its format."""

from __future__ import annotations

import uuid

import httpx
import pytest
import respx

from tests.support import app_client, by_songs, filed_track, tids

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

    async with session_scope() as s:
        t = filed_track(uid=str(uuid.uuid4()), path=f"Кино/{nd_song_id}.mp3", nd_song_id=nd_song_id, artist="Кино, Гость",
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
    # the sources that may have the words timed are still asked; one that only has plain texts no longer is
    assert r["lines"] == [{"t": 1000, "text": "строка"}] and calls == ["file", "lrclib", "lrcred", "lyricsplus", "netease"]

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
    assert r["kind"] == "none" and len(calls) == 6
    calls.clear()
    assert (await c.get("/api/lyrics", params={"nd_song_id": "nd1"})).json()["kind"] == "none" and calls == []

    answers.update(lrclib=Found("lrclib", lines=[{"t": 0, "text": "a"}]), ovh=Found("ovh", plain="b\nc"))
    found = (await c.get("/api/lyrics/candidates", params={"track_id": track_id})).json()
    assert [(x["source"], x["kind"], x["preview"]) for x in found] == [("lrclib", "synced", "a"), ("ovh", "plain", "b\nc")]

    # «Другой вариант» asks each source on its own: what it has, or that it failed, and how long it took
    assert [x["id"] for x in (await c.get("/api/lyrics/sources")).json()] == ["file", "lrclib", "lrcred", "lyricsplus", "netease", "ovh"]
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
    assert [x["id"] for x in r["sources"]] == ["file", "lrclib", "lrcred", "lyricsplus", "netease", "ovh"] and r["on_add"] and r["retry_days"] == 7
    assert r["stats"] == {"total": 2, "word": 0, "synced": 0, "plain": 0, "none": 0, "unchecked": 2}

    r = (await c.put("/api/lyrics/settings", json={"sources": [{"id": "ovh", "on": True}, {"id": "lrclib", "on": False},
                                                              {"id": "bogus", "on": True}], "retry_days": 30, "on_add": False})).json()
    assert [(x["id"], x["on"]) for x in r["sources"]] == [("ovh", True), ("lrclib", False), ("file", True), ("lrcred", True), ("lyricsplus", True), ("netease", True)]
    assert r["retry_days"] == 30 and r["on_add"] is False

    answers["ovh"] = Found("ovh", plain="text")
    assert (await c.post("/api/lyrics/scan")).status_code == 200  # the admin's own scan runs with `on_add` off
    async with session_scope() as s:
        job = (await s.execute(select(Job).where(Job.kind == "lyrics_scan"))).scalar_one()
        job.status = "running"
    await run_job(job)
    assert "lrclib" not in calls and calls.count("ovh") == 2  # a source switched off is not asked
    assert (await c.get("/api/lyrics/settings")).json()["stats"] == {"total": 2, "word": 0, "synced": 0, "plain": 2, "none": 0, "unchecked": 0}


def test_the_language_of_a_text_is_told_by_its_letters():
    from app.lyrics.translate import detect

    assert detect("あぁ 逃げ出した猫の後を追いかけて\n天気が良い") == "ja"
    assert detect("天气很好 我们一起走") == "zh" and detect("사랑해요 그대를") == "ko"
    assert detect("Тёплое место, но улицы ждут") == "ru" and detect("Ще не вмерла України і слава, і воля її") == "uk"
    assert detect("And I could leave it on a shelf, or keep it to myself, you know") == "en"
    assert detect("Je ne regrette rien, ni le bien qu'on m'a fait") is None  # Latin, not English: not told
    assert detect("♪ 123") is None
    # a Japanese song with an English hook is Japanese
    assert detect("君はふてぶてしくつぶやいた\n" * 6 + "oh baby") == "ja"


async def test_a_translation_is_made_once_kept_per_language_and_corrected_by_hand(env, monkeypatch):
    from app.lyrics import translate
    from app.lyrics.providers import Found

    c, answers = env["client"], env["answers"]
    track_id = await add_track()
    asked: list[tuple[str, str, list[str]]] = []

    async def fake_llm(lines, target, **kw):
        asked.append(("llm", target, list(lines)))
        if target == "de":
            raise translate.TranslateError("ИИ не ответил вовремя")
        return [f"{target}:{x}" for x in lines]

    async def fake_google(lines, target):
        asked.append(("google", target, list(lines)))
        return [f"g-{target}:{x}" for x in lines]

    monkeypatch.setattr(translate, "llm", fake_llm)
    monkeypatch.setattr(translate, "google", fake_google)
    answers["lrclib"] = Found("lrclib", lines=[{"t": 0, "text": "君はつぶやいた"}, {"t": 5, "text": ""},
                                                 {"t": 9, "text": "猫の後を"}, {"t": 12, "text": "君はつぶやいた"}])
    r = (await c.get("/api/lyrics/translation", params={"track_id": track_id, "lang": "ru"})).json()
    assert (r["status"], r["detected"], r["provider"], r["provider_title"]) == ("ready", "ja", "llm", "ИИ Хаба")
    assert r["lines"] == ["ru:君はつぶやいた", "", "ru:猫の後を", "ru:君はつぶやいた"]  # line for line; empty stays empty
    assert asked == [("llm", "ru", ["君はつぶやいた", "猫の後を"])]  # a repeated line is sent once

    asked.clear()
    assert (await c.get("/api/lyrics/translation", params={"track_id": track_id, "lang": "ru"})).json()["lines"] == r["lines"]
    assert asked == []  # the second time it comes from the database
    # another language is another translation; a translator that fails gives way to the next
    r = (await c.get("/api/lyrics/translation", params={"track_id": track_id, "lang": "de"})).json()
    assert (r["provider"], r["lines"][0]) == ("google", "g-de:君はつぶやいた")
    # the song's own language, and a language the listener reads, are left alone — unless asked
    assert (await c.get("/api/lyrics/translation", params={"track_id": track_id, "lang": "ja"})).json()["status"] == "same"
    q = {"track_id": track_id, "lang": "en", "skip": "ja,ru"}
    assert (await c.get("/api/lyrics/translation", params=q)).json()["status"] == "skipped"
    assert (await c.get("/api/lyrics/translation", params={**q, "force": True})).json()["status"] == "ready"
    assert (await c.get("/api/lyrics/translation", params={"track_id": track_id, "lang": "xx"})).status_code == 400

    # a line corrected by hand stays through a new translation
    r = (await c.put("/api/lyrics/translation/line", json={"track_id": track_id, "lang": "ru", "index": 2, "text": " вслед за котом "})).json()
    assert r["lines"][2] == "вслед за котом" and r["edited"] == [2]
    r = (await c.post("/api/lyrics/translation/redo", json={"track_id": track_id, "lang": "ru", "provider": "google"})).json()
    assert (r["provider"], r["lines"][0], r["lines"][2], r["edited"]) == ("google", "g-ru:君はつぶやいた", "вслед за котом", [2])
    assert (await c.put("/api/lyrics/translation/line", json={"track_id": track_id, "lang": "ru", "index": 9, "text": "x"})).status_code == 404

    # another text for the track: its translation is made again
    answers["ovh"] = Found("ovh", plain="one\ntwo")
    await c.post("/api/lyrics/choose", json={"track_id": track_id, "source": "ovh"})
    r = (await c.get("/api/lyrics/translation", params={"track_id": track_id, "lang": "ru"})).json()
    assert (r["lines"], r["edited"]) == (["ru:one", "ru:two"], [])

    # the admin's order of translators
    r = (await c.get("/api/lyrics/translators")).json()
    assert [x["id"] for x in r["providers"]] == ["llm", "google", "mymemory"] and {"code": "en", "title": "English"} in r["langs"]
    r = (await c.put("/api/lyrics/translators", json={"providers": [{"id": "google", "on": True}, {"id": "llm", "on": False}]})).json()
    assert [(x["id"], x["on"]) for x in r["providers"]] == [("google", True), ("llm", False), ("mymemory", True)]


def test_a_song_may_be_in_several_languages():
    from app.lyrics.translate import languages

    ja = "君はふてぶてしくつぶやいた\n" * 8
    assert languages(ja + "永遠\n") == ["ja"]  # a line of kanji alone is still Japanese
    assert languages(ja + "and I could leave it on a shelf\nyou know that I love you\nit is all for you\n") == ["ja", "en"]
    assert languages(ja + "oh baby\n") == ["ja"]  # a borrowed phrase is not a language of the song
    assert languages("Тёплое место, но улицы ждут\n" * 4) == ["ru"]
    assert languages("la la la\n" * 5) == [] and languages("") == []


async def test_tags_languages_genres_and_ones_own(env):
    from app import tags
    from app.db import session_scope
    from app.lyrics.providers import Found
    from app.models import Track

    c, answers = env["client"], env["answers"]
    a = await add_track("nd1")
    b = await add_track("nd2")
    async with session_scope() as s:
        (await s.get(Track, a)).genre = "Asian Music"
    answers["lrclib"] = Found("lrclib", plain="君はふてぶてしくつぶやいた\n" * 6 + "and you know that I love you\n" * 3)
    await c.get("/api/lyrics", params={"nd_song_id": "nd1"})  # the lyrics are found: the languages are told
    async with session_scope() as s:
        await tags.sync(s)
    r = (await c.get("/api/tags")).json()
    by = {g["id"]: {t["name"]: t for t in g["tags"]} for g in r["groups"]}
    # «без слов» is always there to be put on by hand, though no track has it yet
    assert set(by["lang"]) == {"японский", "английский", "без слов"} and by["lang"]["без слов"]["count"] == 0
    assert set(by["genre"]) == {"Asian Music"} and by["own"] == {}
    assert sorted((await by_songs(r["map"]))["nd1"]) == sorted([by["lang"]["японский"]["id"], by["lang"]["английский"]["id"], by["genre"]["Asian Music"]["id"]])
    assert "nd2" not in (await by_songs(r["map"]))

    # a tag of one's own, on several tracks at once; the same name again is the same tag
    chill = (await c.post("/api/tags", json={"name": "  chill "})).json()
    assert chill["group"] == "own" and (await c.post("/api/tags", json={"name": "CHILL"})).json()["id"] == chill["id"]
    r = (await c.post("/api/tags/assign", json={"track_ids": await tids("nd1", "nd2"), "add": [chill["id"]]})).json()
    assert chill["id"] in (await by_songs(r["map"]))["nd1"] and (await by_songs(r["map"]))["nd2"] == [chill["id"]]

    # a tag the Hub set, taken off by hand, stays off; put back by hand, it stays on
    en = by["lang"]["английский"]["id"]
    await c.post("/api/tags/assign", json={"track_ids": await tids("nd1"), "remove": [en, chill["id"]]})
    async with session_scope() as s:
        await tags.sync(s)
    r = (await c.get("/api/tags")).json()
    assert en not in (await by_songs(r["map"]))["nd1"] and chill["id"] not in (await by_songs(r["map"]))["nd1"]
    await c.post("/api/tags/assign", json={"track_ids": await tids("nd1"), "add": [en]})
    async with session_scope() as s:
        await tags.sync(s)
    assert en in (await by_songs((await c.get("/api/tags")).json()["map"]))["nd1"]

    # «no lyrics for this track» is «без слов», and the languages of the old text are gone
    await c.post("/api/lyrics/choose", json={"track_id": b, "source": None})
    await c.post("/api/lyrics/choose", json={"track_id": a, "source": None})
    r = (await c.get("/api/tags")).json()
    names = {t["id"]: t["name"] for g in r["groups"] for t in g["tags"]}
    assert [names[i] for i in (await by_songs(r["map"]))["nd2"] if names[i] != "chill"] == ["без слов"]
    assert "японский" not in [names[i] for i in (await by_songs(r["map"]))["nd1"]] and "английский" in [names[i] for i in (await by_songs(r["map"]))["nd1"]]


async def test_no_words_by_hand_by_a_source_and_what_it_does_to_the_search(env):
    from sqlalchemy import select

    from app import tags
    from app.db import session_scope
    from app.lyrics.providers import Found
    from app.models import Lyrics

    c, calls, answers = env["client"], env["calls"], env["answers"]
    a = await add_track("nd1")
    b = await add_track("nd2")
    async with session_scope() as s:
        await tags.sync(s)

    async def state() -> tuple[dict, dict]:
        r = (await c.get("/api/tags")).json()
        names = {t["id"]: t["name"] for g in r["groups"] for t in g["tags"]}
        return {song: sorted(names[i] for i in ids) for song, ids in (await by_songs(r["map"])).items()}, {v: k for k, v in names.items()}

    _, ids = await state()
    # put on by hand (a track just uploaded, never looked for): the text is not looked for at all
    await c.post("/api/tags/assign", json={"track_ids": await tids("nd1"), "add": [ids["без слов"]]})
    assert (await state())[0] == {"nd1": ["без слов"]}
    r = (await c.get("/api/lyrics", params={"nd_song_id": "nd1"})).json()
    assert (r["kind"], r["pinned"]) == ("none", True) and calls == []

    # taken off: the track is looked for again, as a new one — and the tag stays off though a source says «instrumental»
    answers["lrclib"] = Found("lrclib", instrumental=True)
    await c.post("/api/tags/assign", json={"track_ids": await tids("nd1"), "remove": [ids["без слов"]]})
    async with session_scope() as s:
        assert (await s.execute(select(Lyrics).where(Lyrics.track_id == a))).scalar_one_or_none() is None
    r = (await c.get("/api/lyrics", params={"nd_song_id": "nd1"})).json()
    assert (r["kind"], r["pinned"]) == ("none", False) and "lrclib" in calls
    assert "nd1" not in (await state())[0]
    # …until the listener says «no lyrics» in the lyrics window
    await c.post("/api/lyrics/choose", json={"track_id": a, "source": None})
    assert (await state())[0] == {"nd1": ["без слов"]}

    # a source that calls the track an instrumental sets the tag by itself; a text found elsewhere outweighs it
    await c.get("/api/lyrics", params={"nd_song_id": "nd2"})
    assert (await state())[0]["nd2"] == ["без слов"]
    answers["ovh"] = Found("ovh", plain="and you know that I love you\n" * 6)
    await c.post("/api/lyrics/reset", json={"track_id": b})
    assert (await state())[0]["nd2"] == ["английский"]


async def test_own_tags_are_renamed_merged_deleted_and_selections_follow(env):
    c = env["client"]
    await add_track("nd1")
    await add_track("nd2")
    chill = (await c.post("/api/tags", json={"name": "chill"})).json()["id"]
    calm = (await c.post("/api/tags", json={"name": "calm"})).json()["id"]
    road = (await c.post("/api/tags", json={"name": "для дороги"})).json()["id"]
    await c.post("/api/tags/assign", json={"track_ids": await tids("nd1"), "add": [chill, road]})
    await c.post("/api/tags/assign", json={"track_ids": await tids("nd2"), "add": [calm]})

    sel = (await c.post("/api/tags/selections", json={"name": " Тихое ", "scope": "liked", "filter": {str(calm): "yes", str(road): "no", "bogus": "yes"}})).json()
    assert (sel["name"], sel["filter"]) == ("Тихое", {str(calm): "yes", str(road): "no"})
    assert (await c.post("/api/tags/selections", json={"name": "x", "scope": "liked", "filter": {}})).status_code == 400
    assert (await c.post("/api/tags/selections", json={"name": "x", "scope": "nowhere", "filter": {"1": "yes"}})).status_code == 422

    assert (await c.put(f"/api/tags/{calm}", json={"name": "CHILL"})).status_code == 409  # that name is taken: merge them
    r = (await c.put(f"/api/tags/{road}", json={"name": "в дорогу"})).json()
    assert "в дорогу" in [t["name"] for g in r["groups"] for t in g["tags"]]

    r = (await c.post(f"/api/tags/{calm}/merge", json={"into": chill})).json()
    assert sorted((await by_songs(r["map"]))["nd1"]) == sorted([chill, road]) and (await by_songs(r["map"]))["nd2"] == [chill]
    assert calm not in [t["id"] for g in r["groups"] for t in g["tags"]]
    assert (await c.get("/api/tags/selections/all")).json()[0]["filter"] == {str(chill): "yes", str(road): "no"}

    r = (await c.delete(f"/api/tags/{road}")).json()
    assert (await by_songs(r["map"]))["nd1"] == [chill] and (await c.get("/api/tags/selections/all")).json()[0]["filter"] == {str(chill): "yes"}
    assert (await c.delete(f"/api/tags/{road}")).status_code == 404

    r = (await c.put(f"/api/tags/selections/{sel['id']}", json={"name": "Спокойное", "scope": "library", "filter": {"untagged": "no"}})).json()
    assert (r["name"], r["scope"], r["filter"]) == ("Спокойное", "library", {"untagged": "no"})
    assert (await c.delete(f"/api/tags/selections/{sel['id']}")).json() == {"ok": True}
    assert (await c.get("/api/tags/selections/all")).json() == []


async def test_the_kind_of_a_text_matters_more_than_its_source(env):
    """A text timed by the word, from a later source, beats one timed by the line from an earlier one; of two of
    a kind the earlier source wins."""
    from app.lyrics.providers import Found

    c, calls, answers = env["client"], env["calls"], env["answers"]
    await add_track()
    answers.update(lrclib=Found("lrclib", lines=[{"t": 0, "text": "по строкам"}]),
                   lrcred=Found("lrcred", lines=[{"t": 0, "text": "тоже по строкам"}]),
                   lyricsplus=Found("lyricsplus", lines=[{"t": 0, "text": "по словам", "words": [{"t": 0, "text": "по "}, {"t": 300, "text": "словам"}]}]),
                   netease=Found("netease", lines=[{"t": 0, "text": "не спросят"}]))
    r = (await c.get("/api/lyrics", params={"nd_song_id": "nd1"})).json()
    assert (r["kind"], r["source"], r["source_title"]) == ("word", "lyricsplus", "LyricsPlus")
    assert calls == ["file", "lrclib", "lrcred", "lyricsplus"]  # words in hand: nobody else is asked

    answers.pop("lyricsplus")
    answers.pop("netease")
    r = (await c.post("/api/lyrics/reset", json={"track_id": r["track_id"]})).json()
    assert (r["kind"], r["source"]) == ("synced", "lrclib")  # of two texts timed by the line, the earlier source's


def test_lyricsplus_lines():
    from app.lyrics import parse

    lines = parse.parse_lyricsplus([
        {"time": 2000, "duration": 1500, "text": "second line", "syllabus": [{"time": 2000, "duration": 700, "text": "second "},
                                                                         {"time": 2700, "duration": 800, "text": "line"}]},
        {"time": 0, "duration": 900, "text": "first", "syllabus": [{"time": 0, "duration": 900, "text": "first"}]},
        {"time": 5000, "duration": 10, "text": "  "}, {"text": "no time"}, "junk"])
    assert lines == [{"t": 0, "end": 900, "text": "first"},
                     {"t": 2000, "end": 3500, "text": "second line", "words": [{"t": 2000, "text": "second "}, {"t": 2700, "text": "line"}]}]
    assert parse.kind_of(lines, None) == "word"


async def test_sources_that_know_a_recording_by_its_isrc():
    from app.lyrics import providers
    from app.lyrics.providers import Query

    q = Query(artist="Queen", title="Bohemian Rhapsody", duration_s=355, isrc="gbum71029604")
    with respx.mock(assert_all_called=False) as mock:
        mock.get("https://lrc.red/s/GBUM71029604.json").mock(return_value=httpx.Response(200, json={
            "duration": 356.5, "files": {"lyricsfile": "https://lrc.red/s/GBUM71029604.lyricsfile.yaml"}}))
        mock.get("https://lrc.red/s/GBUM71029604.lyricsfile.yaml").mock(return_value=httpx.Response(200, text=(
            "lines:\n  - text: Is this the real life?\n    start_ms: 0\n    end_ms: 2906\n    words:\n"
            "      - {text: 'Is ', start_ms: 0, end_ms: 458}\n      - {text: 'this', start_ms: 458, end_ms: 806}\n")))
        found = await providers.from_lrcred(q)
        assert found is not None and found.kind == "word" and found.lines[0]["words"][1] == {"t": 458, "text": "this"}
        # no ISRC: nothing to ask; a record of another length is not this recording
        assert await providers.from_lrcred(Query(artist="Queen", title="Bohemian Rhapsody", duration_s=355)) is None
        assert await providers.from_lrcred(Query(artist="Queen", title="x", duration_s=200, isrc="GBUM71029604")) is None

        route = mock.get(providers.LYRICSPLUS_URL).mock(return_value=httpx.Response(200, json={
            "metadata": {"totalDuration": 356520},
            "lyrics": [{"time": 0, "duration": 2906, "text": "Is this the real life?",
                        "syllabus": [{"time": 0, "duration": 458, "text": "Is "}, {"time": 458, "duration": 348, "text": "this "}]}]}))
        found = await providers.from_lyricsplus(q)
        assert found is not None and (found.source, found.kind) == ("lyricsplus", "word")
        assert dict(route.calls.last.request.url.params) == {"isrc": "GBUM71029604"}
        # by the names it asks with the length, and with none it does not guess
        await providers.from_lyricsplus(Query(artist="Queen", title="Bohemian Rhapsody", duration_s=355))
        assert dict(route.calls.last.request.url.params) == {"title": "Bohemian Rhapsody", "artist": "Queen", "duration": "355"}
        assert await providers.from_lyricsplus(Query(artist="Queen", title="Bohemian Rhapsody")) is None
        mock.get(providers.LYRICSPLUS_URL).mock(return_value=httpx.Response(404, json={"error": {}}))
        assert await providers.from_lyricsplus(q) is None


def test_how_a_text_is_read():
    from app.lyrics import reading

    ja = reading.of(["名前を呼ぶよ", "Hello 世界", ""], "ja")
    assert ja["lines"] == ["namae wo yobu yo", "Hello sekai", ""]
    assert ja["ruby"][0] == [["名前", "なまえ"], ["を", None], ["呼ぶ", "よぶ"], ["よ", None]] and ja["ruby"][2] == []
    assert reading.of(["月亮代表我的心 forever"], "zh")["lines"] == ["yuè liàng dài biǎo wǒ de xīn forever"]
    # Korean: a consonant is carried over to a following vowel; two ㄹ in a row are «ll»
    assert reading.of(["사랑해요", "한국어를 읽어요", "빨리 달려"], "ko")["lines"] == ["saranghaeyo", "hangugeoreul ilgeoyo", "ppalli dallyeo"]
    assert reading.of(["plain english"], "en") is None and reading.of(["", " "], "ja") is None


async def test_a_text_comes_with_its_reading(env):
    from app.lyrics.providers import Found

    c, answers = env["client"], env["answers"]
    await add_track()
    answers["lrclib"] = Found("lrclib", lines=[{"t": 0, "text": "名前を呼ぶよ"}, {"t": 4000, "text": "世界"}])
    r = (await c.get("/api/lyrics", params={"nd_song_id": "nd1"})).json()
    assert r["reading"]["lang"] == "ja" and r["reading"]["lines"] == ["namae wo yobu yo", "sekai"]
    assert r["reading"]["ruby"][1] == [["世界", "せかい"]]
    answers["lrclib"] = Found("lrclib", lines=[{"t": 0, "text": "just english"}])
    r = (await c.post("/api/lyrics/reset", json={"track_id": r["track_id"]})).json()
    assert r["reading"] is None


async def test_a_model_that_miscounts_is_asked_for_the_lines_in_halves(monkeypatch):
    """One stubborn line does not cost the whole song its translation."""
    import json
    from types import SimpleNamespace

    from app.ai import llm as llm_mod
    from app.lyrics import translate

    asked: list[int] = []

    class Completions:
        async def create(self, **kw):
            text = kw["messages"][1]["content"]
            lines = [x.split(". ", 1)[1] for x in text.split("lines:\n", 1)[1].splitlines()]
            asked.append(len(lines))
            out = [f"<{x}>" for x in lines]
            if len(lines) > 2 and "bad" in lines:
                out = out[:-1]  # a line is lost whenever «bad» is among more than two
            return SimpleNamespace(choices=[SimpleNamespace(message=SimpleNamespace(content=json.dumps({"lines": out})))])

    class Client:
        base_url, model = "https://openrouter.ai/api/v1", "m"
        _client = SimpleNamespace(chat=SimpleNamespace(completions=Completions()))

        async def __aenter__(self):
            return self

        async def __aexit__(self, *a):
            return None

    monkeypatch.setattr(llm_mod, "LLMClient", Client)
    monkeypatch.setattr(llm_mod, "configured", lambda: True)
    got = await translate.llm(["a", "b", "bad", "d", "e", "f", "g", "h"], "ru")
    assert got == ["<a>", "<b>", "<bad>", "<d>", "<e>", "<f>", "<g>", "<h>"]
    assert asked[:2] == [8, 8] and 2 in asked  # asked twice whole, then in halves down to where it counts right
