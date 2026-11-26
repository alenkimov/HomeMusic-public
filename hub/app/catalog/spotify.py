"""Read-only Spotify Web Player catalogue. Spotify is never an audio provider."""

from __future__ import annotations

import asyncio
import logging
import re
from collections.abc import Awaitable, Callable
from contextlib import aclosing
from datetime import date
from hashlib import sha256
from typing import Any
from urllib.parse import urljoin, urlsplit

import httpx
from pydantic import BaseModel
from spotapi import Catalogue
from spotapi.catalogue import Operations, Queries
from spotapi.models.catalogue import from_dict
from spotapi.models.catalogue.artist_details import ArtistDetails
from spotapi.models.catalogue.playlist_track import PlaylistTrack
from spotapi.models.catalogue.track import Track as SearchTrack

from app.catalog.cache import cache_get, cache_set, cached
from app.catalog.models import ArtistPage, CatalogAlbum, CatalogArtist, CatalogPlaylist, CatalogTrack, SearchResults
from app.integrations import shared_search_credential

log = logging.getLogger(__name__)
ID = re.compile(r"[A-Za-z0-9]{22}")
KINDS = ("artist", "album", "track", "playlist")
SHORT_HOSTS = {"spotify.link", "spotify.app.link"}
HOSTS = {"open.spotify.com", "play.spotify.com"}
TRACK_TTL_S = 24 * 60 * 60
# an artist's page and a release: a day (they were an hour; the plays counted change little in a day, and an artist's
# page waited for Spotify on nearly every opening)
ARTIST_TTL_S = 24 * 60 * 60
ALBUM_TTL_S = 24 * 60 * 60
PLAYLIST_TTL_S = 15 * 60


def resource(value: str) -> tuple[str, str] | None:
    value = value.strip()
    if value.startswith("spotify:"):
        parts = value.split(":")[1:]
    else:
        try:
            url = urlsplit(value)
            if url.scheme not in ("http", "https") or url.hostname not in HOSTS or url.username is not None:
                return None
        except ValueError:
            return None
        parts = url.path.strip("/").split("/")
        if parts and parts[0].startswith("intl-"):
            parts.pop(0)
        if parts and parts[0] == "embed":
            parts.pop(0)
    if len(parts) == 2 and parts[0] in KINDS and ID.fullmatch(parts[1]):
        return parts[0], parts[1]
    return None


def is_short(value: str) -> bool:
    try:
        url = urlsplit(value)
        return url.scheme == "https" and url.hostname in SHORT_HOSTS and url.username is None and url.port in (None, 443)
    except ValueError:
        return False


async def expand(value: str) -> tuple[str, str] | None:
    if found := resource(value):
        return found
    from app.outbound import RoutingTransport

    async with httpx.AsyncClient(timeout=6, transport=RoutingTransport()) as client:
        for _ in range(4):
            if not is_short(value):
                return None
            response = await client.get(value)
            location = response.headers.get("location")
            if not response.is_redirect or not location:
                return None
            value = urljoin(value, location)
            if found := resource(value):
                return found
    return None


def _id(row: Any) -> str:
    return row.uri.rsplit(":", 1)[-1]


def _image(image: Any, size: int = 0) -> str | None:
    rows = getattr(image, "sources", None) or []
    if not rows:
        return None
    if size:
        return min(rows, key=lambda x: abs((x.width or size) - size)).url
    return max(rows, key=lambda x: x.width or 0).url


def _credits(row: Any) -> list[tuple[str, str]]:
    groups = (getattr(row, "artists", None), getattr(row, "first_artist", None), getattr(row, "other_artists", None))
    return list(dict.fromkeys((a.profile.name, f"sp:{_id(a)}")
                              for group in groups if group for a in group.items if a.profile and a.profile.name))


def _date(value: Any) -> tuple[date | None, int | None]:
    if value is None:
        return None, None
    year = value.year
    iso = getattr(value, "iso_string", None)
    try:
        if iso and getattr(value, "precision", None) not in ("YEAR", "MONTH", "year", "month"):
            return date.fromisoformat(iso[:10]), year
        if getattr(value, "month", None) and getattr(value, "day", None):
            return date(year, value.month, value.day), year
    except ValueError:
        pass
    return None, year


