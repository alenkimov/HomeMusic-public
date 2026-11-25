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

    async def doubles(s):
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
