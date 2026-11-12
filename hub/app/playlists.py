"""Playlists (ARCHITECTURE.md): the Hub's own. A playlist holds tracks — with a file or without one, each in its
place — and keeps when each was added. «Любимое» is the playlist of `kind` "liked", one per user.

Everything here is the Hub's database only. What Navidrome and the apps that talk to it see is a mirror made
elsewhere; nothing below waits for it.
"""

from __future__ import annotations

from collections.abc import Iterable
from datetime import datetime

from sqlalchemy import delete, func, select
from sqlalchemy.dialects.sqlite import insert
from sqlalchemy.ext.asyncio import AsyncSession

from app.models import Playlist, PlaylistItem, Track, TrackFile, utcnow

LIKED = "liked"
USER = "user"
LIKED_NAME = "Любимое"


async def create(s: AsyncSession, user_id: int, name: str, *, kind: str = USER) -> Playlist:
    p = Playlist(user_id=user_id, kind=kind, name=" ".join(name.split())[:200])
    s.add(p)
    await s.flush()
    return p


async def liked(s: AsyncSession, user_id: int) -> Playlist:
    """«Любимое» of the user (made on the first call)."""
    p = (await s.execute(select(Playlist).where(Playlist.user_id == user_id, Playlist.kind == LIKED)
                         .order_by(Playlist.id).limit(1))).scalar_one_or_none()
    if p is None:
        # two requests at once (a page asks for the likes twice as it opens) must not make two: the database lets
        # one in, and both read it
        await s.execute(insert(Playlist).values(user_id=user_id, kind=LIKED, name=LIKED_NAME).on_conflict_do_nothing())
        p = (await s.execute(select(Playlist).where(Playlist.user_id == user_id, Playlist.kind == LIKED)
                             .order_by(Playlist.id).limit(1))).scalar_one()
    return p


async def own(s: AsyncSession, user_id: int, playlist_id: int) -> Playlist | None:
    p = await s.get(Playlist, playlist_id)
    return p if p is not None and p.user_id == user_id else None


async def of_user(s: AsyncSession, user_id: int, *, kinds: Iterable[str] = (USER,)) -> list[Playlist]:
    """The user's playlists, the one changed last first."""
    return list((await s.execute(select(Playlist).where(Playlist.user_id == user_id, Playlist.kind.in_(list(kinds)))
                                 .order_by(Playlist.updated_at.desc(), Playlist.id.desc()))).scalars())


async def counts(s: AsyncSession, playlist_ids: Iterable[int]) -> dict[int, tuple[int, int, int]]:
    """playlist id -> (tracks, of them with a file, seconds)."""
    ids = list(playlist_ids)
    if not ids:
        return {}
    filed = select(TrackFile.track_id).distinct().subquery()
    rows = (await s.execute(
        select(PlaylistItem.playlist_id, func.count(PlaylistItem.id), func.count(filed.c.track_id),
               func.coalesce(func.sum(Track.duration_s), 0))
        .join(Track, Track.id == PlaylistItem.track_id)
        .outerjoin(filed, filed.c.track_id == PlaylistItem.track_id)
        .where(PlaylistItem.playlist_id.in_(ids)).group_by(PlaylistItem.playlist_id))).all()
    out = {pid: (0, 0, 0) for pid in ids}
    out.update({pid: (n, with_file, int(seconds)) for pid, n, with_file, seconds in rows})
    return out


async def items(s: AsyncSession, playlist_id: int) -> list[tuple[PlaylistItem, Track]]:
    """The playlist in its order."""
    return [(i, t) for i, t in (await s.execute(
        select(PlaylistItem, Track).join(Track, Track.id == PlaylistItem.track_id)
        .where(PlaylistItem.playlist_id == playlist_id).order_by(PlaylistItem.position, PlaylistItem.id))).all()]


async def track_ids(s: AsyncSession, playlist_id: int) -> list[int]:
    return list((await s.execute(select(PlaylistItem.track_id).where(PlaylistItem.playlist_id == playlist_id)
                                 .order_by(PlaylistItem.position, PlaylistItem.id))).scalars())