def _artist(row: Any) -> CatalogArtist:
    id_ = _id(row)
    photo = getattr(row.visuals, "avatar_image", None)
    stats = getattr(row, "stats", None)
    return CatalogArtist(ref=f"sp:{id_}", source="sp", id=id_, name=row.profile.name,
                         picture_url=_image(photo), picture_small_url=_image(photo, 240),
                         nb_fans=getattr(stats, "followers", None), monthly_listeners=getattr(stats, "monthly_listeners", None))


def _album(row: Any, artist: CatalogArtist | None = None) -> CatalogAlbum:
    id_ = _id(row)
    names = _credits(row) or ([(artist.name, artist.ref)] if artist else [])
    dt, year = _date(getattr(row, "date", None))
    kind = {"SINGLE": "single", "EP": "ep", "COMPILATION": "compile"}.get((row.type or "").upper(), "album")
    return CatalogAlbum(ref=f"sp:{id_}", source="sp", id=id_, title=row.name,
                        artist=names[0][0] if names else "", artist_ref=names[0][1] if names else None,
                        artists=[n for n, _ in names], artist_refs=[r for _, r in names], record_type=kind,
                        release_date=dt, year=year, cover_url=_image(row.cover_art),
                        cover_small_url=_image(row.cover_art, 240),
                        nb_tracks=getattr(getattr(row, "tracks_v2", None), "total_count", None))


def _track(row: Any, *, artist: CatalogArtist | None = None, album: CatalogAlbum | None = None) -> CatalogTrack:
    id_ = _id(row)
    names = _credits(row) or ([(artist.name, artist.ref)] if artist else [])
    release = getattr(row, "album_of_track", None)
    count = str(getattr(row, "playcount", "") or "")
    return CatalogTrack(
        ref=f"sp:{id_}", source="sp", id=id_, title=row.name, spotify_id=id_,
        artist=names[0][0] if names else "", artists=[n for n, _ in names], artist_refs=[r for _, r in names],
        artist_ref=names[0][1] if names else None,
        album_ref=album.ref if album else f"sp:{_id(release)}" if release else None,
        album_title=album.title if album else getattr(release, "name", None),
        album_artist=album.artist if album else None, release_date=album.release_date if album else None,
        cover_url=album.cover_url if album else _image(getattr(release, "cover_art", None)),
        cover_small_url=album.cover_small_url if album else _image(getattr(release, "cover_art", None), 240),
        duration_s=round(row.duration.total_milliseconds / 1000) if row.duration else None,
        track_no=getattr(row, "track_number", None), disc_no=getattr(row, "disc_number", None),
        spotify_plays=int(count) if count.isdecimal() else None,
    )


def _playlist(row: Any) -> CatalogPlaylist:
    images = getattr(row.images, "items", None) or []
    owner = getattr(row.owner_v2, "data", None)
    return CatalogPlaylist(id=_id(row), title=row.name, owner=getattr(owner, "name", None),
                           cover_url=_image(images[0], 240) if images else None,
                           total_tracks=getattr(getattr(row, "content", None), "total_count", None))



_PATH = re.compile(r"\.([A-Za-z_][A-Za-z0-9_]*)|\[(\d+)\]|\[('[^']*'|\"[^\"]*\")\]")


def _camel(name: str) -> str:
    head, *rest = name.split("_")
    return head + "".join(w[:1].upper() + w[1:] for w in rest)


def _drop(data: Any, path: str) -> bool:
    """Leaves out of `data` what `path` («ArtistDetails.profile.biography», «X.items[3]») names; False: not found."""
    steps = [(m.group(1), m.group(2), m.group(3)) for m in _PATH.finditer(path[path.find("."):] if "." in path else "")]
    if not steps:
        return False
    node = data
    for name, index, key in steps[:-1]:
        if index is not None:
            if not isinstance(node, list) or int(index) >= len(node):
                return False
            node = node[int(index)]
        else:
            k = key[1:-1] if key else name
            if not isinstance(node, dict):
                return False
            k = k if k in node else _camel(k)
            if k not in node:
                return False
            node = node[k]
    name, index, key = steps[-1]
    if index is not None:
        if isinstance(node, list) and int(index) < len(node):
            node.pop(int(index))
            return True
        return False
    k = key[1:-1] if key else name
    if isinstance(node, dict):
        for cand in (k, _camel(k)):
            if cand in node:
                del node[cand]
                return True
    return False


