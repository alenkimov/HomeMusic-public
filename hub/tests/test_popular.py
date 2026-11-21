"""Popular tracks of an artist where the catalog's own list is short: the rest is Last.fm's top, each track found in
the catalog as this artist's own."""

from __future__ import annotations

from types import SimpleNamespace
from unittest.mock import AsyncMock

from app.catalog.models import ArtistPage, CatalogArtist, CatalogTrack


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

    monkeypatch.setattr(popular.sources, "lastfm_configured", AsyncMock(return_value=True))
    monkeypatch.setattr(popular, "_top_names", top_names)
    monkeypatch.setattr(popular, "svc", SimpleNamespace(deezer=SimpleNamespace(search_tracks=search_tracks)))
    monkeypatch.setitem(namesakes._by_artist, "dz:7", {"dz:gone"})

    got = await popular.more(artist, have)
    assert [t.title for t in got] == ["mochimochi", "LEMON"]  # Last.fm's order, without what the catalog gave
    assert "pppp" not in asked  # the catalog's own list has it already: not looked up

    # what the artist's own catalog lacks is looked for in Yandex, among the tracks that credit this very name
    def ym(id_, title, artists):
        return CatalogTrack(ref=f"ym:{id_}", source="ym", id=id_, title=title, artist=artists[0], artists=artists, artist_ref="ym:1")

    async def yandex_tracks(name, title, limit=5):
        return {"Not in the catalog": [ym("9", "Not in the catalog", ["Somebody", "tak"])], "Smoke": [ym("8", "Smoke", ["Another"])]}.get(title, [])

    monkeypatch.setattr(popular, "svc", SimpleNamespace(deezer=SimpleNamespace(search_tracks=search_tracks), yandex=SimpleNamespace(search_tracks=yandex_tracks)))
    assert [t.ref for t in await popular.more(artist, have)] == ["dz:3", "ym:9", "dz:6"]
    monkeypatch.setattr(popular, "svc", SimpleNamespace(deezer=SimpleNamespace(search_tracks=search_tracks)))

    # how many listen to which, by the track's name: the first of a name Last.fm keeps twice
    async def top(name):
        return [{"name": "PPPP", "listeners": 243}, {"name": "pppp", "listeners": 5}, {"name": "LEMON", "listeners": None}]

    monkeypatch.setattr(popular, "_top", top)
    assert await popular.heard("TAK") == {popular.song("pppp"): 243}

    # the catalog's list is long enough: Last.fm is not asked
    asked.clear()
    assert await popular.more(artist, [track(str(i), f"t{i}") for i in range(10)]) == [] and asked == []
    # no key: the page is what the catalog said
    monkeypatch.setattr(popular.sources, "lastfm_configured", AsyncMock(return_value=False))
    assert await popular.more(artist, have) == [] and await popular.listeners("TAK") is None


async def test_ranked_popular_keeps_the_source_for_each_position(monkeypatch):
    from app.catalog import popular

    def page(source, titles):
        artist = CatalogArtist(ref=f"{source}:7", source=source, id="7", name="TAK")
        tracks = [CatalogTrack(ref=f"{source}:{i}", source=source, id=str(i), title=title,
                               artist=artist.name, artist_ref=artist.ref) for i, title in enumerate(titles)]
        return ArtistPage(artist=artist, top_tracks=tracks)

    spotify = page("sp", ["PPPP", "numb numb"])
    deezer = page("dz", ["numb numb", "LEMON"])
    yandex = page("ym", ["mochimochi", "Psycho Mode", "a guest track"])
    yandex.top_tracks[-1].artist_ref = "ym:99"

    async def lastfm(artist, have, refs):
        assert [t.title for t in have] == ["PPPP", "numb numb", "LEMON"]
        return [track("9", "mochimochi")]

    monkeypatch.setattr(popular, "more", lastfm)
    ranked = await popular.ranked(deezer.artist, [yandex, deezer, spotify])
    assert [(t.title, service) for t, service in ranked] == [
        ("PPPP", "spotify"), ("numb numb", "spotify"), ("LEMON", "deezer"),
        ("mochimochi", "lastfm"), ("Psycho Mode", "yandex"),
    ]
