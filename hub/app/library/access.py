"""Whose the library's files are («Мои файлы» of a listener, «Все файлы» of an admin).

The library is one for everybody: a song two listeners brought has one file. But its files are looked after by who
brought them (`created_by` of a track and of an album) and by the admins: a listener sees, renames and deletes only
what is their own, an admin — everything. A track nobody is written on (its listener is gone) is the admins'.

An album can hold tracks of several listeners. Each sees their own tracks of it; the album itself — its name, its
picture, merging it — is changed by the one all of its tracks are of, or by an admin."""

from __future__ import annotations

from fastapi import HTTPException
from sqlalchemy import func, or_, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.models import Album, Track, User


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


async def need_own_track(s: AsyncSession, user: User, track_id: int) -> Track:
    t = await s.get(Track, track_id)
    if t is None or not owns_track(user, t):
        raise HTTPException(404, "Трек не найден")
    return t
