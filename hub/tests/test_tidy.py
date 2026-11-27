"""«Наведение порядка»: what the rules and the AI find, accepting it, taking it back, and not asking twice."""

from __future__ import annotations

import pytest

from tests.support import filed_track
from tests.test_flow import drain_jobs
from tests.test_flow import env as env  # the fixture
from tests.test_flow import src_mp3 as src_mp3  # its file

# ---------------------------------------------------------------- the rules alone


@pytest.mark.parametrize(("title", "artist", "no", "clean"), [
    ("Kagerou Days (Official Video)", "Jin", None, "Kagerou Days"),
    ("neko [Lyrics]", "nekotsuki", None, "neko"),
    ("Song | Official Music Video", "X", None, "Song"),
    ("Песня (Премьера клипа 2023)", "X", None, "Песня"),
    ("Jin - Kagerou Days", "Jin", None, "Kagerou Days"),
    ("LSP - Деньги", "ЛСП", None, "Деньги"),  # the artist spelt otherwise, read the same
    ("03. Name", "X", 3, "Name"),
    ("Song (Live Video)", "X", None, None),  # «Live» is the name's
    ("Song - Remix", "X", None, None),
    ("Video Games", "Lana Del Rey", None, None),
    ("7 rings", "Ariana Grande", 7, None),
    ("Song (New)", "X", None, None),
    ("Eight -THE BEST OF 八王子P-", "Hachioji P", None, None),  # its dashes are the name's
    ("CORE-KrnT edit-", "otetsu", None, None),
])
def test_a_name_loses_what_is_not_the_name(title, artist, no, clean):
    from app.tidy import rules

    got = rules.clean_title(title, artist, no)
    assert (got.title if got else None) == clean


def test_a_name_tells_its_version_as_written():
    from app.tidy import rules

    assert rules.version_of("Trampoline (Sped Up)") == rules.Version("Trampoline", "Sped Up")
    assert rules.version_of("Song (slowed + reverb)").label == "Slowed + Reverb"
    assert rules.version_of("Песня (замедленная версия)").label == "Замедленная версия"
    assert rules.version_of("Song (Deluxe)") is None and rules.version_of("Song") is None
    assert rules.song_key("Song (feat. X) [Live]") == rules.song_key("song") == rules.song_key("Song (Deluxe)")


def test_the_lines_of_a_text_that_are_not_the_song():
    from app.tidy import rules

    junk = rules.lyric_junk(["Подписывайтесь: t.me/abc", "Я пою", "Подпишись на канал", "12 Contributors", "подпиши мне"])
    assert [(j.index, j.sure) for j in junk] == [(0, True), (2, False), (3, True)]


# ---------------------------------------------------------------- the library


async def _add(**fields) -> int:
    from app.db import session_scope
    from app.library import artists

    async with session_scope() as s:
        t = filed_track(**{"fingerprint": "x", **fields})
        s.add(t)
        await s.flush()
        await artists.credit_track(s, t)
        return t.id


async def _scan():
    from app.tidy import scan

    return await scan.run()


async def _open(c, section: str) -> list[dict]:
    r = await c.get("/api/tidy/proposals", params={"section": section})
    assert r.status_code == 200, r.text
    return r.json()


async def test_a_name_is_put_right_written_down_and_taken_back(env):
    from app.db import session_scope
    from app.models import Edit, Track

    c = env["client"]
    tid = await _add(artist="Jin", title="Kagerou Days (Official Video)")
    await _add(artist="Jin", title="Kagerou Days", renamed_at=None)
    status = await _scan()
    assert status["ai"] == "off"
    ov = (await c.get("/api/tidy")).json()
    assert {x["id"]: x["count"] for x in ov["sections"]}["names"] == 1 and ov["checked_at"]

    (p,) = await _open(c, "names")
    assert (p["was"], p["to"], p["by"], p["sure"], p["subject_id"]) == ("Kagerou Days (Official Video)", "Kagerou Days", "rule", True, tid)
    r = await c.post(f"/api/tidy/proposals/{p['id']}/accept")
    assert r.status_code == 200 and r.json()["state"] == "accepted", r.text
    async with session_scope() as s:
        t = await s.get(Track, tid)
        assert t.title == "Kagerou Days" and t.renamed_at is not None
        assert (await s.execute(Edit.__table__.select().where(Edit.target_id == tid))).first() is not None
    (j,) = (await c.get("/api/tidy/journal")).json()
    assert j["id"] == p["id"] and j["decided_by"] == "admin"

    r = await c.post(f"/api/tidy/proposals/{p['id']}/revert")
    assert r.status_code == 200 and r.json()["state"] == "reverted", r.text
    async with session_scope() as s:
        assert (await s.get(Track, tid)).title == "Kagerou Days (Official Video)"
    # taken back is answered: not offered again; a name put by hand is not offered at all
    await _scan()
    assert await _open(c, "names") == []


