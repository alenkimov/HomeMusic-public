"""Popular tracks in ranking-source order, and Last.fm audience statistics."""

from __future__ import annotations

import asyncio
import logging
from collections.abc import Awaitable, Callable
from typing import Any

from app.catalog import namesakes
from app.catalog.cache import cached
from app.catalog.models import ArtistPage, CatalogArtist, CatalogTrack
from app.matching.normalize import normalize_title
from app.mixes import sources
from app.services import svc

log = logging.getLogger(__name__)

WANT = 10  # popular tracks of an artist's page
ASKED = 50  # of Last.fm's top: looked through for the ones the catalog's list lacks, and for who listens to which
PARALLEL = 4
STATS_TTL_S = 7 * 86400


async def listeners(name: str) -> int | None:
    """How many listened to the artist on Last.fm (all time); None: no key, no answer, nobody of that name."""
    if not await sources.lastfm_configured() or not name.strip():
        return None

    async def produce() -> dict[str, Any]:
        body = await sources._lastfm("artist.getInfo", artist=name)
        stats = (body.get("artist") or {}).get("stats") or {}
        return {"listeners": int(stats["listeners"]) if str(stats.get("listeners") or "").isdigit() else None}

    try:
        return (await cached(f"lfm:artist_stats:{sources._key(name)}", STATS_TTL_S, produce) or {}).get("listeners")
    except Exception as e:
        log.info("Last.fm stats of %r: %r", name, e)
        return None


async def _top(name: str) -> list[dict[str, Any]]:
    """Last.fm's top of the artist, the most listened first: each track's name and how many listened to it."""
    async def produce() -> list[dict[str, Any]]:
        body = await sources._lastfm("artist.getTopTracks", artist=name, limit=ASKED)
        return [{"name": t["name"], "listeners": int(t["listeners"]) if str(t.get("listeners") or "").isdigit() else None}
                for t in sources._as_list((body.get("toptracks") or {}).get("track")) if t.get("name")]

    return await cached(f"lfm:artist_top2:{sources._key(name)}", STATS_TTL_S, produce) or []


async def _top_names(name: str) -> list[str]:
    return [t["name"] for t in await _top(name)]


def song(title: str) -> str:
    """A track's name as Last.fm's count of it is looked up by."""
    return normalize_title(title).text


async def heard(name: str) -> dict[str, int]:
    """How many listened to each of the artist's most listened tracks on Last.fm (all time), by `song` of its name.
    Empty: no key, no answer."""
    if not await sources.lastfm_configured() or not name.strip():
        return {}
    try:
        top = await _top(name)
    except Exception as e:
        log.info("Last.fm top of %r: %r", name, e)
        return {}
    out: dict[str, int] = {}
    for t in top:  # a name Last.fm keeps twice (another spelling of it) is its first, the most listened
        if t.get("listeners"):
            out.setdefault(song(t["name"]), t["listeners"])
    return out


async def track_listeners(artist: str, title: str) -> int | None:
    """The Last.fm audience for one track, including songs outside the artist's top."""
    if not await sources.lastfm_configured() or not artist.strip() or not title.strip():
        return None

    async def produce() -> dict[str, int | None]:
        body = await sources._lastfm("track.getInfo", artist=artist, track=title, autocorrect=0)
        count = (body.get("track") or {}).get("listeners")
        return {"listeners": int(count) if str(count).isdecimal() else None}

    info = await cached(f"lfm:track_listeners:{sources._key(artist, title)}", STATS_TTL_S, produce)
    return info["listeners"]


def _same(a: str, b: str) -> bool:
    return normalize_title(a).text == normalize_title(b).text


