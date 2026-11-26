"""Yandex Music as a catalog: tracks, albums and artists the other catalogs
lack — Russian-language music first of all — with their covers, the artists' pictures and how many listen to them.

A record is "ym:<id>". What Yandex gives without a sign-in is read here; what needs one (stations, lyrics, the
audio itself) is not: a track of this catalog is listened to and downloaded from the Hub's sources, by its names,
like a track of any other catalog. Yandex keeps no ISRC of a track.

Read through the Hub's own client of Yandex's API (`app.catalog.ym`): asynchronous, limited, proxied, journaled.
"""

from __future__ import annotations

import logging
from datetime import date
from typing import Any

from app.catalog.cache import cached
from app.catalog.models import ArtistPage, CatalogAlbum, CatalogArtist, CatalogNotFound, CatalogTrack, SearchResults
from app.catalog.yandex import yandex_client, yandex_track_row
from app.integrations import shared_search_credential

log = logging.getLogger(__name__)

SOURCE = "ym"
SEARCH_TTL_S = 86400
TTL_S = 7 * 86400
ARTIST_TTL_S = 86400
COVER, COVER_SMALL = "1000x1000", "200x200"
ALBUMS_PAGE = 100
ALBUM_PAGES = 5
RECORD_TYPES = {"single": "single", "compilation": "compile"}  # an album has no `type`; Yandex knows no EP


def _img(uri: str | None, size: str) -> str | None:
    return "https://" + uri.replace("%%", size) if uri else None


def _ref(id_: Any) -> str:
    return f"{SOURCE}:{id_}"


def _named(title: str | None, version: str | None) -> str:
    title, version = (title or "").strip(), (version or "").strip()
    return f"{title} ({version})" if version else title


def _released(a: Any) -> tuple[date | None, int | None]:
    """(the day, the year) of a release. Yandex's day is often the day a reissue came out, its year — the year of
    the music: the day is kept only where the two agree."""
    year = getattr(a, "year", None)
    try:
        day = date.fromisoformat(str(getattr(a, "release_date", None) or "")[:10])
    except ValueError:
        day = None
    return (day if day and (not year or day.year == year) else None), year or (day.year if day else None)


def artist_of(a: Any) -> CatalogArtist:
    uri = getattr(getattr(a, "cover", None), "uri", None) or getattr(a, "og_image", None)
    counts = getattr(a, "counts", None)
    return CatalogArtist(ref=_ref(a.id), source=SOURCE, id=str(a.id), name=a.name, picture_url=_img(uri, COVER),
                         picture_small_url=_img(uri, COVER_SMALL),
                         nb_albums=getattr(counts, "direct_albums", None), nb_fans=getattr(a, "likes_count", None))


def album_of(a: Any, tracks: list[CatalogTrack] | None = None) -> CatalogAlbum:
    who = [x for x in (a.artists or []) if getattr(x, "name", None)]
    day, year = _released(a)
    uri = getattr(a, "cover_uri", None)
    return CatalogAlbum(
        ref=_ref(a.id), source=SOURCE, id=str(a.id), title=_named(a.title, getattr(a, "version", None)),
        artist=who[0].name if who else "", artist_ref=_ref(who[0].id) if who else None,
        artists=[x.name for x in who], artist_refs=[_ref(x.id) for x in who],
        record_type=RECORD_TYPES.get(getattr(a, "type", None) or "", "album"), release_date=day, year=year,
        cover_url=_img(uri, COVER), cover_small_url=_img(uri, COVER_SMALL), nb_tracks=getattr(a, "track_count", None),
        explicit=getattr(a, "content_warning", None) == "explicit" or None, genre=getattr(a, "genre", None),
        tracks=tracks)


def track_of(t: Any, album: Any | None = None) -> CatalogTrack | None:
    """A track of Yandex (`app.catalog.ym`) → a track of the catalog (None: a podcast, a row with no name). `album`: the release it
    was read in, where the track itself names another (a single put into an album later)."""
    row = yandex_track_row(t)
    if row is None or getattr(t, "id", None) is None:
        return None
    who = [x for x in (t.artists or []) if getattr(x, "name", None)]
    albums = getattr(t, "albums", None) or []
    al = album or (albums[0] if albums else None)
    place = next((x.track_position for x in albums if al is not None and str(x.id) == str(al.id)
                  and getattr(x, "track_position", None)), None)
    day, _ = _released(al) if al is not None else (None, None)
    uri = getattr(t, "cover_uri", None) or getattr(al, "cover_uri", None)
    al_who = [x for x in (getattr(al, "artists", None) or []) if getattr(x, "name", None)]
    return CatalogTrack(
        ref=_ref(str(t.id).split(":")[0]), source=SOURCE, id=str(t.id).split(":")[0], title=row["title"],
        artist=who[0].name if who else row["artist"], artists=[x.name for x in who],
        artist_refs=[_ref(x.id) for x in who], artist_ref=_ref(who[0].id) if who else None,
        album_ref=_ref(al.id) if al is not None else None,
        album_title=_named(al.title, getattr(al, "version", None)) if al is not None and al.title else None,
        album_artist=al_who[0].name if al_who else None, duration_s=row["duration_s"],
        isrc=getattr(t, "isrc", None),
        track_no=getattr(place, "index", None), disc_no=getattr(place, "volume", None),
        explicit=bool(getattr(t, "explicit", None)) or getattr(t, "content_warning", None) == "explicit" or None,
        release_date=day, cover_url=_img(uri, COVER), cover_small_url=_img(uri, COVER_SMALL))


