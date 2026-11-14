"""An artist's page beyond what the catalog says: how many listen to them on Last.fm, and popular tracks where the
catalog's own list is short.

The catalog's «popular» is its own listeners' — and for an artist it hardly has an audience of (a vocaloid producer
heard on YouTube and Spotify) it gives three tracks, or one. Last.fm counts listens from everywhere, so where the
catalog gives fewer than `WANT`, the rest is taken from Last.fm's top of the artist: each of its tracks is looked up
in the catalog among this artist's own (so it plays, has a cover and is theirs, not a namesake's) and the ones found
follow the catalog's, in Last.fm's order. The catalog's list is never replaced: its tracks are already the page's own.
Without a Last.fm key, or when it does not answer, the page is what the catalog said.
"""

from __future__ import annotations

import asyncio
import logging
from typing import Any

from app.catalog import namesakes
from app.catalog.cache import cached
from app.catalog.models import CatalogArtist, CatalogTrack
from app.matching.normalize import normalize_title
from app.mixes import sources
from app.services import svc

log = logging.getLogger(__name__)

WANT = 10  # popular tracks of an artist's page
ASKED = 30  # of Last.fm's top looked through for the ones the catalog's list lacks
PARALLEL = 4
STATS_TTL_S = 7 * 86400


async def listeners(name: str) -> int | None:
    """How many listened to the artist on Last.fm (all time); None: no key, no answer, nobody of that name."""
    if not sources.lastfm_configured() or not name.strip():
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


async def _top_names(name: str) -> list[str]:
    async def produce() -> list[str]:
        body = await sources._lastfm("artist.getTopTracks", artist=name, limit=ASKED)
        return [t["name"] for t in sources._as_list((body.get("toptracks") or {}).get("track")) if t.get("name")]

    return await cached(f"lfm:artist_top:{sources._key(name)}", STATS_TTL_S, produce) or []


def _same(a: str, b: str) -> bool:
    return normalize_title(a).text == normalize_title(b).text


async def more(artist: CatalogArtist, have: list[CatalogTrack]) -> list[CatalogTrack]:
    """Popular tracks of the artist to follow the catalog's `have`, up to `WANT` in all: Last.fm's top, each found
    in the catalog as this artist's own. Empty when the catalog's list is long enough or Last.fm has nothing."""
    need = WANT - len(have)
    if need <= 0 or not sources.lastfm_configured() or svc.deezer is None:
        return []
    try:
        names = await _top_names(artist.name)
    except Exception as e:
        log.info("Last.fm top of %r: %r", artist.name, e)
        return []
    names = [n for n in names if not any(_same(n, t.title) for t in have)]
    gate = asyncio.Semaphore(PARALLEL)

    async def find(title: str) -> CatalogTrack | None:
        async with gate:
            try:
                found = await svc.deezer.search_tracks(artist.name, title, limit=5)
            except Exception as e:
                log.info("popular: %r of %r not looked up: %r", title, artist.name, e)
                return None
        own = [t for t in found if t.artist_ref == artist.ref or artist.ref in (t.artist_refs or [])]
        own = namesakes.own_tracks(artist.ref, own)  # not a namesake's release
        return next((t for t in own if _same(t.title, title)), None)

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