async def ranked(artist: CatalogArtist, pages: list[ArtistPage]) -> list[tuple[CatalogTrack, str]]:
    """Fill the list in priority order and retain the service that gave each position."""
    result: list[tuple[CatalogTrack, str]] = []
    seen: set[str] = set()

    def add(tracks: list[CatalogTrack], service: str) -> None:
        for track in tracks:
            if len(result) >= WANT:
                return
            key = song(track.title) or track.ref
            if key not in seen:
                seen.add(key)
                result.append((track, service))

    def from_catalog(source: str, *, own_only: bool = False) -> list[CatalogTrack]:
        shelves = [[t for t in namesakes.own_tracks(p.artist.ref, p.top_tracks)
                    if not own_only or t.artist_ref == p.artist.ref or
                    (t.artist_ref is None and sources._key(t.artist) == sources._key(p.artist.name))]
                   for p in pages if p.artist.source == source]
        tracks = [t for shelf in shelves for t in shelf]
        if len(shelves) > 1:
            tracks.sort(key=lambda t: (t.spotify_plays if source == "sp" else t.rank) or 0, reverse=True)
            return tracks[:max(map(len, shelves))]
        return tracks

    add(from_catalog("sp"), "spotify")
    add(from_catalog("dz"), "deezer")
    add(await more(artist, [t for t, _ in result], [p.artist.ref for p in pages]), "lastfm")
    add(from_catalog("ym", own_only=bool(result)), "yandex")
    add(from_catalog("hm"), "hitmoz")
    return result


async def more(artist: CatalogArtist, have: list[CatalogTrack], refs: list[str] | None = None) -> list[CatalogTrack]:
    """Popular tracks of the artist to follow the catalog's `have`, up to `WANT` in all: Last.fm's top, each found
    in the catalog as this artist's own. Empty when the catalog's list is long enough or Last.fm has nothing."""
    need = WANT - len(have)
    if need <= 0 or not await sources.lastfm_configured():
        return []
    searches: dict[str, Callable[[str, str, int], Awaitable[list[CatalogTrack]]]] = {}
    if spotify := getattr(svc, "spotify", None):
        async def search_spotify(name: str, title: str, limit: int) -> list[CatalogTrack]:
            return (await spotify.search(f"{name} {title}", kinds=("track",), limit=limit)).tracks

        searches["sp"] = search_spotify
    if svc.deezer is not None:
        searches["dz"] = svc.deezer.search_tracks
    if yandex := getattr(svc, "yandex", None):
        searches["ym"] = yandex.search_tracks
    if not searches:
        return []
    try:
        names = await _top_names(artist.name)
    except Exception as e:
        log.info("Last.fm top of %r: %r", artist.name, e)
        return []
    names = [n for n in names if not any(_same(n, t.title) for t in have)]
    gate = asyncio.Semaphore(PARALLEL)
    profiles = [artist.ref, *(refs or [])]
    profiles_by_source = {source: {ref for ref in profiles if ref.partition(":")[0] == source} for source in searches}
    artist_name = sources._key(artist.name)

    async def find(title: str) -> CatalogTrack | None:
        async with gate:
            for source, search in searches.items():
                linked = profiles_by_source[source]
                if source == "dz" and not linked:
                    continue
                try:
                    found = [t for t in await search(artist.name, title, 5) if _same(t.title, title)]
                except Exception as error:
                    log.info("popular: %r of %r not looked up in %s: %r", title, artist.name, source, error)
                    continue
                if linked:
                    own = {t.ref for ref in linked for t in namesakes.own_tracks(ref, found)
                           if ref == t.artist_ref or ref in t.artist_refs}
                    hit = next((t for t in found if t.ref in own), None)
                else:
                    hit = next((t for t in found if any(sources._key(name) == artist_name for name in [t.artist, *t.artists])), None)
                if hit is not None:
                    return hit
        return None

    out: list[CatalogTrack] = []
    seen = {t.ref for t in have}
    # a few more than needed are asked for at once: some are not in the catalog
    for t in await asyncio.gather(*(find(n) for n in names[: need * 2 + 2])):
        if t is not None and t.ref not in seen and not any(_same(t.title, x.title) for x in out):
            seen.add(t.ref)
            out.append(t)
            if len(out) >= need:
                break
    return out