def _own_first(artist_ref: str, tracks: list[Any]) -> list[CatalogTrack]:
    """An artist's most listened as Yandex lists them, their own tracks before the ones they are a guest on: the
    list counts every track that credits them, and somebody else's track is not what they are known by."""
    found = [x for t in tracks if (x := track_of(t)) is not None]
    return sorted(found, key=lambda t: t.artist_ref != artist_ref)


class YandexCatalog:
    async def _call(self, f: Any) -> Any:
        from app.catalog.ym import YandexError

        try:
            return await f()
        except YandexError as e:
            if e.kind == "not_found":
                raise CatalogNotFound(str(e)) from e
            raise

    async def search(self, q: str) -> SearchResults:
        """The first tracks, albums and artists Yandex finds by the words (one request, no pages)."""
        q = " ".join(q.split())
        if not q:
            return SearchResults()

        async def read(token: str | None = None) -> dict:
            r = await yandex_client(token).search(q, type_="all")
            got = lambda kind: (getattr(getattr(r, kind, None), "results", None) or []) if r is not None else []  # noqa: E731
            return SearchResults(
                artists=[artist_of(a) for a in got("artists") if getattr(a, "name", None) and not getattr(a, "various", False)],
                albums=[album_of(a) for a in got("albums") if getattr(a, "title", None)],
                tracks=[x for t in got("tracks") if (x := track_of(t)) is not None]).model_dump(mode="json")

        async def search() -> dict:
            try:
                return await self._call(read)
            except Exception:
                token = await shared_search_credential("yandex")
                if not token:
                    raise
                try:
                    return await self._call(lambda: read(token))
                except Exception:
                    raise RuntimeError("Yandex catalogue search unavailable") from None

        return SearchResults.model_validate(await cached(f"ym:search:{q.lower()}", SEARCH_TTL_S, search))

    async def search_tracks(self, artist: str, title: str, limit: int = 10) -> list[CatalogTrack]:
        return (await self.search(f"{artist} {title}")).tracks[:limit]

    async def _artist(self, artist_id: str) -> dict:
        async def read() -> dict:
            c = yandex_client()
            b = await c.artists_brief_info(artist_id)
            if b is None or b.artist is None:
                raise CatalogNotFound(artist_id)
            albums: list[Any] = []
            for page in range(ALBUM_PAGES):
                got = await c.artists_direct_albums(artist_id, page=page, page_size=ALBUMS_PAGE, sort_by="year")
                albums += list(got.albums or []) if got is not None else []
                if got is None or not got.albums or len(albums) >= ((got.pager.total if got.pager else 0) or 0):
                    break
            artist = artist_of(b.artist).model_copy(update={"nb_albums": len(albums) or None})
            releases = sorted((album_of(a) for a in albums if getattr(a, "title", None)),
                              key=lambda a: (a.release_date or date(a.year or 1, 1, 1)), reverse=True)
            stats = getattr(b, "stats", None)
            return {
                "page": ArtistPage(artist=artist, top_tracks=_own_first(artist.ref, b.popular_tracks or []), albums=releases).model_dump(mode="json"),
                "listeners": getattr(stats, "last_month_listeners", None),
                "similar": [artist_of(a).model_dump(mode="json") for a in (b.similar_artists or []) if getattr(a, "name", None)],
            }

        return await cached(f"ym:artist2:{artist_id}", ARTIST_TTL_S, lambda: self._call(read))

    async def get_artist(self, artist_id: str | int) -> ArtistPage:
        """Their page: the most listened tracks and every release of their own, the newest first."""
        return ArtistPage.model_validate((await self._artist(str(artist_id)))["page"])

    async def listeners(self, artist_id: str | int) -> int | None:
        """How many listened to the artist on Yandex in the last month."""
        return (await self._artist(str(artist_id))).get("listeners")

    async def similar(self, artist_id: str | int) -> list[CatalogArtist]:
        return [CatalogArtist.model_validate(a) for a in (await self._artist(str(artist_id))).get("similar") or []]

    async def find_artist(self, name: str) -> CatalogArtist | None:
        """The artist Yandex knows by exactly this name (None: nobody, or only somebody of another name)."""
        from app.library.artists import key

        return next((a for a in (await self.search(name)).artists if key(a.name) == key(name)), None)

    async def get_album(self, album_id: str | int) -> CatalogAlbum:
        album_id = str(album_id)

        async def read() -> dict:
            a = await yandex_client().albums_with_tracks(album_id)
            if a is None or not a.title:
                raise CatalogNotFound(album_id)
            tracks = [x for vol in (a.volumes or []) for t in vol if (x := track_of(t, a)) is not None]
            return album_of(a, tracks).model_dump(mode="json")

        return CatalogAlbum.model_validate(await cached(f"ym:album:{album_id}", TTL_S, lambda: self._call(read)))

    async def get_track(self, track_id: str | int) -> CatalogTrack:
        track_id = str(track_id)

        async def read() -> dict:
            found = await yandex_client().tracks([track_id]) or []
            t = track_of(found[0]) if found else None
            if t is None:
                raise CatalogNotFound(track_id)
            return t.model_dump(mode="json")

        return CatalogTrack.model_validate(await cached(f"ym:track:{track_id}", TTL_S, lambda: self._call(read)))
