"""Search and match recordings across the catalogs available to the Hub."""

from __future__ import annotations

import asyncio
import logging
from collections.abc import Awaitable, Iterable
from dataclasses import dataclass

from app.catalog.models import ArtistPage, CatalogArtist, CatalogTrack, SearchResults
from app.config import get_settings
from app.matching.normalize import clean_isrc, normalize_artist, normalize_title, version_kind
from app.matching.score import artist_sim
from app.matching.select import Verdict, classify_scores, match_catalog
from app.providers.base import TrackQuery
from app.services import svc

log = logging.getLogger(__name__)

SOURCE_PRIORITY = {"sp": 0, "dz": 1, "ym": 2}
SPOTIFY_SEARCH_LIMIT = 100


class CatalogUnavailable(Exception):
    """No queried catalog supplied a result. Callers can retry a failed lookup."""


def source_available(ref: str) -> bool:
    return {"sp": svc.spotify, "dz": svc.deezer, "ym": svc.yandex}.get(ref.partition(":")[0]) is not None


def preferred_refs(refs: Iterable[str]) -> list[str]:
    return sorted(dict.fromkeys(ref for ref in refs if source_available(ref)),
                  key=lambda ref: SOURCE_PRIORITY[ref.partition(":")[0]])


async def artist_page(refs: Iterable[str]) -> ArtistPage | None:
    for ref in preferred_refs(refs):
        try:
            return await svc.artist_page(ref)
        except Exception as error:
            log.info("catalog artist %s unavailable: %r", ref, error)
    return None


async def _read[T](calls: dict[str, Awaitable[T]], *, timeout: float = 25) -> list[T]:
    async def one(source: str, call: Awaitable[T]) -> T:
        try:
            async with asyncio.timeout(timeout):
                return await call
        except Exception as error:
            log.info("catalog lookup %s failed: %r", source, error)
            raise

    results = await asyncio.gather(*(one(source, call) for source, call in calls.items()), return_exceptions=True)
    available = [result for result in results if not isinstance(result, BaseException)]
    if results and not available:
        raise CatalogUnavailable("Каталоги не ответили")
    return available


async def search(query: str, *, kinds: tuple[str, ...], limit: int = 10, timeout: float = 25) -> SearchResults:
    calls = {}
    if svc.spotify is not None:
        calls["spotify"] = svc.spotify.search(query, kinds=kinds, limit=limit)
    if svc.deezer is not None:
        calls["deezer"] = svc.deezer.search(query, kinds=kinds, limit=limit)
    if svc.yandex is not None:
        calls["yandex"] = svc.yandex.search(query)
    found = await _read(calls, timeout=timeout)
    return SearchResults(**{
        kind + "s": [row for result in found for row in getattr(result, kind + "s")[:limit]]
        for kind in kinds
    })


async def track_page(query: str, offset: int, limit: int, *, timeout: float = 25) -> tuple[list[CatalogTrack], int | None]:
    calls = {}
    if svc.spotify is not None and offset < SPOTIFY_SEARCH_LIMIT:
        async def spotify_page() -> tuple[list[CatalogTrack], int | None]:
            # SpotAPI takes a limit and returns results starting at the beginning.
            end = min(SPOTIFY_SEARCH_LIMIT, offset + limit)
            result = await svc.spotify.search(query, kinds=("track",), limit=end)
            return result.tracks[offset:end], end if end < SPOTIFY_SEARCH_LIMIT and len(result.tracks) >= end else None

        calls["spotify"] = spotify_page()
    if svc.deezer is not None:
        calls["deezer"] = svc.deezer.search_track_page(query, offset, limit)
    if svc.yandex is not None and not offset:
        async def yandex_page() -> tuple[list[CatalogTrack], None]:
            return (await svc.yandex.search(query)).tracks[:limit], None

        calls["yandex"] = yandex_page()
    pages = await _read(calls, timeout=timeout)
    return ([track for rows, _ in pages for track in rows],
            min((next_ for _, next_ in pages if next_ is not None), default=None))


def named_artist(name: str, candidates: Iterable[CatalogArtist]) -> CatalogArtist | None:
    want = normalize_artist(name)
    for source in SOURCE_PRIORITY:
        matches = {row.ref: row for row in candidates if row.source == source
                   and artist_sim(want, normalize_artist(row.name)) == 1}
        if len(matches) == 1:
            return next(iter(matches.values()))
    return None


@dataclass(frozen=True)
class TrackMatch:
    verdict: Verdict
    track: CatalogTrack
    score: float


def _compatible(q: TrackQuery, t: CatalogTrack, credited: list[str], *, ratio: float) -> bool:
    if q.explicit is not None and t.explicit is not None and q.explicit != t.explicit:
        return False
    query_version = version_kind(q.version or normalize_title(q.title).version)
    track_version = version_kind(normalize_title(t.title).version)
    if query_version != track_version:
        return False
    want = [normalize_artist(name) for name in credited]
    got = [normalize_artist(name) for name in (t.artists or [t.artist])]
    return any(artist_sim(a, b) >= ratio for a in want for b in got)


def _close_duration(q: TrackQuery, t: CatalogTrack, tolerance: int) -> bool:
    return not q.duration_s or not t.duration_s or abs(q.duration_s - t.duration_s) <= tolerance


async def match_track(q: TrackQuery, *, credited: list[str] | None = None) -> TrackMatch | None:
    names = credited or [q.artist]
    artist = names[0]
    calls = {}
    cfg = get_settings().cfg.matching
    if svc.spotify is not None:
        async def spotify_tracks() -> list[CatalogTrack]:
            return (await svc.spotify.search(f"{artist} {q.title}", kinds=("track",), limit=10)).tracks

        calls["spotify"] = spotify_tracks()
    if svc.deezer is not None:
        async def deezer_tracks() -> list[CatalogTrack]:
            catalog = svc.deezer
            if code := clean_isrc(q.isrc):
                try:
                    track = await catalog.get_track_by_isrc(code)
                except Exception as error:
                    log.info("Deezer ISRC lookup failed: %r", error)
                else:
                    if track is not None and _close_duration(q, track, 5) and _compatible(q, track, names, ratio=1):
                        return [track]
            return await catalog.search_tracks(artist, q.title, limit=10)

        calls["deezer"] = deezer_tracks()
    if svc.yandex is not None:
        calls["yandex"] = svc.yandex.search_tracks(artist, q.title, limit=10)

    found = await _read(calls)
    matches: list[TrackMatch] = []
    code = clean_isrc(q.isrc)
    for shelf in found:
        candidates = [t for t in shelf if _compatible(q, t, names, ratio=cfg.artist_min_ratio)]
        coded = [t for t in candidates if code and clean_isrc(t.isrc) == code
                 and _close_duration(q, t, 5) and _compatible(q, t, names, ratio=1)]
        if len(coded) == 1:
            matches.append(TrackMatch("auto", coded[0], 1.0))
            continue
        scored = match_catalog(artist, q.title, candidates, album=q.album, duration=q.duration_s, version=q.version)
        if scored:
            track, score = scored[0]
            verdict = classify_scores([s.total for _, s in scored], cfg)
            if verdict == "auto" and not _close_duration(q, track, cfg.duration_tolerance_s):
                verdict = "confirm"
            matches.append(TrackMatch(verdict, track, score.total))
    levels = {"auto": 2, "confirm": 1, "not_found": 0}
    return max(matches, key=lambda m: (levels[m.verdict], m.score, -SOURCE_PRIORITY[m.track.source]), default=None)
