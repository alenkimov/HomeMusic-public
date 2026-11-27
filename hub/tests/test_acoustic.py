"""A recording is known by its sound; the artists of a line of names; an upload the library has."""

from __future__ import annotations

import random

from tests.support import filed_track
from tests.test_plays import env as env  # the fixture


def test_the_artists_of_a_line():
    from app.library.credits import main_artist, split_artists

    assert split_artists("Три дня дождя, Роки") == ["Три дня дождя", "Роки"]
    assert split_artists("TAK feat. Hatsune Miku, Kasane Teto") == ["TAK", "Hatsune Miku", "Kasane Teto"]
    assert split_artists("Tyler, The Creator") == ["Tyler, The Creator"]  # the comma is inside the name
    assert split_artists("Simon, and Garfunkel") == ["Simon, and Garfunkel"]
    assert split_artists("Кино, кино ,  ") == ["Кино"]  # each once
    assert split_artists("") == [] and main_artist("") == ""
    assert main_artist("Три дня дождя, Роки") == "Три дня дождя"


def test_two_pictures_of_one_recording_and_of_two():
    from app.library import acoustic

    rnd = random.Random(7)
    one = [rnd.getrandbits(32) for _ in range(900)]
    # the same recording encoded otherwise: a few bits of each frame differ
    other = [v ^ (1 << rnd.randrange(32)) ^ (1 << rnd.randrange(32)) for v in one]
    assert acoustic.likeness(one, other) > 0.9
    # …and starting later (silence cut off the start): found at its shift
    assert acoustic.likeness(one, other[40:]) > 0.9
    another = [rnd.getrandbits(32) for _ in range(900)]
    assert acoustic.likeness(one, another) < 0.6  # two recordings agree in about half of the bits
    assert acoustic.likeness(one, []) == 0.0
    assert acoustic.unpack(acoustic.pack(one)) == one and acoustic.unpack(None) == []
    # a version over the same music (other words): alike, not the same
    version = [v ^ (rnd.getrandbits(32) & rnd.getrandbits(32) & rnd.getrandbits(32)) for v in one]
    assert acoustic.ALIKE <= acoustic.likeness(one, version) < acoustic.SAME


async def test_an_upload_the_library_has(env):
    """Told by the sound where both files have a picture of it, else by the names and the length; a better file is
    offered in the place of the library's."""
    from sqlalchemy import select

    from app.api.uploads import _better, _twin
    from app.db import session_scope
    from app.library import acoustic
    from app.models import Upload, User

    rnd = random.Random(3)
    sound = [rnd.getrandbits(32) for _ in range(600)]
    async with session_scope() as s:
        uid = (await s.execute(select(User.id))).scalar_one()
        s.add(filed_track(uid="f-1", path="a/1.mp3", nd_song_id="nd1", artist="Три дня дождя", title="Демоны",
                          fingerprint="x", duration_s=205, codec="mp3", bitrate_kbps=128, chroma=acoustic.pack(sound)))
        from app.matching.normalize import fingerprint

        s.add(filed_track(uid="f-2", path="a/2.mp3", nd_song_id="nd2", artist="Три дня дождя, Роки", title="Марафоны",
                          fingerprint=fingerprint("Три дня дождя, Роки", "Марафоны"), duration_s=147, codec="mp3",
                          bitrate_kbps=320))
        await s.flush()
        # the same recording under another name, a FLAC: known by its sound, and the better file
        a = Upload(user_id=uid, staging_path="u/a.flac", orig_name="track01.flac", status="uploaded", duration_s=206.0,
                   probed_tags_json={"artist": "3 дня дождя", "title": "Demons", "codec": "flac"},
                   chroma=acoustic.pack([v ^ 1 for v in sound]))
        # no picture of the library's file: by the names and the length
        b = Upload(user_id=uid, staging_path="u/b.mp3", orig_name="b.mp3", status="uploaded", duration_s=147.0,
                   probed_tags_json={"artist": "Три дня дождя, Роки", "title": "Марафоны", "codec": "mp3", "bitrate_kbps": 128})
        # a recording the library has not
        c = Upload(user_id=uid, staging_path="u/c.mp3", orig_name="c.mp3", status="uploaded", duration_s=205.0,
                   probed_tags_json={"artist": "Кто-то", "title": "Что-то"},
                   chroma=acoustic.pack([rnd.getrandbits(32) for _ in range(600)]))
        s.add_all([a, b, c])
        await s.flush()
        ta, tb, tc = await _twin(s, a), await _twin(s, b), await _twin(s, c)
    assert (ta.title, ta.by, ta.sure, ta.better, ta.codec) == ("Демоны", "sound", True, True, "mp3")
    assert (tb.title, tb.by, tb.better) == ("Марафоны", "name", False)
    assert tc is None
    assert _better("mp3", 320, "mp3", 128) and not _better("mp3", 192, "mp3", 160) and not _better("mp3", 320, "flac", None)


