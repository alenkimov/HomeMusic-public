from types import SimpleNamespace
from unittest.mock import AsyncMock

from app.catalog.models import CatalogArtist, CatalogTrack, SearchResults


async def test_lastfm_popular_uses_spotify_and_checks_the_linked_artist(hub_env, monkeypatch):
    from app.catalog import namesakes, popular
    from app.config import get_settings
    from app.main import create_app

    monkeypatch.setenv("LASTFM_API_KEY", "test-key")
    get_settings.cache_clear()
    artist = CatalogArtist(ref="dz:7", source="dz", id="7", name="TAK")

    def track(source, id_, title, artist_id, *, album="release"):
        return CatalogTrack(ref=f"{source}:{id_}", source=source, id=id_, title=title, artist=artist.name,
                            artist_ref=f"{source}:{artist_id}", album_ref=f"{source}:{album}")

    spotify_results = {
        "TAK Песня": SearchResults(tracks=[track("sp", "1", "Песня", "999"), track("sp", "2", "Песня", "7")]),
        "TAK Другая": SearchResults(tracks=[track("sp", "3", "Другая", "7", album="excluded")]),
        "TAK Третья": OSError("Spotify unavailable"),
    }

    async def search_spotify(query, *, kinds, limit):
        assert kinds == ("track",) and limit == 5
        result = spotify_results[query]
        if isinstance(result, Exception):
            raise result
        return result

    spotify = AsyncMock(side_effect=search_spotify)
    deezer = AsyncMock(side_effect=lambda name, title, limit: [track("dz", "4", "Другая", "7")] if title == "Другая" else [])
    yandex = AsyncMock(return_value=[track("ym", "5", "Третья", "7")])
    monkeypatch.setattr(popular, "_top_names", AsyncMock(return_value=["Песня", "Другая", "Третья"]))

    app = create_app()
    async with app.router.lifespan_context(app):
        monkeypatch.setitem(namesakes._by_artist, "sp:7", {"sp:excluded"})
        monkeypatch.setattr(popular, "svc", SimpleNamespace(spotify=SimpleNamespace(search=spotify),
                                                           deezer=SimpleNamespace(search_tracks=deezer),
                                                           yandex=SimpleNamespace(search_tracks=yandex)))
        assert [t.ref for t in await popular.more(artist, [], ["sp:7", "ym:7"])] == ["sp:2", "dz:4", "ym:5"]
        assert {call.args[1] for call in deezer.await_args_list} == {"Другая", "Третья"}
        yandex.assert_awaited_once_with("TAK", "Третья", 5)