async def test_a_proposal_of_a_name_changed_since_is_gone(env):
    from app.db import session_scope
    from app.models import Track

    c = env["client"]
    tid = await _add(artist="X", title="Song (HD)")
    await _scan()
    (p,) = await _open(c, "names")
    async with session_scope() as s:
        (await s.get(Track, tid)).title = "Другое"
    r = await c.post(f"/api/tidy/proposals/{p['id']}/accept")
    assert r.status_code == 409
    assert await _open(c, "names") == []


async def test_rejecting_and_taking_the_rejection_back(env):
    c = env["client"]
    await _add(artist="X", title="Song (HD)")
    await _scan()
    (p,) = await _open(c, "names")
    assert (await c.post(f"/api/tidy/proposals/{p['id']}/reject")).json()["state"] == "rejected"
    await _scan()
    assert await _open(c, "names") == []  # not asked again
    assert (await c.post(f"/api/tidy/proposals/{p['id']}/reopen")).json()["state"] == "open"
    assert [x["id"] for x in await _open(c, "names")] == [p["id"]]


async def test_a_decision_is_put_back_for_review(env):
    from app.db import session_scope
    from app.models import Track

    c = env["client"]
    tid = await _add(artist="X", title="Song (HD)")
    await _add(artist="X", title="Other (Lyrics)")
    await _scan()
    a, b = await _open(c, "names")
    assert (await c.post(f"/api/tidy/proposals/{a['id']}/accept")).status_code == 200
    assert (await c.post(f"/api/tidy/proposals/{b['id']}/reject")).status_code == 200
    # the journal has both: what was accepted and what was rejected
    assert {x["id"]: x["state"] for x in (await c.get("/api/tidy/journal")).json()} == {a["id"]: "accepted", b["id"]: "rejected"}

    # accepted: undone, and a question again
    r = await c.post(f"/api/tidy/proposals/{a['id']}/review")
    assert r.status_code == 200 and r.json()["state"] == "open", r.text
    async with session_scope() as s:
        assert (await s.get(Track, a["subject_id"])).title == a["was"]
    # rejected: a question again
    assert (await c.post(f"/api/tidy/proposals/{b['id']}/review")).json()["state"] == "open"
    assert {x["id"] for x in await _open(c, "names")} == {a["id"], b["id"]}
    assert (await c.get("/api/tidy/journal")).json() == []
    assert (await c.post(f"/api/tidy/proposals/{a['id']}/review")).status_code == 409  # open already
    assert tid in (a["subject_id"], b["subject_id"])


async def test_artists_that_read_the_same_are_joined_and_parted_again(env):
    from sqlalchemy import select

    from app.db import session_scope
    from app.library import artists
    from app.models import Artist, Track, TrackArtist

    c = env["client"]
    async with session_scope() as s:  # two records of one artist, as two catalogs would have them
        lsp = Artist(name="LSP")
        ru = Artist(name="ЛСП")
        s.add_all([lsp, ru])
        await s.flush()
        ids = []
        for who, title in ((lsp, "Coca-Colla"), (ru, "Деньги"), (ru, "Монетка")):
            t = filed_track(artist=who.name, title=title, fingerprint="x")
            s.add(t)
            await s.flush()
            await artists.credit_track(s, t, [who])
            ids.append(t.id)
        lsp_id, ru_id = lsp.id, ru.id
    await _scan()
    (p,) = await _open(c, "artists")
    assert (p["subject_id"], p["was"], p["to"], p["sure"]) == (lsp_id, "LSP", "ЛСП", True)
    # the pages mark it on both artists: who joins and who is joined
    (m,) = (await c.get("/api/tidy/marks")).json()
    assert (m["id"], m["show"]["artist_id"], m["show"]["into_id"]) == (p["id"], lsp_id, ru_id)
    assert (await c.post(f"/api/tidy/proposals/{p['id']}/accept")).status_code == 200
    await drain_jobs()
    async with session_scope() as s:
        assert (await s.get(Artist, lsp_id)).merged_into_id == ru_id
        assert (await s.get(Track, ids[0])).artist == "ЛСП"
    r = await c.post(f"/api/tidy/proposals/{p['id']}/revert")
    assert r.status_code == 200, r.text
    await drain_jobs()
    async with session_scope() as s:
        assert (await s.get(Artist, lsp_id)).merged_into_id is None
        assert (await s.get(Track, ids[0])).artist == "LSP"
        assert (await s.execute(select(TrackArtist.artist_id).where(TrackArtist.track_id == ids[0]))).scalar_one() == lsp_id