async def test_albums_in_pieces_are_listed(env):
    from sqlalchemy import select

    from app.db import session_scope
    from app.models import Album, User

    c = env["client"]
    async with session_scope() as s:
        uid = (await s.execute(select(User.id))).scalar_one()
        one = Album(uid="a1", title="Любовь, аддикция и марафоны", album_artist="Три дня дождя", created_by=uid)
        two = Album(uid="a2", title="Любовь, аддикция и марафоны", album_artist="Три дня дождя, Роки", created_by=uid)
        other = Album(uid="a3", title="Любовь, аддикция и марафоны", album_artist="Другие", created_by=uid)
        s.add_all([one, two, other])
        await s.flush()
        s.add(filed_track(uid="f1", path="x/1.mp3", artist="Три дня дождя", title="Демоны", fingerprint="a", album_id=one.id, added_by=uid))
        s.add(filed_track(uid="f2", path="x/2.mp3", artist="Три дня дождя", title="Ещё", fingerprint="b", album_id=one.id, added_by=uid))
        s.add(filed_track(uid="f3", path="x/3.mp3", artist="Три дня дождя, Роки", title="Марафоны", fingerprint="c", album_id=two.id, added_by=uid, source="ytdlp"))
        await s.flush()
        ids = (one.id, two.id)
    got = (await c.get("/api/library/albums/split")).json()
    assert len(got) == 1 and got[0]["target_id"] == ids[0]  # the one with the most tracks takes the rest
    assert [(a["id"], a["track_count"]) for a in got[0]["albums"]] == [(ids[0], 2), (ids[1], 1)]
    assert got[0]["dismissed"] is False and all(len(a["track_ids"]) == a["track_count"] for a in got[0]["albums"])
    # each part's credits, each with its page where the Hub knows the artist
    assert [[x["name"] for x in a["artists"]] for a in got[0]["albums"]] == [["Три дня дождя"], ["Три дня дождя", "Роки"]]

    # «Это разные альбомы»: kept apart, offered again only when asked to
    group = {"album_ids": list(ids)}
    assert (await c.post("/api/library/albums/split/dismiss", json=group)).status_code == 200
    assert (await c.post("/api/library/albums/split/dismiss", json=group)).status_code == 200  # twice: one answer
    assert [g["dismissed"] for g in (await c.get("/api/library/albums/split")).json()] == [True]
    assert (await c.post("/api/library/albums/split/restore", json=group)).status_code == 200
    assert [g["dismissed"] for g in (await c.get("/api/library/albums/split")).json()] == [False]
    # where each part came from: here only the source its file was downloaded from
    assert [a["sources"] for a in got[0]["albums"]] == [[], [{"kind": "file", "service": "ytdlp", "count": 1}]]

    # merged with the name and the artist chosen; a picture only of a part being merged
    r = await c.post("/api/library/albums/merge", json={"target_id": ids[0], "source_ids": [ids[1]], "cover_from": 999})
    assert r.status_code == 400
    r = await c.post("/api/library/albums/merge", json={"target_id": ids[0], "source_ids": [ids[1]],
                                                       "title": "Любовь, аддикция и марафоны (Deluxe)", "album_artist": "Три дня дождя"})
    assert r.status_code == 200, r.text
    assert (r.json()["title"], r.json()["album_artist"], r.json()["track_count"]) == ("Любовь, аддикция и марафоны (Deluxe)", "Три дня дождя", 3)


