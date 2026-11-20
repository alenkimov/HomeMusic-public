from datetime import date
from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest

from app.catalog.models import ArtistPage, CatalogAlbum, CatalogArtist, CatalogTrack, SearchResults
from app.library import recognize
from app.services import svc


TAGS = {"artist": "Исполнитель", "title": "Песня", "album": "Пластинка"}


@pytest.fixture(autouse=True)
def catalogs(monkeypatch):
    for name in ("deezer", "spotify", "yandex"):
        monkeypatch.setattr(svc, name, None)
    monkeypatch.setattr(recognize, "_released", {})
    monkeypatch.setattr(recognize, "_kinds", {})


def recording(source: str, *, album: str = "Пластинка") -> CatalogTrack:
    return CatalogTrack(ref=f"{source}:123", source=source, id="123", title=TAGS["title"],
                        artist=TAGS["artist"], album_title=album, album_ref=f"{source}:456", duration_s=180)


def release(source: str, *, tracks: list[CatalogTrack] | None = None) -> CatalogAlbum:
    return CatalogAlbum(ref=f"{source}:456", source=source, id="456", title=TAGS["album"],
                        artist=TAGS["artist"], year=2025, release_date=date(2025, 1, 1),
                        record_type="ep", tracks=tracks)


async def test_file_search_compares_enabled_catalogs_and_prefers_spotify_on_a_tie(monkeypatch):
    spotify = SimpleNamespace(search=AsyncMock(return_value=SearchResults(tracks=[recording("sp")])),
                              album=AsyncMock(return_value=release("sp")))
    deezer = SimpleNamespace(search_track_page=AsyncMock(return_value=([recording("dz")], None)))
    yandex = SimpleNamespace(search=AsyncMock(return_value=SearchResults(tracks=[recording("ym")])))
    monkeypatch.setattr(svc, "spotify", spotify)
    monkeypatch.setattr(svc, "deezer", deezer)
    monkeypatch.setattr(svc, "yandex", yandex)

    result = await recognize.of_file(TAGS, "Песня.flac", 180)

    assert result["state"] == "found"
    assert result["meta"]["track_ref"] == "sp:123"
    assert result["meta"]["record_type"] == "ep"
    spotify.search.assert_awaited()
    deezer.search_track_page.assert_awaited()
    yandex.search.assert_awaited()


@pytest.mark.parametrize("source", ["sp", "ym"])
async def test_file_can_find_the_tagged_release_in_spotify_or_yandex_discography(monkeypatch, source):
    track = recording(source).model_copy(update={"album_title": None})
    artist = CatalogArtist(ref=f"{source}:789", source=source, id="789", name=TAGS["artist"])
    shelf, whole = release(source), release(source, tracks=[track])

    async def search(query, **kwargs):
        if query == TAGS["artist"]:
            return SearchResults(artists=[artist])
        return SearchResults()

    if source == "sp":
        catalog = SimpleNamespace(search=AsyncMock(side_effect=search),
                                  discography=AsyncMock(return_value=([shelf], False, False)),
                                  album=AsyncMock(return_value=whole))
        monkeypatch.setattr(svc, "spotify", catalog)
        discography = catalog.discography
    else:
        catalog = SimpleNamespace(search=AsyncMock(side_effect=search),
                                  get_artist=AsyncMock(return_value=ArtistPage(artist=artist, albums=[shelf])),
                                  get_album=AsyncMock(return_value=whole))
        monkeypatch.setattr(svc, "yandex", catalog)
        discography = catalog.get_artist

    result = await recognize.of_file(TAGS, "Песня.flac", 180)

    discography.assert_awaited_once_with("789")
    assert result["state"] == "found"
    assert result["meta"]["track_ref"] == track.ref
    assert result["meta"]["album"] == TAGS["album"]
    assert result["meta"]["year"] == 2025


async def test_uploaded_file_can_be_linked_to_a_yandex_record_manually(monkeypatch):
    track = recording("ym")
    album_track = track.model_copy(update={"disc_no": 2, "track_no": 3})
    yandex = SimpleNamespace(get_track=AsyncMock(return_value=track),
                             get_album=AsyncMock(return_value=release("ym", tracks=[album_track])))
    monkeypatch.setattr(svc, "yandex", yandex)

    result = await recognize.of_ref(track.ref)

    assert result["state"] == "found"
    assert result["by_hand"]
    assert result["meta"]["track_ref"] == track.ref
    assert result["meta"]["disc_no"] == 2
    assert result["meta"]["track_no"] == 3