async def test_a_version_is_joined_to_its_song_and_parted_again(env):
    from app.db import session_scope
    from app.library import versions
    from app.models import Track

    c = env["client"]
    plain = await _add(artist="SHAED", title="Trampoline")
    fast = await _add(artist="SHAED", title="Trampoline (Sped Up)")
    await _add(artist="Other", title="Trampoline (Live)")  # another artist's: not this song
    await _scan()
    (p,) = await _open(c, "versions")
    assert (p["subject_id"], p["to"]) == (fast, "Версия «Sped Up» трека «Trampoline»")
    assert (await c.post(f"/api/tidy/proposals/{p['id']}/accept")).status_code == 200
    async with session_scope() as s:
        card = await versions.card(s, await s.get(Track, plain))
        assert [(v["track_id"], v["label"]) for v in card["versions"]] == [(plain, versions._guess("Trampoline")), (fast, "Sped Up")]
    assert (await c.post(f"/api/tidy/proposals/{p['id']}/revert")).status_code == 200
    async with session_scope() as s:
        assert (await versions.card(s, await s.get(Track, plain)))["song_id"] is None


async def test_the_worse_of_two_copies_goes_to_the_trash_and_comes_back(env, monkeypatch):
    from sqlalchemy import select

    from app import playlists
    from app.db import session_scope
    from app.library import acoustic
    from app.library.importer import library_abs
    from app.models import PlaylistItem, Track, User

    c = env["client"]
    good = await _add(artist="X", title="Song", path="x/good.flac", codec="flac", bitrate_kbps=900)
    bad = await _add(artist="X", title="Song", path="x/bad.mp3", codec="mp3", bitrate_kbps=128)
    for p in ("x/good.flac", "x/bad.mp3"):
        library_abs(p).parent.mkdir(parents=True, exist_ok=True)
        library_abs(p).write_bytes(b"sound")

    async def doubles(s=None, *, budget=None):
        return [[good, bad]]

    monkeypatch.setattr(acoustic, "doubles", doubles)
    async with session_scope() as s:
        admin = (await s.execute(select(User))).scalars().first()
        mine = await playlists.create(s, admin.id, "Моё")
        await playlists.add(s, mine, [bad])
        mine_id = mine.id
    await _scan()
    (p,) = await _open(c, "versions")
    assert (p["kind"], p["subject_id"], p["sure"], p["was"], p["to"]) == ("double", bad, True, "MP3 · 128 kbps · без альбома", "FLAC · 900 kbps · без альбома")
    assert (await c.post(f"/api/tidy/proposals/{p['id']}/accept")).status_code == 200
    async with session_scope() as s:
        assert await s.get(Track, bad) is None
        assert list((await s.execute(select(PlaylistItem.track_id).where(PlaylistItem.playlist_id == mine_id))).scalars()) == [good]
    r = await c.post(f"/api/tidy/proposals/{p['id']}/revert")
    assert r.status_code == 200, r.text
    async with session_scope() as s:
        back = (await s.execute(select(Track).where(Track.title == "Song", Track.id != good))).scalar_one()
        assert list((await s.execute(select(PlaylistItem.track_id).where(PlaylistItem.playlist_id == mine_id))).scalars()) == [back.id]


