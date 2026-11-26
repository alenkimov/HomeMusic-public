"""An artist of Russia, though named in Latin (Pharaoh, Scriptonite, ROCKET): their tracks take the Russian source
order and, explicit, the guard against censored copies (`app.matching.select.needs_uncensored`).

An artist the Hub knows is Russian when at least `CYRILLIC_TITLES` of their tracks and releases it knows are named in
Cyrillic — the library's tracks, and the releases and most listened tracks of their pages in the catalogs — or one of
their tracks is registered in Russia (ISRC `RU…`). In a library looked at, the artists not of Russia had no title in
Cyrillic at all, and the Russian ones named in Latin had from 7 % of them to all: a count, not a share. Two, not one:
one may be a track with a Russian guest, credited to both.
"""

from __future__ import annotations

import logging
import time

from sqlalchemy import select

from app.db import session_scope
from app.matching.normalize import has_cyrillic

log = logging.getLogger(__name__)

CYRILLIC_TITLES = 2
TTL_S = 3600

_known: dict[str, tuple[float, bool]] = {}


def _titles(page: dict | None) -> list[str]:
    if not isinstance(page, dict):
        return []
    rows = [*(page.get("albums") or []), *(page.get("top_tracks") or [])]
    return [str(r.get("title") or "") for r in rows if isinstance(r, dict)]


async def russian_artist(name: str) -> bool:
    """The main artist of a credit («A, B» — A) is of Russia, by what the Hub knows of them."""
    main = (name or "").split(",")[0].strip()
    if not main:
        return False
    key = main.casefold()
    if (hit := _known.get(key)) and time.monotonic() - hit[0] < TTL_S:
        return hit[1]
    try:
        said = await _said(main)
    except Exception as e:  # the database away: the names alone decide, as before
        log.warning("russian artist %s not looked up: %r", main, e)
        return False
    _known[key] = (time.monotonic(), said)
    if len(_known) > 5000:
        _known.clear()
    return said


async def _said(main: str) -> bool:
    from app.library import artists
    from app.models import ArtistRef, Track, TrackArtist

    async with session_scope() as s:
        who = await artists.holder(s, main)
        if who is None:
            said = False
        else:
            tracks = select(Track.title, Track.isrc).join(TrackArtist, TrackArtist.track_id == Track.id).where(
                TrackArtist.artist_id == who.id)
            rows = (await s.execute(tracks.limit(500))).all()
            russian_isrc = any((isrc or "").upper().startswith("RU") for _, isrc in rows)
            titles = {t for t, _ in rows if t}
            if not russian_isrc and sum(map(has_cyrillic, titles)) < CYRILLIC_TITLES:
                for page in await s.scalars(select(ArtistRef.page_json).where(ArtistRef.artist_id == who.id)):
                    titles.update(_titles(page))
            said = russian_isrc or sum(map(has_cyrillic, titles)) >= CYRILLIC_TITLES
    return said
