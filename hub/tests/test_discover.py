"""«Открытия недели»: only what the user does not know, little-known artists first, one track of an artist."""

from __future__ import annotations

from datetime import date, datetime

from app.mixes import build as b
from app.mixes import discover as d
from tests.test_mix_build import known, profile, tr


def cand(n, artist, *, pos=0, artist_ref=None, title="") -> b.Cand:
    return b.Cand(track=tr(n, artist, title, artist_ref=artist_ref or f"dz:a{n}"), gens={"ytm_radio": pos})


async def test_only_the_unknown_and_the_little_known_first(monkeypatch):
    fans = {"dz:a1": 2_000_000, "dz:a2": 900, "dz:a3": 150_000, "dz:a7": 40_000, "dz:a10": 12}

    async def fans_of(ref):
        return fans.get(ref)

    async def no_sound(p, cands, stats):
        return {}

    monkeypatch.setattr(d, "fans_of", fans_of)
    monkeypatch.setattr(b, "sound", no_sound)
    p = profile(known(50, "Слышанный"), listens={"Слушаю Давно": 3})
    owned = cand(6, "Новый, но скачан")
    owned.nd_song_id = "nd6"
    cands = [
        cand(1, "Звезда"),                        # too many listeners
        cand(2, "Ноунейм", pos=1),
        cand(3, "Средний", pos=0),
        cand(4, "Слушаю Давно"),                  # an artist of the history
        cand(5, "Из Библиотеки"),                 # an artist of the library
        owned,                                    # the library has the track
        cand(7, "Ноунейм", pos=2, artist_ref="dz:a2", title="Вторая"),  # the second track of an artist
        b.Cand(track=known(50, "Слышанный").track, gens={"ytm_radio": 0}),  # heard
        cand(8, "Был На Прошлой Неделе"),          # in an earlier issue
        cand(9, "Дуэт", title="Песня (Live)"),     # a live version
        cand(10, "Никто"),                         # nobody listens to: a stray upload, most likely
        cand(11, "Слышанный", title="Другая"),     # another track of an artist the profile knows
    ]
    stats: dict = {}
    out = await d.pick(p, cands, known_artists={b.artist_key("Из Библиотеки")}, past={"dz:8"}, stats=stats)

    assert [c.track.artist for c in out] == ["Ноунейм", "Средний"]  # the less known one first, each artist once
    assert stats["rejected"] == {"popular": 1, "nobody": 1, "known_artist": 3, "library": 1, "heard": 1, "earlier_issue": 1, "version": 1}


def test_the_week_begins_on_monday_morning():
    assert d.week_of(datetime(2026, 11, 12, 15, 0)) == date(2026, 11, 9)  # a Thursday
    assert d.week_of(datetime(2026, 11, 9, 4, 59)) == date(2026, 11, 2)  # Monday before the hour: still last week
    assert d.week_of(datetime(2026, 11, 9, 5, 0)) == date(2026, 11, 9)


def test_obscurity():
    assert d.obscurity(900, None) == d.obscurity(20_000, None) == 1.0  # little-known in full
    assert 0 < d.obscurity(150_000, None) < d.obscurity(40_000, None) < 1
    assert d.obscurity(300_000, None) == d.obscurity(1_000_000, None) == 0.0
    assert d.obscurity(None, None) == 0.5