async def add(s: AsyncSession, p: Playlist, tracks: Iterable[int], *, at: datetime | None = None,
              again: bool = False, first: bool = False) -> list[int]:
    """Put tracks into the playlist, in the order given: after the last one, or — `first` — before the first (a
    like goes on top of «Любимое»). A track that is in it already is left where it is unless `again`. `at`: when
    they were added, if not now (an import brings the dates of the service). Returns the tracks that went in."""
    want = list(dict.fromkeys(tracks)) if not again else list(tracks)
    if not again:
        there = set(await track_ids(s, p.id))
        want = [t for t in want if t not in there]
    if not want:
        return []
    edge = (await s.execute(select(func.min(PlaylistItem.position) if first else func.max(PlaylistItem.position))
                            .where(PlaylistItem.playlist_id == p.id))).scalar_one_or_none()
    start = (edge - len(want)) if first and edge is not None else (edge + 1) if edge is not None else 0
    now = at or utcnow()
    for k, track_id in enumerate(want):
        s.add(PlaylistItem(playlist_id=p.id, track_id=track_id, position=start + k, added_at=now))
    p.updated_at = utcnow()
    await s.flush()
    return want


async def remove_items(s: AsyncSession, p: Playlist, item_ids: Iterable[int]) -> int:
    """Take places out of the playlist (a track that stands in it twice keeps the other place)."""
    ids = list(item_ids)
    if not ids:
        return 0
    res = await s.execute(delete(PlaylistItem).where(PlaylistItem.playlist_id == p.id, PlaylistItem.id.in_(ids)))
    if res.rowcount:
        p.updated_at = utcnow()
    return res.rowcount or 0


async def remove_tracks(s: AsyncSession, p: Playlist, tracks: Iterable[int]) -> int:
    """Take tracks out of the playlist, every place they have in it."""
    ids = list(tracks)
    if not ids:
        return 0
    res = await s.execute(delete(PlaylistItem).where(PlaylistItem.playlist_id == p.id, PlaylistItem.track_id.in_(ids)))
    if res.rowcount:
        p.updated_at = utcnow()
    return res.rowcount or 0


async def reorder(s: AsyncSession, p: Playlist, item_ids: list[int]) -> None:
    """The playlist in a new order: the items named first, in that order, then the rest as they stood."""
    rows = {i.id: i for i, _ in await items(s, p.id)}
    order = [i for i in dict.fromkeys(item_ids) if i in rows] + [i for i in rows if i not in set(item_ids)]
    for position, item_id in enumerate(order):
        rows[item_id].position = position
    p.updated_at = utcnow()
    await s.flush()


async def having(s: AsyncSession, user_id: int, tracks: Iterable[int], *,
                 kinds: Iterable[str] = (USER, LIKED)) -> dict[int, set[int]]:
    """playlist id -> which of these tracks are in it, for the user's playlists that have any of them."""
    ids = list(dict.fromkeys(tracks))
    out: dict[int, set[int]] = {}
    for k in range(0, len(ids), 500):
        for pid, tid in (await s.execute(
                select(PlaylistItem.playlist_id, PlaylistItem.track_id)
                .join(Playlist, Playlist.id == PlaylistItem.playlist_id)
                .where(Playlist.user_id == user_id, Playlist.kind.in_(list(kinds)),
                       PlaylistItem.track_id.in_(ids[k:k + 500])))).all():
            out.setdefault(pid, set()).add(tid)
    return out


async def kept(s: AsyncSession, user_id: int) -> tuple[set[int], set[int]]:
    """(tracks in «Любимое», tracks in some playlist made by hand or by an import) — what the «+ / ♥ / ✓» of a
    track row goes by."""
    rows = (await s.execute(
        select(Playlist.kind, PlaylistItem.track_id).join(Playlist, Playlist.id == PlaylistItem.playlist_id)
        .where(Playlist.user_id == user_id, Playlist.kind.in_((USER, LIKED))).distinct())).all()
    return {t for kind, t in rows if kind == LIKED}, {t for kind, t in rows if kind == USER}