async def test_the_lines_of_a_text_that_are_not_the_song_are_dropped_and_come_back(env):
    from app.db import session_scope
    from app.models import Lyrics

    c = env["client"]
    tid = await _add(artist="Hayd", title="Superhero")
    text = "Подписывайтесь: t.me/abc\nI am a hero\nhttps://vk.com/x"
    async with session_scope() as s:
        s.add(Lyrics(track_id=tid, kind="plain", source="lrclib", plain=text, search_text=text.casefold()))
    await _scan()
    (p,) = await _open(c, "lyrics")
    assert (p["to"], p["show"]["lines"]) == ("Убрать 2 строки", ["Подписывайтесь: t.me/abc", "https://vk.com/x"])
    assert p["show"]["drop_at"] == [0, 2]  # the places the text's mark finds them by
    assert [m["id"] for m in (await c.get("/api/tidy/marks")).json()] == [p["id"]]
    assert (await c.post("/api/tidy/accept-sure", json={"section": "lyrics"})).json() == {"accepted": 1, "failed": 0}
    async with session_scope() as s:
        row = await s.get(Lyrics, tid)
        assert (row.plain, row.pinned) == ("I am a hero", True)
    assert (await c.post(f"/api/tidy/proposals/{p['id']}/revert")).status_code == 200
    async with session_scope() as s:
        row = await s.get(Lyrics, tid)
        assert (row.plain, row.pinned) == (text, False)


async def test_the_ai_names_a_russian_artist_in_cyrillic_once_and_is_paid_out_of_the_month(env, monkeypatch):
    from sqlalchemy import select

    from app.ai.llm import LLMClient, LLMUsage
    from app.db import session_scope
    from app.models import AiUsage, Artist, ArtistName
    from app.tidy import ai

    c = env["client"]
    await _add(artist="Mikhail Krug", title="Vladimirskiy central")
    asked: list[dict] = []

    async def call_tool(self, *, system, user_content, tool_name, tool_description, parameters, max_tokens):
        import json

        items = json.loads(user_content.split("Items:\n", 1)[1])
        asked.extend(items)
        out = []
        for q in items:
            if q.get("type") == "artist" and q["name"] == "Mikhail Krug":
                out.append({"id": q["id"], "name": "Михаил Круг", "sure": True, "why": "Транслит"})
            if q.get("type") == "track":
                out.append({"id": q["id"], "name": "Владимирский централ", "sure": True})
        return {"items": out}, LLMUsage(tokens_in=100, tokens_out=10, cost_usd=0.01)

    monkeypatch.setattr(ai, "available", lambda: True)
    monkeypatch.setattr(LLMClient, "call_tool", call_tool)
    status = await _scan()
    assert status["ai"] == "ok"
    props = await _open(c, "names")
    assert sorted((p["subject_type"], p["was"], p["to"], p["by"]) for p in props) == [
        ("artist", "Mikhail Krug", "Михаил Круг", "ai"), ("track", "Vladimirskiy central", "Владимирский централ", "ai")]
    async with session_scope() as s:
        spent = list((await s.execute(select(AiUsage))).scalars())
        assert [(u.user_id, u.kind) for u in spent] == [(None, "tidy"), (None, "tidy")]
    # the same questions are not paid for again
    n = len(asked)
    await _scan()
    assert len(asked) == n

    artist = next(p for p in props if p["subject_type"] == "artist")
    assert (await c.post(f"/api/tidy/proposals/{artist['id']}/accept")).status_code == 200
    await drain_jobs()
    async with session_scope() as s:
        a = await s.get(Artist, artist["subject_id"])
        names = set((await s.execute(select(ArtistName.name).where(ArtistName.artist_id == a.id))).scalars())
        assert a.name == "Михаил Круг" and {"Mikhail Krug", "Михаил Круг"} <= names  # the Latin is still found
    assert (await c.post(f"/api/tidy/proposals/{artist['id']}/revert")).status_code == 200
    async with session_scope() as s:
        a = await s.get(Artist, artist["subject_id"])
        names = set((await s.execute(select(ArtistName.name).where(ArtistName.artist_id == a.id))).scalars())
        assert a.name == "Mikhail Krug" and "Михаил Круг" not in names


async def test_a_name_put_right_here_may_be_put_right_further(env, monkeypatch):
    from app.ai.llm import LLMClient, LLMUsage
    from app.tidy import ai

    c = env["client"]
    await _add(artist="Михаил Круг", title="Vladimirskiy central (Official Video)")
    await _scan()
    (p,) = await _open(c, "names")
    assert (await c.post(f"/api/tidy/proposals/{p['id']}/accept")).status_code == 200

    async def call_tool(self, *, user_content, **kw):
        import json

        items = json.loads(user_content.split("Items:\n", 1)[1])
        return {"items": [{"id": q["id"], "name": "Владимирский централ", "sure": True}
                          for q in items if q["name"] == "Vladimirskiy central"]}, LLMUsage()

    monkeypatch.setattr(ai, "available", lambda: True)
    monkeypatch.setattr(LLMClient, "call_tool", call_tool)
    await _scan()
    (p,) = await _open(c, "names")
    assert (p["was"], p["to"]) == ("Vladimirskiy central", "Владимирский централ")


