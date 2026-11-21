"""Identify recommendation tracks in the library or one of the Hub's catalogs."""

from __future__ import annotations

import asyncio
import hashlib
import logging

from sqlalchemy.exc import IntegrityError

from app.catalog import lookup
from app.catalog.cache import cache_get, cache_put, cache_set
from app.catalog.models import CatalogTrack, split_ref
from app.db import session_scope
from app.imports.resolve import Resolution, _remember, _remembered
from app.library.owned import find_owned
from app.mixes.sources import Hit
from app.providers.base import TrackQuery
from app.services import svc

log = logging.getLogger(__name__)

TRACK_TTL_S = 30 * 86400
PARALLEL = 4


def library_track(t) -> CatalogTrack:
    """Represent a library song in a list of recommendations."""
    if t.ref and t.ref.startswith(("dz:", "sp:", "ym:")):
        source, id_ = split_ref(t.ref)
        return CatalogTrack(ref=t.ref, source=source, id=id_, title=t.title,
                            artist=t.artist, artists=[t.artist], album_title=t.album, duration_s=t.duration_s,
                            isrc=t.isrc, explicit=t.explicit, spotify_id=id_ if source == "sp" else None)
    return CatalogTrack(ref=f"nd:{t.nd_song_id}", source="nd", id=str(t.nd_song_id), title=t.title, artist=t.artist,
                        artists=[t.artist], album_title=t.album, duration_s=t.duration_s, isrc=t.isrc,
                        explicit=t.explicit)


def external_id(hit: Hit) -> str:
    if hit.source_id:
        return hit.source_id[:128]
    raw = f"{hit.artist}|{hit.title}".casefold()
    return raw if len(raw) <= 128 else hashlib.sha1(raw.encode()).hexdigest()


async def remember_track(t: CatalogTrack, s=None) -> None:
    """Keep the catalog's record of a track (`s`: within this transaction)."""
    if s is not None:
        await cache_put(s, f"mix:track:{t.ref}", t.model_dump(mode="json"), TRACK_TTL_S)
    else:
        await cache_set(f"mix:track:{t.ref}", t.model_dump(mode="json"), TRACK_TTL_S)


async def remembered_track(ref: str) -> CatalogTrack | None:
    """The catalog record of a track as the Hub has kept it (`remember_track`); the catalog is not asked."""
    hit = await cache_get(f"mix:track:{ref}")
    return CatalogTrack.model_validate(hit) if hit is not None else None


async def catalog_track(ref: str) -> CatalogTrack | None:
    """The catalog's record of a track (a source's match remembered, else read from the catalog it is of)."""
    if (hit := await remembered_track(ref)) is not None:
        return hit
    if split_ref(ref)[0] not in ("dz", "ym", "sp"):
        return None
    try:
        t = await svc.track(ref)
    except Exception as e:
        log.info("mix: catalog track %s: %r", ref, e)
        from app.library.tracks import by_ref

        async with session_scope() as s:
            own = await by_ref(s, ref)
            if not own or not own.artist or not own.title or own.title == ref:
                return None
            saved = own.catalog or {}
            source, ident = split_ref(ref)
            return CatalogTrack.model_validate({
                **saved, "ref": ref, "source": source, "id": ident,
                "title": own.title, "artist": own.artist, "album_title": own.album,
                "duration_s": own.duration_s, "isrc": own.isrc, "explicit": own.explicit,
            })
    await remember_track(t)
    return t


async def _search(hit: Hit) -> Resolution:
    match = await lookup.match_track(TrackQuery(artist=hit.artist, title=hit.title, duration_s=hit.duration_s))
    if match is None or match.verdict != "auto":
        return Resolution("not_found", None, round(match.score, 3) if match else None)
    await remember_track(match.track)
    return Resolution("exact", match.track.ref, round(match.score, 3))


async def resolve_hit(source: str, hit: Hit) -> CatalogTrack | None:
    async with session_scope() as s:
        lib = await find_owned(s, artist=hit.artist, title=hit.title, duration_s=hit.duration_s)
        if lib is not None and lib.nd_song_id:
            return library_track(lib)
        known = await _remembered(s, source, "track", external_id(hit))
    if known is None or (known.ref and not lookup.source_available(known.ref)):
        r = await _search(hit)
        try:
            async with session_scope() as s:
                await _remember(s, source, "track", external_id(hit), r)
        except IntegrityError:
            pass  # another build remembered the same id meanwhile
    else:
        r = Resolution(known.match, known.ref, known.score)
    if r.match not in ("exact",) or not r.ref:
        return None
    return await catalog_track(r.ref)


async def resolve_hits(source: str, hits: list[Hit]) -> list[CatalogTrack | None]:
    """Each hit's catalog track (None: not found or not sure), in order. A catalog error fails one hit, not all.
    A source id given twice (two seeds' lists share a track) is looked up once."""
    sem = asyncio.Semaphore(PARALLEL)

    async def one(h: Hit) -> CatalogTrack | None:
        if not h.artist or not h.title:
            return None
        async with sem:
            try:
                return await resolve_hit(source, h)
            except Exception as e:
                log.info("mix: resolve %s - %s (%s): %r", h.artist, h.title, source, e)
                return None

    unique = list({external_id(h): h for h in hits}.values())
    found = dict(zip((external_id(h) for h in unique), await asyncio.gather(*(one(h) for h in unique)), strict=True))
    return [found[external_id(h)] for h in hits]
