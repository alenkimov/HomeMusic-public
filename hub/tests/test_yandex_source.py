"""Yandex Music as a source of files; an artist of Russia named in Latin; the guard against censored copies."""

from __future__ import annotations

from types import SimpleNamespace as NS

import pytest

from app.db import session_scope
from app.matching import russian
from app.matching.select import needs_uncensored, trusted_original
from app.library import artists
from app.models import ArtistRef, Track, TrackArtist
from app.providers import yandex
from app.providers.base import Candidate, TrackQuery
from app.services import Services
from tests.test_flow import env as env
from tests.test_flow import src_mp3 as src_mp3


def _track(id_, title, artist="Hatsune Miku", ms=200_000, warning=None, version=None):
    return NS(id=id_, title=title, version=version, artists=[NS(name=artist)], albums=[NS(id=7, title="Rejected Diva", year=2024)],
              duration_ms=ms, content_warning=warning, available=True, type="music")


class FakeClient:
    def __init__(self):
        self.asked: list = []

    def tracks(self, ids):
        self.asked.append(("tracks", ids))
        return [_track(ids[0], "Rejected Diva")]

    def search(self, text, type_):
        self.asked.append(("search", text))
        return NS(tracks=NS(results=[_track(1, "Rejected Diva"), _track(2, "Другая", warning="explicit")]))

    def tracks_file_info(self, track_id, quality, codecs, transport):
        self.asked.append(("file", track_id, quality, tuple(codecs), transport))
        return NS(download_info=NS(codec="flac", bitrate=0, url="https://example.invalid/f.flac",
                                   download=lambda path, timeout: open(path, "wb").write(b"f" * 100_000)))


@pytest.fixture
def signed_in(monkeypatch):
    client = FakeClient()

    async def token():
        return 1, "t"

    monkeypatch.setattr(yandex, "plus_token", token)
    monkeypatch.setattr("app.catalog.yandex.yandex_client", lambda t=None, c=None: client)
    return client


async def test_a_track_of_yandex_is_taken_by_its_id(signed_in, tmp_path):
    p = yandex.YandexProvider()
    found = await p.search(TrackQuery(artist="Hatsune Miku", title="Rejected Diva", yandex_id="555"))
    assert [c.provider_id for c in found] == ["555"] and signed_in.asked == [("tracks", ["555"])]
    path = await p.fetch(found[0], tmp_path)
    assert path.name == "yandex_555.flac" and path.stat().st_size == 100_000
    assert signed_in.asked[-1] == ("file", "555", "lossless", ("flac", "mp3"), "raw")
    await p.aclose()


async def test_any_other_is_looked_for_by_its_names(signed_in):
    p = yandex.YandexProvider()
    found = await p.search(TrackQuery(artist="Hatsune Miku", title="Rejected Diva"))
    assert [(c.provider_id, c.explicit, c.duration_s) for c in found] == [("1", None, 200), ("2", True, 200)]
    assert found[0].extra["source"] == "yandex"
    await p.aclose()


async def test_without_an_account_with_plus_nothing_is_found(monkeypatch):
    async def none():
        return None

    monkeypatch.setattr(yandex, "plus_token", none)
    p = yandex.YandexProvider()
    assert await p.search(TrackQuery(artist="A", title="B")) == []
    await p.aclose()


def test_yandex_goes_first_unless_the_lists_name_it(monkeypatch):
    svc = Services()
    svc.providers = {n: NS(name=n) for n in ("yandex", "hitmoz", "hitmos_fm", "ytdlp", "soundcloud", "yt_reupload")}
    assert [p.name for p in svc.provider_order(TrackQuery(artist="Ado", title="Usseewa"))] == ["yandex", "hitmoz", "ytdlp"]
    assert [p.name for p in svc.provider_order(TrackQuery(artist="Кино", title="Звезда"))][:2] == ["yandex", "hitmoz"]
    from app.config import get_settings

    sources = get_settings().cfg.sources
    monkeypatch.setattr(sources, "default", ["hitmoz", "yandex", "ytdlp"])
    assert [p.name for p in svc.provider_order(TrackQuery(artist="Ado", title="Usseewa"))] == ["hitmoz", "yandex", "ytdlp"]
    monkeypatch.setattr(sources, "yandex_first", False)
    monkeypatch.setattr(sources, "default", ["hitmoz", "ytdlp"])
    assert [p.name for p in svc.provider_order(TrackQuery(artist="Ado", title="Usseewa"))] == ["hitmoz", "ytdlp"]


def test_a_copy_of_yandex_is_never_trusted_to_be_uncensored():
    from app.config import get_settings

    c = Candidate(provider="yandex", provider_id="1", artist="Кино", title="Звезда", page_url="", explicit=True,
                  extra={"source": "yandex"})
    assert not trusted_original(c, get_settings().cfg.matching)


def test_the_guard_is_for_russia_only():
    explicit = {"explicit": True}
    assert needs_uncensored(TrackQuery(artist="Скриптонит", title="Танцуй сама", **explicit))
    assert not needs_uncensored(TrackQuery(artist="Скриптонит", title="Танцуй сама", explicit=False))
    assert not needs_uncensored(TrackQuery(artist="Океан Ельзи", title="Обійми", **explicit))  # Ukrainian
    assert not needs_uncensored(TrackQuery(artist="Ирина Кайратовна", title="Айдахар", isrc="KZA012300001", **explicit))
    assert needs_uncensored(TrackQuery(artist="ROCKET", title="Ice", lang="ru", isrc="RUA1D2300001", **explicit))


async def _artist(name, titles, isrc=None, pages=()):
    async with session_scope() as s:
        who = await artists.who(s, name)
        for i, title in enumerate(titles):
            t = Track(ref=f"t:{name}:{i}", title=title, artist=name, isrc=isrc if i == 0 else None,
                      fingerprint=f"{name}|{title}")
            s.add(t)
            await s.flush()
            s.add(TrackArtist(track_id=t.id, artist_id=who.id, position=0))
        for i, page in enumerate(pages):
            s.add(ArtistRef(ref=f"dz:{name}{i}", artist_id=who.id, page_json=page))


async def test_an_artist_named_in_latin_is_of_russia_by_two_titles_in_cyrillic(env):
    russian._known.clear()
    await _artist("LIL MORTY", ["Kisa", "Vinovat", "Плакал"])  # one in the library...
    assert not await russian.russian_artist("LIL MORTY")
    russian._known.clear()
    await _artist("ROCKET", ["Ice"], pages=[{"albums": [{"title": "Инопланетянин"}], "top_tracks": [{"title": "Ракета"}]}])
    assert await russian.russian_artist("ROCKET, Boulevard Depo")  # ...or two with the catalog's pages
    await _artist("OBLADAET", ["Fluffy", "Rain"], isrc="RUA1D2000001")
    assert await russian.russian_artist("OBLADAET")  # registered in Russia
    await _artist("Ado", ["Usseewa", "Odo", "Gira Gira"])
    assert not await russian.russian_artist("Ado")
    assert not await russian.russian_artist("Nobody Known")
