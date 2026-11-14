"""«Корзина»: a deleted track can be brought back while its file is still kept.

Deleting a track moves its files to `.trash/<date>/…` (stirred out after `trash_days`) and deletes the track with
everything that hangs on it. So that it can come back as it was, what is deleted is written down first
(`trashed_tracks`): the track itself, its files and where each went, its album when the album goes with its last
track, the places it had in playlists («Любимое» among them), its tags, its own cover. Bringing it back puts the
files where they were and makes all of that again — under the track's old id while nothing has taken it, so what
names the track by its id (the listens, the downloads) is the track's again. Its lyrics are not kept: they are
found again as for any track. A row whose files have been stirred out is dropped with them.
"""

from __future__ import annotations

import asyncio
from datetime import date, datetime, timedelta
from pathlib import Path
from typing import Any

from sqlalchemy import Date, DateTime, delete, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.config import get_settings
from app.library.importer import _move, library_abs, library_lock
from app.library.layout import unique_path
from app.models import (
    Album,
    Playlist,
    PlaylistItem,
    Tag,
    Track,
    TrackCover,
    TrackFile,
    TrackTag,
    TrashedTrack,
    User,
    utcnow,
)


def _row(obj: Any) -> dict[str, Any]:
    """A row as JSON: its table's columns, dates as text."""
    out = {}
    for c in obj.__table__.columns:
        v = getattr(obj, c.name)
        out[c.name] = v.isoformat() if isinstance(v, (date, datetime)) else v
    return out


def _back(model: Any, data: dict[str, Any]) -> dict[str, Any]:
    """The columns of `model` out of what `_row` made, dates read back; a column the table has got since is left to
    its default, one it has lost is dropped."""
    out = {}
    for c in model.__table__.columns:
        if c.name not in data:
            continue
        v = data[c.name]
        if v is not None and isinstance(c.type, DateTime):
            v = datetime.fromisoformat(v)
        elif v is not None and isinstance(c.type, Date):
            v = date.fromisoformat(v)
        out[c.name] = v
    return out


async def remember(s: AsyncSession, track: Track, moved: dict[int, str], user_id: int | None,
                   album: Album | None) -> None:
    """Write down a track that is being deleted. `moved`: file id -> where its file went (absolute); `album`: its
    album, when the album goes with it."""
    files = [dict(_row(f), trash_path=moved.get(f.id)) for f in (await s.execute(
        select(TrackFile).where(TrackFile.track_id == track.id))).scalars()]
    places = [{"playlist_id": i.playlist_id, "position": i.position, "added_at": i.added_at.isoformat()}
              for i in (await s.execute(select(PlaylistItem).where(PlaylistItem.track_id == track.id))).scalars()]
    tags = [{"tag_id": t.tag_id, "auto": t.auto}
            for t in (await s.execute(select(TrackTag).where(TrackTag.track_id == track.id))).scalars()]
    cover = (await s.execute(select(TrackCover.image).where(TrackCover.track_id == track.id))).scalar_one_or_none()
    s.add(TrashedTrack(
        track_id=track.id, title=track.title, artist=track.artist, album=track.album, deleted_by=user_id,
        data_json={"track": _row(track), "files": files, "playlists": places, "tags": tags,
                   "album": _row(album) if album is not None else None},
        cover=cover))


async def listed(s: AsyncSession) -> list[dict[str, Any]]:
    days = get_settings().cfg.library.trash_days
    rows = (await s.execute(select(TrashedTrack, User.nd_username).join(User, User.id == TrashedTrack.deleted_by, isouter=True)
                            .order_by(TrashedTrack.id.desc()))).all()
    now = utcnow()
    return [{"id": t.id, "title": t.title, "artist": t.artist, "album": t.album, "deleted_by": who,
             "deleted_at": t.deleted_at, "days_left": max(0, days - (now - t.deleted_at).days),
             "playlists": len((t.data_json or {}).get("playlists") or [])} for t, who in rows]


async def restore(s: AsyncSession, row: TrashedTrack) -> Track:
    """Bring the track back: its files to where they were, and what hung on it. Raises ValueError when none of its
    files is kept any more."""
    data = row.data_json or {}
    kept = [f for f in data.get("files") or [] if f.get("trash_path") and Path(f["trash_path"]).is_file()]
    if not kept:
        raise ValueError("Файл уже стёрт из корзины — вернуть нечего")
    async with library_lock:
        tr = _back(Track, data["track"])
        album = data.get("album")
        if tr.get("album_id") and await s.get(Album, tr["album_id"]) is None:
            if album:
                s.add(Album(**_back(Album, album)))  # it went with its last track: it comes back with it
            else:
                tr["album_id"] = None
        if await s.get(Track, tr["id"]) is not None:  # the id was taken since: a track of its own, then
            tr.pop("id")
        if tr.get("ref") and (await s.execute(select(Track.id).where(Track.ref == tr["ref"]))).first():
            tr["ref"] = None  # the song was met anew meanwhile; this is the file's track
        track = Track(**tr)
        s.add(track)
        await s.flush()
        for f in kept:
            dest = library_abs(f["path"])
            if dest.exists():
                dest = unique_path(dest.parent, dest.name)
            await asyncio.to_thread(_move, Path(f["trash_path"]), dest)
            cols = _back(TrackFile, f)
            cols.pop("id", None)
            cols.update(track_id=track.id, path=dest.relative_to(library_abs("")).as_posix(), nd_song_id=None)
            s.add(TrackFile(**cols))
        lists = set((await s.execute(select(Playlist.id).where(
            Playlist.id.in_({p["playlist_id"] for p in data.get("playlists") or []})))).scalars())
        for p in data.get("playlists") or []:
            if p["playlist_id"] in lists:
                s.add(PlaylistItem(playlist_id=p["playlist_id"], track_id=track.id, position=p["position"],
                                   added_at=datetime.fromisoformat(p["added_at"])))
        tags = set((await s.execute(select(Tag.id).where(Tag.id.in_({t["tag_id"] for t in data.get("tags") or []})))).scalars())
        for t in data.get("tags") or []:
            if t["tag_id"] in tags:
                s.add(TrackTag(track_id=track.id, tag_id=t["tag_id"], auto=bool(t.get("auto"))))
        if row.cover and track.cover_key:
            s.add(TrackCover(track_id=track.id, image=row.cover))
        elif track.cover_key:
            track.cover_key = None
        await s.delete(row)
        await s.flush()
    return track


async def purge(s: AsyncSession, days: int) -> int:
    """Drop what was written down of tracks whose files have been stirred out of the trash."""
    res = await s.execute(delete(TrashedTrack).where(TrashedTrack.deleted_at < utcnow() - timedelta(days=days)))
    return res.rowcount or 0
