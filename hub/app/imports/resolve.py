"""Matching an import's rows against the library and the catalog, the way the assistant does it (`match_catalog`,
duration ±3 s, the album as a hint), but quietly and in batches.

Verdicts: `owned` — in the library already; `exact` — a confident catalog match (goes to work without asking);
`probable` — a weaker one (goes to work too, but the report lists it); `not_found` — waits for the user.
What a service id turned out to be is kept in `source_map` for every user: a second import of the same
songs barely searches. The library is checked every time (it changes), the catalog once.
"""

from __future__ import annotations

import logging
from dataclasses import dataclass
from typing import Any

from sqlalchemy.dialects.postgresql import insert
from sqlalchemy.ext.asyncio import AsyncSession

from app.catalog import lookup
from app.catalog.models import CatalogTrack, make_ref
from app.config import get_settings
from app.library.owned import find_owned
from app.matching.normalize import has_cyrillic
from app.matching.score import score
from app.matching.select import classify_scores, rank_candidates
from app.models import SourceMap, Track, utcnow
from app.providers.base import TrackQuery
from app.services import svc

log = logging.getLogger(__name__)

VERDICT = {"auto": "exact", "confirm": "probable", "not_found": "not_found"}
NOT_FOUND_TTL_DAYS = 7  # a catalog miss is looked up again after this long (catalogs and sources grow)


@dataclass
class Resolution:
    match: str  # owned/exact/probable/not_found
    ref: str | None = None
    score: float | None = None
    track: Track | None = None  # owned
    catalog: CatalogTrack | None = None  # the catalog's record of the track, where it was just found


async def _remembered(s: AsyncSession, source: str, kind: str, external_id: str) -> SourceMap | None:
    row = await s.get(SourceMap, (source, kind, external_id))
    if row is None:
        return None
    if row.match == "not_found" and (utcnow() - row.updated_at).days >= NOT_FOUND_TTL_DAYS:
        return None
    return row


async def _remember(s: AsyncSession, source: str, kind: str, external_id: str, r: Resolution) -> None:
    # The same song can be resolved concurrently from likes and a playlist (or two users).
    stmt = insert(SourceMap).values(source=source, kind=kind, external_id=external_id,
                                    ref=r.ref, match=r.match, score=r.score, updated_at=utcnow())
    await s.execute(stmt.on_conflict_do_update(
        index_elements=[SourceMap.source, SourceMap.kind, SourceMap.external_id],
        set_={name: getattr(stmt.excluded, name) for name in ("ref", "match", "score", "updated_at")},
    ))


async def _owned(s: AsyncSession, raw: dict[str, Any], ref: str | None = None) -> Track | None:
    return await find_owned(s, artist=raw.get("artist") or "", title=raw.get("title") or "",
                            duration_s=raw.get("duration_s"), catalog_ref=ref)


async def catalog_track(raw: dict[str, Any]) -> Resolution:
    cfg = get_settings().cfg.matching
    artist, title = raw.get("artist") or "", raw.get("title") or ""
    main = (raw.get("artists") or [artist])[0]
    q = TrackQuery(artist=main, title=title, album=raw.get("album"), duration_s=raw.get("duration_s"),
                   isrc=raw.get("isrc"), explicit=raw.get("explicit"))
    best = await lookup.match_track(q, credited=raw.get("artists"))
    if best is not None and best.verdict != "not_found":
        return Resolution(VERDICT[best.verdict], best.track.ref, round(best.score, 3), catalog=best.track)
    if has_cyrillic(artist + title) and (hm := svc.providers.get("hitmoz")) is not None:
        try:
            ranked = rank_candidates(q, await hm.search(q, limit=5), cfg)
        except Exception as e:
            log.info("import: hitmoz search failed for %s - %s: %r", artist, title, e)
            ranked = []
        if ranked and ranked[0].score >= cfg.auto_threshold:
            return Resolution("probable", make_ref("hm", ranked[0].provider_id), round(ranked[0].score, 3))
    return Resolution("not_found", None, round(best.score, 3) if best else None)


async def resolve_track(s: AsyncSession, source: str, external_id: str, raw: dict[str, Any]) -> Resolution:
    lib = await _owned(s, raw)
    known = await _remembered(s, source, "track", external_id)
    if lib is not None:
        return Resolution("owned", known.ref if known else None, 1.0, lib)
    if known is not None:
        r = Resolution(known.match, known.ref, known.score)
    else:
        if source == "spotify" and (record := raw.get("catalog")):
            c = CatalogTrack.model_validate(record)
            r = Resolution("exact", c.ref, 1.0, catalog=c)
        else:
            r = await catalog_track(raw)
        await _remember(s, source, "track", external_id, r)
        if r.catalog is not None:
            # the catalog's own record is kept: the track the Hub makes of the row is named by it, not by the service
            from app.mixes.resolve import remember_track

            await remember_track(r.catalog, s)
    if r.ref and (lib := await _owned(s, raw, r.ref)) is not None:
        return Resolution("owned", r.ref, 1.0, lib)
    return r


async def resolve_album(s: AsyncSession, source: str, external_id: str, raw: dict[str, Any]) -> Resolution:
    known = await _remembered(s, source, "album", external_id)
    if known is not None:
        return Resolution(known.match, known.ref, known.score)
    cfg = get_settings().cfg.matching
    r = Resolution("not_found")
    artist, title = raw.get("artist") or "", raw.get("title") or ""
    albums = await lookup.search(f"{artist} {title}".strip(), kinds=("album",), limit=5)
    scored = sorted(((score(artist, title, a.artist, a.title).total, a) for a in albums.albums), key=lambda x: -x[0])
    if scored:
        best, album = scored[0]
        verdict = VERDICT[classify_scores([value for value, a in scored if a.source == album.source], cfg)]
        if verdict != "not_found":
            r = Resolution(verdict, album.ref, round(best, 3))
    await _remember(s, source, "album", external_id, r)
    return r


async def resolve_artist(s: AsyncSession, source: str, external_id: str, raw: dict[str, Any]) -> Resolution:
    known = await _remembered(s, source, "artist", external_id)
    if known is not None:
        return Resolution(known.match, known.ref, known.score)
    r = Resolution("not_found")
    name = raw.get("name") or ""
    if name:
        results = await lookup.search(name, kinds=("artist",), limit=5)
        if artist := lookup.named_artist(name, results.artists):
            r = Resolution("exact", artist.ref, 1.0)
    await _remember(s, source, "artist", external_id, r)
    return r
