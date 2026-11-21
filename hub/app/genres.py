"""Enrich known tracks in small batches without delaying playback or opening a list."""

from __future__ import annotations

import asyncio
import logging
from datetime import timedelta

from sqlalchemy import and_, or_, select
from sqlalchemy.dialects.sqlite import insert

from app import tags
from app.catalog import release_matches
from app.catalog.models import CatalogAlbum
from app.catalog.identity import matched_track
from app.catalog.spotify import ID
from app.db import session_scope
from app.events import bus
from app.mixes.sources import lastfm_configured, lastfm_track_tags
from app.models import Album, Track, TrackLink, utcnow
from app.services import svc

log = logging.getLogger(__name__)
BATCH_SIZE = 16
REFRESH = timedelta(days=30)
RETRY = timedelta(days=1)
# Last.fm's freeform tags include moods, artist names, years and personal notes.
GENRE_WORDS = set("""
    pop|dance|electronic|electronica|electro|edm|rock|alternative|alternative rock|indie|indie rock|indie pop|
    hip hop|hip-hop|rap|trap|r&b|soul|funk|disco|jazz|blues|metal|heavy metal|punk|folk|country|classical|
    ambient|house|techno|trance|drum and bass|dubstep|breakbeat|hardcore|trip hop|downtempo|experimental|
    synthpop|synth-pop|new wave|post-punk|shoegaze|grunge|k-pop|j-pop|j-rock|vocaloid|city pop|chiptune|
    reggae|ska|latin|bossa nova|samba|reggaeton|afrobeats|phonk|hardstyle|hyperpop|lo-fi|lofi|noise|gothic rock
""".replace("\n", "").strip().split("|"))
GENRE_WORDS = {word.strip() for word in GENRE_WORDS}


def _spotify_key(track: Track, linked: str | None) -> str | None:
    ref = track.ref or ""
    candidates = [ref[3:] if ref.startswith("sp:") else "", linked, (track.catalog or {}).get("spotify_id")]
    return next((key for key in candidates if key and ID.fullmatch(key)), None)


def _album_ref(track: Track, deezer_id: str | None) -> str | None:
    return (track.catalog or {}).get("album_ref") or (f"dz:{deezer_id}" if deezer_id else None)


def _release_genres(albums: list[CatalogAlbum]) -> dict[str, list[str]]:
    return {tags.RELEASE_GENRE_SOURCES[a.source]: tags.genre_names([a.genre] if a.genre else [])
            for a in albums if a.source in tags.RELEASE_GENRE_SOURCES}


async def enrich_batch() -> int:
    now = utcnow()
    due = or_(Track.genres_checked_at.is_(None), Track.genres_checked_at <= now - REFRESH)
    link = and_(TrackLink.track_id == Track.id, TrackLink.service == "spotify")
    async with session_scope() as s:
        rows = (await s.execute(
            select(Track, TrackLink.external_id, Album.deezer_album_id)
            .outerjoin(TrackLink, link).outerjoin(Album, Album.id == Track.album_id)
            .where(due, Track.artist != "", Track.title != "")
            .order_by(Track.genres_checked_at.asc().nulls_first(),
                      or_(TrackLink.external_id.is_not(None), Track.ref.like("sp:%")).desc(), Track.id.desc())
            .limit(BATCH_SIZE)
        )).all()
        refs = {ref for track, _, dz in rows if (ref := _album_ref(track, dz))}
        groups = await release_matches.groups_for(s, refs)
    if not rows:
        return 0

    gate = asyncio.Semaphore(3)
    failures: set[int] = set()

    async def spotify_key(track: Track, linked: str | None) -> str | None:
        if (key := _spotify_key(track, linked)) or svc.spotify is None:
            return key
        if not track.isrc and not (track.album and track.duration_s):
            return None
        try:
            async with gate, asyncio.timeout(10):
                result = await svc.spotify.search(f"{track.artist} {track.title}", kinds=("track",), limit=12)
            match = matched_track(track, result.tracks)
            return match.id if match else None
        except Exception:
            failures.add(track.id)
            return None

    async def release_genres(ref: str) -> dict[str, list[str]] | None:
        if group := groups.get(ref):
            return _release_genres(group)
        if ref.partition(":")[0] not in tags.RELEASE_GENRE_SOURCES:
            return {}
        from app.catalog.models import CatalogNotFound

        try:
            async with gate, asyncio.timeout(10):
                return _release_genres([await svc.album(ref)])
        except CatalogNotFound:
            return {}
        except Exception:
            return None

    async def listener_tags(track: Track) -> list[str] | None:
        if not await lastfm_configured():
            return None
        try:
            async with gate, asyncio.timeout(10):
                return await lastfm_track_tags(track.artist, track.title)
        except Exception:
            failures.add(track.id)
            return None

    refs = sorted(refs)
    keys, release_rows, listener_rows = await asyncio.gather(
        asyncio.gather(*(spotify_key(track, linked) for track, linked, _ in rows)),
        asyncio.gather(*(release_genres(ref) for ref in refs)),
        asyncio.gather(*(listener_tags(track) for track, _, _ in rows)),
    )
    releases = dict(zip(refs, release_rows, strict=True))
    spotify: dict[str, list[str]] = {}
    if svc.spotify is not None and any(keys):
        try:
            spotify = await svc.spotify.track_genres([key for key in keys if key])
        except Exception as error:
            log.info("Spotify guest genres unavailable: %s", type(error).__name__)

    changed = []
    async with session_scope() as s:
        ids = [track.id for track, _, _ in rows]
        current = {track.id: track for track in await s.scalars(select(Track).where(Track.id.in_(ids)))}
        for (snapshot, linked, dz), key, listener in zip(rows, keys, listener_rows, strict=True):
            track = current.get(snapshot.id)
            if track is None or (track.artist, track.title, track.album_id, track.ref, track.catalog) != (
                snapshot.artist, snapshot.title, snapshot.album_id, snapshot.ref, snapshot.catalog
            ):
                continue
            source_values = dict(track.genre_sources or {})
            release = releases.get(_album_ref(snapshot, dz), {})
            updates = dict(release or {})
            if key in spotify:
                updates["spotify"] = tags.genre_names(spotify[key])
            if listener is not None:
                known = tags.genre_evidence({**source_values, **updates})
                updates["lastfm"] = tags.genre_names(
                    [name for name in listener if name.casefold() in GENRE_WORDS or name.casefold() in known]
                )
            source_values = {source: names for source, names in {**source_values, **updates}.items() if names}
            if source_values != (track.genre_sources or {}):
                track.genre_sources = source_values
                changed.append(track.id)
            retry = track.id in failures or (key is not None and key not in spotify and svc.spotify is not None) or release is None
            track.genres_checked_at = now - REFRESH + RETRY if retry else now
            if key and not linked and not _spotify_key(snapshot, None):
                await s.execute(insert(TrackLink).values(track_id=track.id, service="spotify", external_id=key)
                                .on_conflict_do_nothing())
        if changed:
            await s.flush()
            await tags.sync(s, changed)
    if changed:
        bus.broadcast("genres", {})
    return len(rows)


async def loop() -> None:
    await asyncio.sleep(10)
    while True:
        delay = 60
        try:
            if await enrich_batch():
                delay = 10
        except Exception:
            log.exception("genre enrichment failed")
        await asyncio.sleep(delay)