async def test_no_ai_past_the_months_allowance(env, monkeypatch):
    from app.ai.llm import LLMClient
    from app.db import session_scope
    from app.models import AiUsage
    from app.tidy import ai

    await _add(artist="Mikhail Krug", title="Vladimirskiy central")
    async with session_scope() as s:
        s.add(AiUsage(user_id=None, kind="tidy", cost_usd=5.0))

    async def call_tool(self, **kw):
        raise AssertionError("not to be asked")

    monkeypatch.setattr(ai, "available", lambda: True)
    monkeypatch.setattr(LLMClient, "call_tool", call_tool)
    assert (await _scan())["ai"] == "limit"
    ov = (await env["client"].get("/api/tidy")).json()
    assert ov["ai"]["spent_usd"] == 5.0


async def test_only_an_admin_starts_a_look_through(env):
    from app.db import session_scope
    from app.models import Job

    c = env["client"]
    r = await c.post("/api/tidy/scan")
    assert r.status_code == 200 and r.json()["running"] is True
    await c.post("/api/tidy/scan")  # one at a time
    async with session_scope() as s:
        from sqlalchemy import func, select

        assert await s.scalar(select(func.count(Job.id)).where(Job.kind == "tidy_scan")) == 1
    await drain_jobs()
    assert (await c.get("/api/tidy")).json()["running"] is False


# ---------------------------------------------------------------- censorship in lyrics


def test_a_censored_word_and_what_may_hide_behind_it():
    from app.tidy import rules

    assert rules.masks("Я б***ь не знаю, «х*й»!") == ["б***ь", "х*й"]
    assert rules.masks("Всё [цензура] надоело") == ["[цензура]"]
    assert rules.masks("строка со * звездой") == []
    # a star not hiding anything: a separator, a name, an action
    assert rules.masks("toushindai no ura•omote") == rules.masks("くって*ねて*あそべ！") == []
    assert rules.masks("ネバーランド - DECO*27") == rules.masks("*sigh* okay") == []
    assert rules.masks("I'm ****ing 'em both, (beep)") == ["****ing", "(beep)"]
    assert (rules.fits("б***ь", "блять"), rules.fits("б**", "блять"), rules.fits("б***ь", "блин"), rules.fits("х*й", "хуй")) == (
        "strict", "loose", None, "strict")
    assert rules.fits("б***ь", "б***ь") is None
    got = rules.unmask("Я б***ь не знаю, х*й", ["Совсем другое", "Я блять не знаю, хуй"])
    assert got == rules.Unmasked(("блять", "хуй"), True)
    assert rules.unmask("Я б***ь не знаю", ["Мы блять не знаем"]) is None  # another line, not this one whole
    assert rules.put_words("«Б***ь», — сказал", ["блять"]) == "«Блять», — сказал"


async def _censored(tid: int, timed: bool) -> str:
    from app.db import session_scope
    from app.models import Lyrics

    lines = ["Я б***ь не знаю", "что делать"]
    async with session_scope() as s:
        if timed:
            s.add(Lyrics(track_id=tid, kind="word", source="lrclib", lines_json=[
                {"t": 0, "text": lines[0], "words": [{"t": 0, "text": "Я "}, {"t": 300, "text": "б***ь "},
                                                     {"t": 600, "text": "не "}, {"t": 800, "text": "знаю"}]},
                {"t": 2000, "text": lines[1]}], search_text="\n".join(lines).casefold()))
        else:
            s.add(Lyrics(track_id=tid, kind="plain", source="lrclib", plain="\n".join(lines),
                         search_text="\n".join(lines).casefold()))
    return "\n".join(lines)


