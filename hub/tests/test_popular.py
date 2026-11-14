"""Popular tracks of an artist where the catalog's own list is short: the rest is Last.fm's top, each track found in
the catalog as this artist's own."""

from __future__ import annotations

from types import SimpleNamespace

from app.catalog.models import CatalogArtist, CatalogTrack


def track(id_: str, title: str, artist_ref: str = "dz:7", album_ref: str = "dz:a1") -> CatalogTrack:
    return CatalogTrack(ref=f"dz:{id_}", source="dz", id=id_, title=title, artist="TAK", artist_ref=artist_ref, album_ref=album_ref)


async def test_the_rest_of_popular_is_lastfms_and_the_artists_own(monkeypatch):
    from app.catalog import namesakes, popular

    artist = CatalogArtist(ref="dz:7", source="dz", id="7", name="TAK")
    have = [track("1", "PPPP"), track("2", "numb numb")]
    shelf = {
        "mochimochi": [track("3", "mochimochi")],
        "Smoke": [track("4", "Smoke", artist_ref="dz:999")],  # a namesake under another id: not theirs
        "Psycho Mode": [track("5", "Psycho Mode", album_ref="dz:gone")],  # of a release said to be a namesake's
        "LEMON": [track("6", "LEMON")],
    }
    asked: list[str] = []

    async def search_tracks(name, title, limit=5):
        asked.append(title)
        return shelf.get(title, [])

    async def top_names(name):
        return ["pppp", "mochimochi", "Smoke", "Psycho Mode", "Not in the catalog", "LEMON"]

    monkeypatch.setattr(popular.sources, "lastfm_configured", lambda: True)
    monkeypatch.setattr(popular, "_top_names", top_names)
    monkeypatch.setattr(popular, "svc", SimpleNamespace(deezer=SimpleNamespace(search_tracks=search_tracks)))
    monkeypatch.setitem(namesakes._by_artist, "dz:7", {"dz:gone"})

    got = await popular.more(artist, have)
    assert [t.title for t in got] == ["mochimochi", "LEMON"]  # Last.fm's order, without what the catalog gave
    assert "pppp" not in asked  # the catalog's own list has it already: not looked up

    # the catalog's list is long enough: Last.fm is not asked
    asked.clear()
    assert await popular.more(artist, [track(str(i), f"t{i}") for i in range(10)]) == [] and asked == []
    # no key: the page is what the catalog said
    monkeypatch.setattr(popular.sources, "lastfm_configured", lambda: False)
    assert await popular.more(artist, have) == [] and await popular.listeners("TAK") is None