async def test_the_files_as_a_list_and_the_doubles(env):
    from sqlalchemy import select

    from app.db import session_scope
    from app.library import acoustic
    from app.models import User

    rnd = random.Random(11)
    sound = [rnd.getrandbits(32) for _ in range(600)]
    c = env["client"]
    async with session_scope() as s:
        uid = (await s.execute(select(User.id))).scalar_one()
        for n, chroma in (("1", sound), ("2", [v ^ 1 for v in sound]), ("3", [rnd.getrandbits(32) for _ in range(600)])):
            s.add(filed_track(uid=f"d-{n}", path=f"d/{n}.mp3", artist="Кто-то", title=f"Трек {n}", fingerprint=f"d{n}",
                              duration_s=200, codec="mp3", bitrate_kbps=128, chroma=acoustic.pack(chroma), created_by=uid))
    rows = [r for r in (await c.get("/api/library/files")).json() if r["title"].startswith("Трек ")]
    assert sorted(r["title"] for r in rows) == ["Трек 1", "Трек 2", "Трек 3"]
    assert all(r["mine"] and r["no_album"] and not r["kept"] and r["added_at"] and r["file"]["codec"] == "mp3" for r in rows)
    ids = {r["title"]: r["library_track_id"] for r in rows}
    assert (await c.get("/api/library/files/doubles")).json() == [sorted([ids["Трек 1"], ids["Трек 2"]])]


def test_which_file_is_the_better_and_which_is_weak():
    from app.library import quality

    assert quality.better("flac", None, "mp3", 320) and not quality.better("mp3", 320, "flac", None)
    assert quality.better("mp3", 320, "mp3", 128) and not quality.better("mp3", 192, "mp3", 160)
    assert quality.better("opus", 130, "mp3", 128)  # Opus at 130 is heard as an MP3 at about 200
    assert not quality.better("opus", 130, "mp3", 320) and not quality.better("mp3", None, "mp3", 128)
    assert quality.weak("mp3", 128) and quality.weak("opus", 96) and not quality.weak("opus", 130)
    assert not quality.weak("mp3", 192) and not quality.weak("flac", None) and not quality.weak("mp3", None)


def test_the_names_of_a_file_and_an_album_that_is_none():
    from app.library.recognize import names_of, single_album

    assert names_of({"artist": "Кино", "title": "Пачка сигарет"}, "01.mp3") == ("Кино", "Пачка сигарет")
    assert names_of({}, "2kizz, WENARO - Утром.mp3") == ("2kizz, WENARO", "Утром")
    assert names_of({"title": "Утром"}, "07. 2kizz – Утром.mp3") == ("2kizz", "Утром")
    assert names_of({}, "song0.mp3") == ("", "song0")
    assert single_album(None, "X") and single_album("Single", "X") and single_album("x", "X") and single_album("X - Single", "X")
    assert not single_album("Sport", "Low Battery")


async def test_a_better_file_takes_the_place_of_a_weak_one(env, tmp_path):
    """Only the weak files are looked for, once; a file found is weighed by its quality and its sound."""
    from sqlalchemy import select

    from app.db import session_scope
    from app.library import acoustic, upgrade
    from app.library.owned import quiet
    from app.models import DownloadItem, Track, Upgrade, User

    rnd = random.Random(5)
    sound = [rnd.getrandbits(32) for _ in range(600)]
    c = env["client"]
    async with session_scope() as s:
        uid = (await s.execute(select(User.id))).scalar_one()
        s.add(filed_track(uid="w-1", path="w/1.mp3", artist="Кто", title="Слабый", fingerprint="w1", ref="dz:101",
                          duration_s=200, codec="mp3", bitrate_kbps=128, chroma=acoustic.pack(sound), created_by=uid))
        s.add(filed_track(uid="w-2", path="w/2.mp3", artist="Кто", title="Хороший", fingerprint="w2", ref="dz:102",
                          duration_s=200, codec="mp3", bitrate_kbps=320, chroma=acoustic.pack(sound), created_by=uid))
        s.add(filed_track(uid="w-3", path="w/3.mp3", artist="Кто", title="Не из каталога", fingerprint="w3",
                          duration_s=200, codec="mp3", bitrate_kbps=128, chroma=acoustic.pack(sound), created_by=uid))
        await s.flush()
        ids = list((await s.execute(select(Track.id).where(Track.fingerprint.in_(["w1", "w2", "w3"])).order_by(Track.fingerprint))).scalars())
        # the two weak ones are looked for — the one the catalog knows first — and only once
        assert await upgrade.start(s, uid, ids) == 2 and await upgrade.start(s, uid, ids) == 0
        items = list((await s.execute(select(DownloadItem).order_by(DownloadItem.id))).scalars())
        assert [i.query_json["meta"]["upgrade_of"] for i in items] == [ids[0], ids[2]]
        assert all(i.force and quiet(i.query_json) for i in items)
        assert "deezer_track_id" in items[0].query_json["meta"] and "deezer_track_id" not in items[1].query_json["meta"]
        # a file no better stays out, whatever it sounds like
        assert (await upgrade.judge(s, ids[0], tmp_path / "x.mp3", "mp3", 160, 200))[0] == "no"
        row = (await s.execute(select(Upgrade).where(Upgrade.track_id == ids[0]))).scalar_one()
        row.state, row.codec, row.bitrate_kbps, row.likeness = "found", "opus", 160, 0.88
    page = (await c.get("/api/quality")).json()
    assert {r["title"]: r["state"] for r in page["rows"]} == {"Слабый": "found", "Не из каталога": "queued"} and page["weak"] == 0
    # the file found is gone meanwhile: there is nothing to put in — and declining it is the end of it
    assert (await c.post(f"/api/quality/{row.id}/accept")).status_code == 409
    assert (await c.post(f"/api/quality/{row.id}/decline")).status_code == 200
    assert (await c.get("/api/quality")).json()["rows"][0]["state"] in ("kept", "queued")


