"""What listeners put right: a track's name and artists, its own cover, an album's cover — for everyone here.

A name is a correction, not a taste («(Remastered 2019)» gone, a typo, a transliteration): the track has one name
for the whole server, written into its file, so the search, a shared link, the lyrics and an app that talks to
Navidrome all know it by the same. Anybody signed in may change it. So that this does not become a mess, every change
is written down (`edits`: who, when, what it was and what it is) — the last one is shown beside the name — and what
the catalog calls the track is kept with it (`Track.catalog`), to go back to.

A track renamed by hand says so (`renamed_at`): the lists the catalog answers (a search, an album's page) then show
the Hub's name of it, not the catalog's. A track may have a cover of its own (`track_covers`) where the album's is
not its — a single put into a collection, a file that came without a picture; it is in the file too.
"""

from __future__ import annotations

import asyncio
import secrets
from typing import Any

from sqlalchemy import delete, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.library import editor, tagger
from app.library.importer import library_abs
from app.library.tracks import current_file
from app.models import Album, Edit, Track, TrackCover, User, utcnow

NAMES = ("title", "artist")


def _journal(s: AsyncSession, user_id: int, kind: str, target_id: int, changes: dict[str, list[Any]]) -> None:
    if changes:
        s.add(Edit(target_type=kind, target_id=target_id, user_id=user_id, changes_json=changes))


async def update_track(s: AsyncSession, user: User, track: Track, **fields: Any) -> Track:
    """Change the track's fields (the names, the number, the genre…) and write it down."""
    fields = {k: (v.strip() if isinstance(v, str) else v) for k, v in fields.items() if v is not None}
    changes = {k: [getattr(track, k), v] for k, v in fields.items() if getattr(track, k) != v}
    if not changes:
        return track
    if any(k in NAMES for k in changes) and not (fields.get("title", track.title) and fields.get("artist", track.artist)):
        raise ValueError("У трека должны быть название и исполнитель")
    await editor.update_track(s, track, **{k: v[1] for k, v in changes.items()})
    if any(k in NAMES for k in changes):
        track.renamed_at = utcnow()
    _journal(s, user.id, "track", track.id, changes)
    await s.flush()
    return track


async def update_album(s: AsyncSession, user: User, album: Album, **fields: Any) -> Album:
    fields = {k: (v.strip() if isinstance(v, str) else v) for k, v in fields.items() if v is not None}
    changes = {k: [getattr(album, k), v] for k, v in fields.items() if getattr(album, k) != v}
    if changes:
        await editor.update_album(s, album, **{k: v[1] for k, v in changes.items()})
        _journal(s, user.id, "album", album.id, changes)
        await s.flush()
    return album


async def set_album_cover(s: AsyncSession, user: User, album: Album, image: bytes) -> Album:
    await editor.set_album_cover(s, album, image)
    _journal(s, user.id, "album", album.id, {"cover": [None, "new"]})
    await s.flush()
    return album


async def track_cover(s: AsyncSession, track_id: int) -> bytes | None:
    return (await s.execute(select(TrackCover.image).where(TrackCover.track_id == track_id))).scalar_one_or_none()


async def set_track_cover(s: AsyncSession, user: User, track: Track, image: bytes | None) -> Track:
    """The track's own cover (None: no cover of its own any more — the album's again). It goes into the file too."""
    cover = await asyncio.to_thread(tagger.prepare_cover, image) if image is not None else None
    await s.execute(delete(TrackCover).where(TrackCover.track_id == track.id))
    if cover is not None:
        track.cover_key = secrets.token_hex(4)
        s.add(TrackCover(track_id=track.id, image=cover))
    else:
        track.cover_key = None
    file = await current_file(s, track.id)
    if file is not None:
        p = library_abs(file.path)
        if p.is_file():
            shown = cover
            if shown is None and track.album_id:  # back to the album's picture
                album = await s.get(Album, track.album_id)
                shown = await editor._album_cover(album) if album is not None else None
            async with editor.library_lock:
                await asyncio.to_thread(tagger.update_tags, p, cover=shown)
    _journal(s, user.id, "track", track.id, {"cover": [None, "new" if cover is not None else "album"]})
    await s.flush()
    return track


async def history(s: AsyncSession, track_id: int, limit: int = 30) -> list[dict[str, Any]]:
    """What was changed of the track, the latest first: who, when, and each field as it was and as it became."""
    rows = (await s.execute(
        select(Edit, User.nd_username).join(User, User.id == Edit.user_id, isouter=True)
        .where(Edit.target_type == "track", Edit.target_id == track_id).order_by(Edit.id.desc()).limit(limit))).all()
    return [{"id": e.id, "by": who, "at": e.created_at, "changes": e.changes_json} for e, who in rows]


def cover_url(track: Track) -> str | None:
    return f"/api/library/tracks/{track.id}/cover?v={track.cover_key}" if track.cover_key else None


async def info(s: AsyncSession, track: Track) -> dict[str, Any]:
    """What the window of a track's names shows: the names, what the catalog calls it, who changed it last."""
    cat = track.catalog or {}
    last = (await s.execute(
        select(Edit, User.nd_username).join(User, User.id == Edit.user_id, isouter=True)
        .where(Edit.target_type == "track", Edit.target_id == track.id).order_by(Edit.id.desc()).limit(1))).first()
    album = await s.get(Album, track.album_id) if track.album_id else None
    return {
        "track_id": track.id, "title": track.title, "artist": track.artist,
        "catalog_title": cat.get("title"), "catalog_artist": ", ".join(cat.get("artists") or []) or cat.get("artist"),
        "renamed": track.renamed_at is not None,
        "last_by": last[1] if last else None, "last_at": last[0].created_at if last else None,
        "own_cover": track.cover_key is not None,
        "cover_url": cover_url(track) or (f"/api/library/albums/{album.id}/cover" if album is not None and album.cover_path
                                           else cat.get("cover_small_url") or cat.get("cover_url")),
        "album_id": album.id if album is not None else None, "album_title": album.title if album is not None else None,
        "has_file": await current_file(s, track.id) is not None,
    }
