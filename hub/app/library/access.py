"""Whose the library's files are («Мои файлы» of a listener, «Все файлы» of an admin).

The library is one for everybody: a song two listeners brought has one file. «Мои файлы» of a listener are the
ones they brought (`created_by` of a track and of an album); an admin sees everything. A track nobody is written on
(its listener is gone) is the admins'. Names and covers are everybody's to put right (library/edits.py).

Deleting a file goes by who keeps the track, not by who brought it (`holders`): a track is kept by those who have it
in a playlist of theirs or in «Любимое». A listener may delete a file nobody but them keeps — whoever brought it —
and may not delete one somebody else keeps, their own bringing or not: it would vanish from that listener's
playlist. An admin may, and is told how many keep it.

An album can hold tracks of several listeners. Each sees their own tracks of it; the album itself — its name, its
picture, merging it — is changed by the one all of its tracks are of, or by an admin."""

from __future__ import annotations

from fastapi import HTTPException
from sqlalchemy import func, or_, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.models import Album, Playlist, PlaylistItem, Track, User


def owns_track(user: User, t: Track) -> bool:
    return user.is_admin or t.created_by == user.id


def albums_of(user: User):
    """The albums `user` sees among their files: made by them, or holding a track of theirs (a clause for a select
    of `Album`; an admin sees all of them)."""
    if user.is_admin:
        return True
    return or_(Album.created_by == user.id, Album.id.in_(select(Track.album_id).where(Track.created_by == user.id)))


def tracks_of(user: User):
    """The tracks `user` sees among their files (a clause for a select of `Track`)."""
    return True if user.is_admin else Track.created_by == user.id


async def _others(s: AsyncSession, user: User, album_id: int) -> int:
    """Tracks of the album that are not `user`'s."""
    return (await s.execute(select(func.count(Track.id)).where(
        Track.album_id == album_id, or_(Track.created_by.is_(None), Track.created_by != user.id)))).scalar_one()


async def _own(s: AsyncSession, user: User, album_id: int) -> int:
    return (await s.execute(select(func.count(Track.id)).where(
        Track.album_id == album_id, Track.created_by == user.id))).scalar_one()


async def sees_album(s: AsyncSession, user: User, a: Album) -> bool:
    return user.is_admin or a.created_by == user.id or await _own(s, user, a.id) > 0


async def owns_album(s: AsyncSession, user: User, a: Album) -> bool:
    """The album itself can be changed by `user`: every track of it is theirs (an empty one: they made it)."""
    if user.is_admin:
        return True
    if await _others(s, user, a.id):
        return False
    return a.created_by == user.id or await _own(s, user, a.id) > 0


async def need_album(s: AsyncSession, user: User, album_id: int) -> Album:
    """The album, for a listener who sees it (404 for anybody else: it is not among their files)."""
    a = await s.get(Album, album_id)
    if a is None or not await sees_album(s, user, a):
        raise HTTPException(404, "Альбом не найден")
    return a


async def need_own_album(s: AsyncSession, user: User, album_id: int) -> Album:
    a = await need_album(s, user, album_id)
    if not await owns_album(s, user, a):
        raise HTTPException(403, "В альбоме есть треки других слушателей — изменить его может администратор")
    return a


KEEPING = ("user", "liked")  # the playlists that keep a track: the ones a listener makes, and «Любимое»


async def holders(s: AsyncSession, track_id: int) -> dict[int, list[Playlist]]:
    """Who keeps the track: listener -> their playlists it is in («Любимое» among them)."""
    rows = (await s.execute(
        select(Playlist).join(PlaylistItem, PlaylistItem.playlist_id == Playlist.id)
        .where(PlaylistItem.track_id == track_id, Playlist.kind.in_(KEEPING)).distinct().order_by(Playlist.id))).scalars()
    out: dict[int, list[Playlist]] = {}
    for p in rows:
        out.setdefault(p.user_id, []).append(p)
    return out


async def keeping(s: AsyncSession, user: User, track_id: int) -> tuple[bool, int, list[Playlist]]:
    """(may `user` delete the track's file, how many other listeners keep it, `user`'s own playlists it is in)."""
    who = await holders(s, track_id)
    others = len([u for u in who if u != user.id])
    return user.is_admin or not others, others, who.get(user.id, [])


async def need_deletable_track(s: AsyncSession, user: User, track_id: int) -> Track:
    t = await s.get(Track, track_id)
    if t is None:
        raise HTTPException(404, "Трек не найден")
    may, others, _ = await keeping(s, user, track_id)
    if not may:
        raise HTTPException(409, f"Трек есть ещё у {others} — удалить файл нельзя: он пропал бы из их плейлистов")
    return t


async def need_own_track(s: AsyncSession, user: User, track_id: int) -> Track:
    t = await s.get(Track, track_id)
    if t is None or not owns_track(user, t):
        raise HTTPException(404, "Трек не найден")
    return t
