"""«Релизы»: what the artists a user follows have put out lately.

Following an artist is the ♥ on the artist's page (a typed favorite, app/favorites.py) — a liked track does not make
its artist followed. Twice a day the Hub reads each followed artist's releases in the catalog and keeps the ones of
the last `WINDOW_DAYS` it has not kept yet (`artist_releases`); the feed shows them newest first, the ones the user
has not seen marked. An artist that is no longer followed leaves the feed, its rows stay.
"""

from __future__ import annotations

import asyncio
import logging
from datetime import date, datetime, timedelta
from typing import Any

from sqlalchemy import func, select, update
from sqlalchemy.ext.asyncio import AsyncSession

from app.catalog.models import CatalogAlbum, split_ref
from app.db import session_scope
from app.events import bus
from app.models import ArtistRelease, ArtistReleaseCheck, User, utcnow
from app.services import svc

log = logging.getLogger(__name__)

WINDOW_DAYS = 90  # a release older than this is not news
CHECK_EVERY = timedelta(hours=12)
LOOP_S = 3600
FEED_LIMIT = 300
TYPES = ("album", "ep", "single", "compilation")

_lock = asyncio.Lock()  # one refresh at a time: the feed's own lookup and the loop do not add a release twice


async def albums_of(ref: str, name: str) -> list[CatalogAlbum]:
    src, id_ = split_ref(ref)
    if src != "dz" or svc.deezer is None:
        return []
    return await svc.deezer.artist_albums_now(id_, name)


async def followed(s: AsyncSession, user_id: int) -> dict[str, str]:
    """catalog ref -> name of the artists the user follows. One liked in the library (no catalog ref) is looked up
    in the catalog by its name."""
    from app.favorites import snapshot
    from app.mixes.build import artist_by_name

    out: dict[str, str] = {}
    for st in (await snapshot(s, user_id)).values():
        if st.target_type != "artist" or not st.liked:
            continue
        name = str((st.data or {}).get("name") or "")
        ref = st.ref if st.ref and st.ref.startswith("dz:") else None
        if ref is None and name:
            try:
                ref = await artist_by_name(name)
            except Exception as e:  # the catalog is away: this artist waits for the next look
                log.info("releases: artist %r not found in the catalog: %r", name, e)
        if ref:
            out.setdefault(ref, name)
    return out


async def refresh(user_id: int, *, only_new: bool = False) -> int:
    """Look up the releases of the followed artists that are due (`only_new`: only the ones never looked up — an
    artist followed a minute ago). Returns how many releases were added."""
    async with _lock:
        async with session_scope() as s:
            artists = await followed(s, user_id)
            checks = {c.artist_ref: c.checked_at for c in (await s.execute(
                select(ArtistReleaseCheck).where(ArtistReleaseCheck.user_id == user_id))).scalars()}
            known = set((await s.execute(
                select(ArtistRelease.album_ref).where(ArtistRelease.user_id == user_id))).scalars())
        now = utcnow()
        due = [ref for ref in artists
               if ref not in checks or (not only_new and now - checks[ref] >= CHECK_EVERY)]
        found: dict[str, list[CatalogAlbum]] = {}
        for ref in due:  # the network, with no transaction open
            try:
                found[ref] = await albums_of(ref, artists[ref])
            except Exception as e:
                log.info("releases: %s not read: %r", ref, e)
        if not found:
            return 0
        today = date.today()
        since = today - timedelta(days=WINDOW_DAYS)
        added = 0
        async with session_scope() as s:
            for ref, albums in found.items():
                for a in albums:
                    if a.ref in known or a.release_date is None or not (since <= a.release_date <= today):
                        continue
                    known.add(a.ref)
                    s.add(ArtistRelease(
                        user_id=user_id, artist_ref=ref, artist=a.artist or artists[ref], album_ref=a.ref,
                        title=a.title, record_type=a.record_type, release_date=a.release_date,
                        cover_url=a.cover_small_url or a.cover_url, nb_tracks=a.nb_tracks, found_at=now))
                    added += 1
                if ref in checks:
                    await s.execute(update(ArtistReleaseCheck).where(
                        ArtistReleaseCheck.user_id == user_id, ArtistReleaseCheck.artist_ref == ref).values(checked_at=now))
                else:
                    s.add(ArtistReleaseCheck(user_id=user_id, artist_ref=ref, checked_at=now))
        if added:
            bus.publish(user_id, "library", {"kind": "releases", "added": added})
        return added


async def unseen(s: AsyncSession, user_id: int, artists: dict[str, str] | None = None) -> int:
    artists = await followed(s, user_id) if artists is None else artists
    if not artists:
        return 0
    return int((await s.execute(select(func.count(ArtistRelease.id)).where(
        ArtistRelease.user_id == user_id, ArtistRelease.seen_at.is_(None),
        ArtistRelease.artist_ref.in_(artists)))).scalar_one())


async def feed(s: AsyncSession, user_id: int) -> dict[str, Any]:
    artists = await followed(s, user_id)
    rows = list((await s.execute(
        select(ArtistRelease).where(ArtistRelease.user_id == user_id, ArtistRelease.artist_ref.in_(artists))
        .order_by(ArtistRelease.release_date.desc(), ArtistRelease.id.desc()).limit(FEED_LIMIT))).scalars()) if artists else []
    return {
        "artists": len(artists),
        "unseen": sum(1 for r in rows if r.seen_at is None),
        "items": [{
            "album_ref": r.album_ref, "title": r.title, "artist": r.artist, "artist_ref": r.artist_ref,
            "record_type": r.record_type, "release_date": r.release_date.isoformat(), "cover_url": r.cover_url,
            "nb_tracks": r.nb_tracks, "new": r.seen_at is None,
        } for r in rows],
    }


async def mark_seen(s: AsyncSession, user_id: int, now: datetime | None = None) -> None:
    await s.execute(update(ArtistRelease).where(ArtistRelease.user_id == user_id, ArtistRelease.seen_at.is_(None))
                    .values(seen_at=now or utcnow()))


async def loop() -> None:
    await asyncio.sleep(300)
    while True:
        try:
            async with session_scope() as s:
                users = list((await s.execute(select(User.id).order_by(User.id))).scalars())
            for user_id in users:
                added = await refresh(user_id)
                if added:
                    log.info("releases: %s new for user %s", added, user_id)
        except Exception:
            log.exception("releases check failed")
        await asyncio.sleep(LOOP_S)