async def test_a_censored_word_comes_back_from_another_source_with_its_timing(env, monkeypatch):
    from app.db import session_scope
    from app.lyrics import service as lyrics_
    from app.lyrics.providers import Found
    from app.models import Lyrics

    c = env["client"]
    tid = await _add(artist="Кто-то", title="Песня")
    await _censored(tid, timed=True)
    asked = []

    async def candidates(s, track):
        asked.append(track.id)
        return [Found(source="lrclib", plain="Я б***ь не знаю\nчто делать"),
                Found(source="netease", plain="Я блять не знаю\nчто делать")]

    monkeypatch.setattr(lyrics_, "candidates", candidates)
    await _scan()
    (p,) = await _open(c, "lyrics")
    assert (p["kind"], p["by"], p["sure"], p["to"]) == ("uncensor", "rule", True, "Вернуть 1 слово без цензуры")
    assert p["show"]["pairs"] == [{"was": "Я б***ь не знаю", "to": "Я блять не знаю"}] and "NetEase" in p["why"]
    assert p["show"]["fix_at"] == [0]  # the place of the line in the text, as the pairs go
    await _scan()
    assert asked == [tid]  # a text is looked up once
    assert (await c.post(f"/api/tidy/proposals/{p['id']}/accept")).status_code == 200
    async with session_scope() as s:
        row = await s.get(Lyrics, tid)
        assert row.lines_json[0]["text"] == "Я блять не знаю" and row.lines_json[0]["words"][1] == {"t": 300, "text": "блять "}
        assert row.lines_json[1] == {"t": 2000, "text": "что делать"} and "блять" in row.search_text
    assert (await c.post(f"/api/tidy/proposals/{p['id']}/revert")).status_code == 200
    async with session_scope() as s:
        assert (await s.get(Lyrics, tid)).lines_json[0]["words"][1]["text"] == "б***ь "


async def test_what_no_source_has_the_ai_reads_off_the_mask_and_is_never_sure(env, monkeypatch):
    import json

    from app.ai.llm import LLMClient, LLMUsage
    from app.lyrics import service as lyrics_
    from app.tidy import ai

    c = env["client"]
    tid = await _add(artist="Кто-то", title="Песня")
    await _censored(tid, timed=False)

    async def candidates(s, track):
        return []

    async def call_tool(self, *, user_content, **kw):
        items = json.loads(user_content.split("Items:\n", 1)[1])
        assert items[0]["masks"] == ["б***ь"] and items[0]["context"][1] == "что делать"
        return {"items": [{"id": items[0]["id"], "words": ["блять"], "sure": True}]}, LLMUsage()

    monkeypatch.setattr(lyrics_, "candidates", candidates)
    monkeypatch.setattr(ai, "available", lambda: True)
    monkeypatch.setattr(LLMClient, "call_tool", call_tool)
    await _scan()
    (p,) = await _open(c, "lyrics")
    assert (p["kind"], p["by"], p["sure"], p["why"]) == ("uncensor", "ai", False, "Восстановил ИИ по маске")
    assert (await c.post("/api/tidy/accept-sure", json={"section": "lyrics"})).json()["accepted"] == 0


async def test_an_ai_word_that_does_not_fit_the_mask_is_not_offered(env, monkeypatch):
    import json

    from app.ai.llm import LLMClient, LLMUsage
    from app.lyrics import service as lyrics_
    from app.tidy import ai

    c = env["client"]
    tid = await _add(artist="Кто-то", title="Песня")
    await _censored(tid, timed=False)

    async def candidates(s, track):
        return []

    async def call_tool(self, *, user_content, **kw):
        items = json.loads(user_content.split("Items:\n", 1)[1])
        return {"items": [{"id": items[0]["id"], "words": ["блин"], "sure": True}]}, LLMUsage()

    monkeypatch.setattr(lyrics_, "candidates", candidates)
    monkeypatch.setattr(ai, "available", lambda: True)
    monkeypatch.setattr(LLMClient, "call_tool", call_tool)
    await _scan()
    assert await _open(c, "lyrics") == []



async def test_a_proposal_of_something_gone_since_is_no_question(env):
    """A double whose copy was deleted between two look-throughs (from «Файлы»), or whose copy that stays was, is
    not shown nor counted: there is nothing to draw it with, and nothing to decide."""
    from app.db import session_scope
    from app.models import Track
    from app.tidy import kinds

    c = env["client"]
    ids = [await _add(artist="Кино", title=f"Группа крови {i}") for i in range(4)]
    async with session_scope() as s:
        for copy, keep in ((ids[0], ids[1]), (ids[2], ids[3])):
            await kinds.offer(s, kind="double", subject_type="track", subject_id=copy, was={"both": True},
                              to={"keep": keep}, show={"title": "Группа крови"}, by="rule", sure=True,
                              why="Звучат одинаково")
    section = kinds.KINDS["double"].section
    assert len(await _open(c, section)) == 2
    async with session_scope() as s:
        await s.delete(await s.get(Track, ids[0]))  # the copy that would go: gone already
        await s.delete(await s.get(Track, ids[3]))  # the one that would stay: gone
    assert await _open(c, section) == []
    assert (await c.get("/api/tidy")).json()["total"] == 0