def lenient[T](cls: type[T], raw: Any, tries: int = 20) -> T:
    """`from_dict` of spotapi, which refuses the whole answer for one field of a shape it does not expect (an
    artist's biography of another form): such a field is left out — the optional ones only — and it is read again."""
    import copy

    data = copy.deepcopy(raw)
    for _ in range(tries):
        try:
            return from_dict(cls, data)
        except (TypeError, ValueError) as e:
            said = str(e)
            path = said.split(":", 1)[0]
            if "required field is missing" in said or not _drop(data, path):
                raise
            log.info("spotify: %s left out of the answer (%s)", path, said[len(path) + 2:][:120])
    return from_dict(cls, data)

class SpotifyLocalTrack(BaseModel):
    uri: str
    title: str
    artist: str
    album: str | None = None
    duration_s: int | None = None

    @property
    def id(self) -> str:
        return "local:" + sha256(self.uri.encode("utf-8")).hexdigest()


def _playlist_track(entry: dict) -> CatalogTrack | SpotifyLocalTrack | None:
    data = (entry.get("itemV2") or {}).get("data") or {}
    uri = data.get("uri")
    if not isinstance(uri, str):
        return None
    if uri.startswith("spotify:track:"):
        return _track(lenient(PlaylistTrack, data))
    if uri.startswith("spotify:local:") and (title := data.get("name")):
        ms = (data.get("localTrackDuration") or {}).get("totalMilliseconds")
        return SpotifyLocalTrack(uri=uri, title=title, artist=data.get("artistName") or "", album=data.get("albumName") or None,
                                 duration_s=round(ms / 1000) if isinstance(ms, int) and ms > 0 else None)
    return None


async def read_playlist(client: Catalogue, id_: str) -> tuple[CatalogPlaylist, list[CatalogTrack | SpotifyLocalTrack], int]:
    uri = f"spotify:playlist:{id_}"
    info = _playlist(await client.get_playlist(uri))
    tracks: list[CatalogTrack | SpotifyLocalTrack] = []
    skipped = 0
    # The typed playlist method omits tracks stored locally by the playlist owner.
    pages = client.queries.paginate(
        Operations.FETCH_PLAYLIST_CONTENTS.value,
        {"uri": uri, "offset": 0, "limit": 100, "enableWatchFeedEntrypoint": False,
         "includeEpisodeContentRatingsV2": True},
        extract_page=lambda data: data["playlistV2"]["content"],
    )
    async with aclosing(pages):
        async for page in pages:
            for entry in page["items"]:
                if (track := _playlist_track(entry)) is not None:
                    tracks.append(track)
                else:
                    skipped += 1
    return info, tracks, skipped