def test_where_two_files_of_one_recording_part():
    """A copy with a second cut out is the same recording — and another sound where the cut is."""
    from app.library import acoustic

    rnd = random.Random(21)
    one = [rnd.getrandbits(32) for _ in range(1600)]  # ≈ 200 s
    noisy = lambda v: v ^ (1 << rnd.randrange(32))  # noqa: E731 — another encoding: a bit of each frame differs
    copy = [noisy(v) for v in one]
    assert acoustic.parts(one, 200.0, copy, 200.0) == []
    cut = list(copy)
    for i in range(480, 492):  # a second and a half, a minute in
        cut[i] = rnd.getrandbits(32)
    (gap,) = acoustic.parts(one, 200.0, cut, 200.0)
    assert 58 <= gap["at"] <= 61 and 61 <= gap["to"] <= 64 and gap["depth"] < 0.75
    assert abs(gap["at"] - gap["other_at"]) < 0.2
    # the cut copy starts two seconds later: the place is told in each file's own time
    (late,) = acoustic.parts(one, 200.0, cut[16:], 198.0)
    assert 58 <= late["at"] <= 61 and 56 <= late["other_at"] <= 59
    # another recording altogether: nothing to lay side by side
    assert acoustic.parts(one, 200.0, [rnd.getrandbits(32) for _ in range(1600)], 200.0) == []


async def test_doubles_compares_each_new_file_once_and_keeps_what_it_found(hub_env, monkeypatch):
    """A file is compared when it is new; the next asking finds the groups written down, and a page's request leaves
    many new files to the worker's pass."""
    from app.db import session_scope
    from app.library import acoustic
    from app.models import Track, TrackFile

    picture = acoustic.pack([0x5A5A5A5A ^ (i * 2654435761 & 0xFFFFFFFF) for i in range(400)])
    async with session_scope() as s:
        ids = []
        for title in ("A", "A again", "B"):
            t = Track(title=title, artist="X", fingerprint=f"fp-{title}", duration_s=200 if title != "B" else 300)
            s.add(t)
            await s.flush()
            s.add(TrackFile(track_id=t.id, uid=f"u-{title}", path=f"x/{title}.mp3", chroma=picture))
            ids.append(t.id)
    calls = []
    real = acoustic.likeness
    monkeypatch.setattr(acoustic, "likeness", lambda a, b: calls.append(1) or real(a, b))

    assert await acoustic.doubles(budget=1) == []  # three new: too many for a page's request
    assert calls == []
    assert await acoustic.refresh() == [sorted(ids[:2])]  # «B» is of another length: not compared at all
    assert len(calls) == 1
    assert await acoustic.doubles() == [sorted(ids[:2])]  # written down: nothing compared again
    assert len(calls) == 1