async def _three_pairs():
    from app.db import session_scope
    from app.models import Artist
    from app.tidy import kinds

    async with session_scope() as s:
        made = [Artist(name=f"курящих нет {i}") for i in range(6)]
        s.add_all(made)
        await s.flush()
        ids = [a.id for a in made]
        for other, into in ((ids[0], ids[1]), (ids[2], ids[3]), (ids[4], ids[5])):
            await kinds.offer(s, kind="artist", subject_type="artist", subject_id=other, was={"apart": True},
                              to={"into": into}, show={"line": "курящих нет"}, by="rule", sure=True, why="Пишутся одинаково")
    return ids


async def test_the_window_of_an_artist_answers_the_question_of_tidy(env):
    """«Привязать», «Это его проект», «Это другой» in the artist's window and in «Наведение порядка» are one answer
    to one question: given in the window, the proposal is answered — in the journal, to be taken back there."""
    from app.db import session_scope
    from app.models import Artist, ArtistApart, ArtistLink
    from app.tidy import kinds

    c = env["client"]
    ids = await _three_pairs()
    section = kinds.KINDS["artist"].section
    assert len(await _open(c, section)) == 3
    # joined into the artist whose window it is — the other way round from how the proposal asked
    assert (await c.post(f"/api/artists/{ids[0]}/merge", json={"other_id": ids[1]})).status_code == 200
    assert (await c.post(f"/api/artists/{ids[3]}/projects", json={"other_id": ids[2]})).status_code == 200
    assert (await c.post(f"/api/artists/{ids[5]}/apart", json={"other_id": ids[4]})).status_code == 200
    assert await _open(c, section) == []
    journal = {x["subject_id"]: x["state"] for x in (await c.get("/api/tidy/journal")).json()}
    assert journal == {ids[1]: "accepted", ids[2]: "accepted", ids[4]: "rejected"}
    async with session_scope() as s:
        assert (await s.get(Artist, ids[1])).merged_into_id == ids[0]
        assert await s.get(ArtistLink, tuple(sorted((ids[2], ids[3])))) is not None
        assert await s.get(ArtistApart, tuple(sorted((ids[4], ids[5])))) is not None


async def test_tidy_answers_two_artists_as_their_window_does(env):
    """The card of two artists has the window's three answers; each is taken back in the journal."""
    from app.db import session_scope
    from app.models import ArtistApart, ArtistLink
    from app.tidy import kinds

    c = env["client"]
    ids = await _three_pairs()
    section = kinds.KINDS["artist"].section
    by = {x["subject_id"]: x["id"] for x in await _open(c, section)}
    r = await c.post(f"/api/tidy/proposals/{by[ids[2]]}/project")
    assert r.status_code == 200 and r.json()["state"] == "accepted", r.text
    assert (await c.post(f"/api/tidy/proposals/{by[ids[4]]}/reject")).json()["state"] == "rejected"
    async with session_scope() as s:
        assert await s.get(ArtistLink, tuple(sorted((ids[2], ids[3])))) is not None
        assert await s.get(ArtistApart, tuple(sorted((ids[4], ids[5])))) is not None
    # the window does not offer «Это другой» again; both taken back, both are questions again
    assert (await c.post(f"/api/tidy/proposals/{by[ids[2]]}/review")).json()["state"] == "open"
    assert (await c.post(f"/api/tidy/proposals/{by[ids[4]]}/review")).json()["state"] == "open"
    async with session_scope() as s:
        assert await s.get(ArtistLink, tuple(sorted((ids[2], ids[3])))) is None
        assert await s.get(ArtistApart, tuple(sorted((ids[4], ids[5])))) is None
    assert len(await _open(c, section)) == 3
    assert (await c.post(f"/api/tidy/proposals/{by[ids[0]]}/project")).status_code == 200
    assert (await c.post(f"/api/tidy/proposals/{by[ids[2]]}/accept")).status_code == 200