class SpotifyCatalog:
    def __init__(self) -> None:
        self._client: Catalogue | None = None
        self._opening: asyncio.Task[Catalogue] | None = None
        self._gate = asyncio.Semaphore(8)
        self._release_gate = asyncio.Semaphore(2)
        self._discographies: dict[str, asyncio.Task[dict]] = {}
        self._reads: dict[str, asyncio.Task[tuple[dict, bool]]] = {}

    async def _open(self) -> Catalogue:
        client = Catalogue()
        async with asyncio.timeout(50):
            self._client = await client.__aenter__()
        return self._client

    @staticmethod
    def _log_open(task: asyncio.Task[Catalogue]) -> None:
        if not task.cancelled() and (error := task.exception()) is not None:
            log.info("Spotify guest session unavailable: %r", error)

    def warm(self) -> None:
        if self._client is None and (self._opening is None or self._opening.done()):
            self._opening = asyncio.create_task(self._open())
            self._opening.add_done_callback(self._log_open)

    async def _get(self) -> Catalogue:
        self.warm()
        if self._client is not None:
            return self._client
        assert self._opening is not None
        return await asyncio.shield(self._opening)

    async def aclose(self) -> None:
        pending = [*self._discographies.values(), *self._reads.values()]
        for job in pending:
            job.cancel()
        await asyncio.gather(*pending, return_exceptions=True)
        if self._opening is not None and not self._opening.done():
            self._opening.cancel()
            await asyncio.gather(self._opening, return_exceptions=True)
        if self._client is not None:
            await self._client.__aexit__(None, None, None)
            self._client = None

    async def _cached_read(self, key: str, ttl_s: int, read: Callable[[], Awaitable[dict]], *,
                           fresh: bool = False) -> dict:
        """Share one read per resource; cancelling a caller does not cancel the shared work."""
        async def load() -> tuple[dict, bool]:
            if not fresh and (hit := await cache_get(key)) is not None:
                return hit, False
            result = await read()
            await cache_set(key, result, ttl_s)
            return result, True

        def finished(task: asyncio.Task[tuple[dict, bool]]) -> None:
            self._reads.pop(key, None)
            if not task.cancelled():
                task.exception()  # a caller might have cancelled while the shared read was running

        while True:
            task = self._reads.get(key)
            if task is None:
                task = self._reads[key] = asyncio.create_task(load())
                task.add_done_callback(finished)
            result, fetched = await asyncio.shield(task)
            if fetched or not fresh:
                return result
            # A manual refresh may have joined a pending cache hit. Read Spotify in that case.

    async def search(self, q: str, *, kinds: tuple[str, ...] = ("artist", "album", "track", "playlist"),
                     limit: int = 20) -> SearchResults:
        async def read() -> dict:
            try:
                async with self._gate, asyncio.timeout(22):
                    result = await (await self._get()).search(q, types=kinds, limit=limit)
            except Exception:
                token = await shared_search_credential("spotify") if "track" in kinds else None
                if not token:
                    raise
                from app.catalog import spotify_session

                try:
                    async with self._gate:
                        tracks = await spotify_session.search_tracks(token, q, limit)
                except Exception:
                    raise RuntimeError("Spotify catalogue search unavailable") from None
                return SearchResults(tracks=[CatalogTrack.model_validate(t) for t in tracks]).model_dump(mode="json")
            return SearchResults(artists=[_artist(a) for a in result.artists],
                                 albums=[_album(a) for a in result.albums],
                                 tracks=[_track(t) for t in result.tracks],
                                 playlists=[_playlist(p) for p in result.playlists]).model_dump(mode="json")

        return SearchResults.model_validate(await cached(f"sp:search:{','.join(kinds)}:{limit}:{q.casefold()}", 900, read))

    async def artist_page(self, id_: str) -> ArtistPage:
        async def read() -> dict:
            op = Operations.QUERY_ARTIST_OVERVIEW
            uri = f"spotify:artist:{id_}"
            async with self._gate, asyncio.timeout(22):
                c = await self._get()
                response = await c.queries.query(op.value, {"uri": uri, "locale": "", "preReleaseV2": False,
                                                            "includeMusicVideoPreviews": False})
            raw = Queries.resource_data(response, op, ArtistDetails, uri)
            row = lenient(ArtistDetails, raw)
            artist = _artist(row)
            visuals = raw.get("visuals") or {}
            wide = (raw.get("visualIdentity") or {}).get("wideFullBleedImage") or {}
            sources = wide.get("sources") or (visuals.get("headerImage") or {}).get("sources") or []
            if not sources:
                sources = [image for item in (visuals.get("gallery") or {}).get("items", [])
                           for image in item.get("sources", []) if (image.get("width") or 0) >= 640
                           and 0 < (image.get("height") or 0) <= image["width"] / 1.5]
            if sources:
                artist.banner_url = max(sources, key=lambda image: image.get("width") or 0).get("url")
            disc = row.discography
            releases = [r for group in (disc.albums, disc.singles, disc.compilations) if group
                        for item in group.items for r in item.releases.items] if disc else []
            if disc and disc.latest:
                releases.append(disc.latest)
            top_rows = {t.track.uri: _track(t.track, artist=artist) for t in disc.top_tracks.items} if disc and disc.top_tracks else {}
            for item in (raw.get("discography") or {}).get("topTracks", {}).get("items", []):
                track = item.get("track") or {}
                if top := top_rows.get(track.get("uri")):
                    try:
                        enriched = _track(from_dict(SearchTrack, track), artist=artist)
                    except (TypeError, ValueError, KeyError, AttributeError):
                        continue
                    top_rows[track["uri"]] = enriched.model_copy(update={"spotify_plays": top.spotify_plays})
            albums = {_id(a): _album(a, artist) for a in releases}
            return ArtistPage(artist=artist, top_tracks=list(top_rows.values()), albums=list(albums.values())).model_dump(mode="json")

        return ArtistPage.model_validate(await cached(f"sp:artist:{id_}", ARTIST_TTL_S, read))

    async def discography(self, id_: str) -> tuple[list[CatalogAlbum], bool, bool]:
        page = await self.artist_page(id_)
        key = f"sp:discography:{id_}"

        async def read() -> dict:
            albums = {a.ref: a for a in page.albums}
            partial = False
            try:
                async with self._release_gate, asyncio.timeout(140):
                    c = await self._get()
                    async with aclosing(c.get_artist_releases(f"spotify:artist:{id_}")) as pages:
                        async for releases in pages:
                            for release in releases:
                                album = _album(release, page.artist)
                                albums.setdefault(album.ref, album)
            except Exception as error:
                log.info("Spotify discography %s is incomplete: %r", id_, error)
                partial = True
            result = {"albums": [a.model_dump(mode="json") for a in albums.values()], "partial": partial}
            await cache_set(key, result, 120 if partial else 3600)
            return result

        result = await cache_get(key)
        if result is None:
            job = self._discographies.get(id_)
            if job is None:
                job = self._discographies[id_] = asyncio.create_task(read())

                def finished(task: asyncio.Task[dict]) -> None:
                    self._discographies.pop(id_, None)
                    if not task.cancelled() and (error := task.exception()) is not None:
                        log.info("Spotify discography cache %s: %r", id_, error)

                job.add_done_callback(finished)
            try:
                result = await asyncio.wait_for(asyncio.shield(job), 2)
            except TimeoutError:
                return page.albums, True, True
        return [CatalogAlbum.model_validate(a) for a in result["albums"]], result["partial"], False

    async def album(self, id_: str) -> CatalogAlbum:
        async def read() -> dict:
            async with self._gate, asyncio.timeout(25):
                c = await self._get()
                uri = f"spotify:album:{id_}"
                row = await c.get_album(uri)
                album = _album(row)
                tracks = row.tracks
                if album.nb_tracks and album.nb_tracks > len(tracks):
                    tracks = []
                    async with aclosing(c.get_album_tracks(uri)) as pages:
                        async for batch in pages:
                            tracks.extend(batch)
            album.tracks = [_track(t, album=album) for t in tracks]
            return album.model_dump(mode="json")

        return CatalogAlbum.model_validate(await cached(f"sp:album:{id_}", ALBUM_TTL_S, read))

    async def track(self, id_: str) -> CatalogTrack:
        async def read() -> dict:
            async with self._gate, asyncio.timeout(15):
                row = await (await self._get()).get_track(f"spotify:track:{id_}")
            return _track(row).model_dump(mode="json")

        return CatalogTrack.model_validate(await self._cached_read(f"sp:track:{id_}", TRACK_TTL_S, read))

    async def track_genres(self, ids: list[str]) -> dict[str, list[str]]:
        from app.catalog import spotify_genres

        ids = [id_ for id_ in dict.fromkeys(ids) if ID.fullmatch(id_)]
        if not ids:
            return {}
        async with self._gate, asyncio.timeout(30):
            return await spotify_genres.fetch(await self._get(), ids)

    async def playlist(self, id_: str, *, fresh: bool = False) -> tuple[CatalogPlaylist, list[CatalogTrack | SpotifyLocalTrack], int]:
        async def read() -> dict:
            async with self._gate, asyncio.timeout(75):
                info, tracks, skipped = await read_playlist(await self._get(), id_)
            return {"playlist": info.model_dump(mode="json"),
                    "tracks": [track.model_dump(mode="json") for track in tracks], "skipped": skipped}

        result = await self._cached_read(f"sp:playlist-items:{id_}", PLAYLIST_TTL_S, read, fresh=fresh)
        return CatalogPlaylist.model_validate(result["playlist"]), [
            SpotifyLocalTrack.model_validate(track) if "uri" in track else CatalogTrack.model_validate(track)
            for track in result["tracks"]
        ], result["skipped"]
