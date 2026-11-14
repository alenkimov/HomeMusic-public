"""Own library: albums, tracks, covers, stars (FR-4 «В коллекцию», FR-5 editor).

The files are looked after by who brought them and by the admins (`app/library/access.py`)."""

from __future__ import annotations

import asyncio
import logging
import io
import uuid

from fastapi import APIRouter, Depends, File, HTTPException, Query, UploadFile
from fastapi.responses import Response
from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.api.schemas import (
    TrackEditOut,
    AlbumCreateIn,
    AlbumMergeIn,
    AlbumPatchIn,
    FileJournalOut,
    LibAlbumOut,
    LibTrackOut,
    StarIn,
    StarredOut,
    TrackPatchIn,
    TracksMoveIn,
    TrackSourceOut,
)
from app.auth import current_user, user_navidrome
from app.catalog.models import split_ref
from app.db import get_session
from app.events import bus
from app.jobs.downloads import enqueue_scan
from app.jobs.queue import notify
from app.library import access, editor, edits, tagger
from app.library.importer import album_reldir, library_abs
from app.library.tracks import current_file, current_files
from app.matching.normalize import normalize_title
from app.library.owned import file_out
from app.models import Album, DownloadItem, Track, TrackFile, User

router = APIRouter(tags=["library"])
log = logging.getLogger(__name__)


def track_out(t: Track, f: TrackFile | None) -> LibTrackOut:
    """A track of the library with what its file is (`f`: the file that plays it)."""
    return LibTrackOut(
        id=t.id, nd_song_id=t.nd_song_id, album_id=t.album_id, artist=t.artist, title=t.title, album=t.album,
        album_artist=t.album_artist, track_no=t.track_no, disc_no=t.disc_no, year=t.year, duration_s=t.duration_s,
        bitrate_kbps=f.bitrate_kbps if f else None, codec=f.codec if f else None, explicit=t.explicit,
        source=f.source if f else None, locked=bool(f and f.locked), warnings=(f.warnings if f else None) or [],
        path=f.path if f else "",
    )


def _has_cover(a: Album) -> bool:
    return (library_abs(album_reldir(a)) / "cover.jpg").is_file()


async def album_out(s: AsyncSession, a: Album, user: User, *, with_tracks: bool = False) -> LibAlbumOut:
    """The album as `user` sees it among their files: their own tracks of it (an admin: all)."""
    tracks = (
        await s.execute(select(Track).where(Track.album_id == a.id, access.tracks_of(user))
                        .order_by(Track.disc_no, Track.track_no, Track.id))
    ).scalars().all()
    files = await current_files(s, [t.id for t in tracks]) if with_tracks else {}
    return LibAlbumOut(
        id=a.id, uid=a.uid, title=a.title, album_artist=a.album_artist, year=a.year, release_type=a.release_type,
        genre=a.genre, deezer_album_id=a.deezer_album_id, track_count=len(tracks), has_cover=_has_cover(a),
        can_edit=await access.owns_album(s, user, a),
        tracks=[track_out(t, files.get(t.id)) for t in tracks] if with_tracks else None,
    )


async def _track(s: AsyncSession, track_id: int) -> Track:
    """A track to put right: anybody signed in may (app/library/edits.py) — the library is everybody's."""
    t = await s.get(Track, track_id)
    if t is None:
        raise HTTPException(404, "Трек не найден")
    return t


async def _album(s: AsyncSession, album_id: int) -> Album:
    a = await s.get(Album, album_id)
    if a is None:
        raise HTTPException(404, "Альбом не найден")
    return a


async def _after_edit(s: AsyncSession, user: User, album_id: int | None) -> None:
    await enqueue_scan(s)
    await s.commit()
    notify()
    bus.publish(user.id, "library", {"kind": "changed", "album_id": album_id})


@router.get("/api/library/albums", response_model=list[LibAlbumOut])
async def list_albums(q: str | None = None, offset: int = 0, limit: int = Query(50, le=200),
                      user: User = Depends(current_user), s: AsyncSession = Depends(get_session, scope="function")):
    rows = (await s.execute(select(Album).where(access.albums_of(user)).order_by(Album.updated_at.desc()))).scalars().all()
    if q:
        nq = normalize_title(q).text
        rows = [a for a in rows if all(w in normalize_title(f"{a.album_artist} {a.title}").text for w in nq.split())]
    rows = rows[offset: offset + limit]
    counts = dict((await s.execute(
        select(Track.album_id, func.count(Track.id)).where(Track.album_id.in_([a.id for a in rows]), access.tracks_of(user))
        .group_by(Track.album_id)
    )).all())
    return [
        LibAlbumOut(id=a.id, uid=a.uid, title=a.title, album_artist=a.album_artist, year=a.year,
                    release_type=a.release_type, genre=a.genre, deezer_album_id=a.deezer_album_id,
                    track_count=counts.get(a.id, 0), has_cover=_has_cover(a),
                    can_edit=await access.owns_album(s, user, a))
        for a in rows
    ]


@router.post("/api/library/albums", response_model=LibAlbumOut)
async def create_album(body: AlbumCreateIn, user: User = Depends(current_user), s: AsyncSession = Depends(get_session, scope="function")):
    a = Album(uid=str(uuid.uuid4()), title=body.title.strip(), album_artist=body.album_artist.strip(), year=body.year,
              genre=body.genre, release_type=body.release_type, created_by=user.id)
    s.add(a)
    await s.flush()
    return await album_out(s, a, user, with_tracks=True)


@router.get("/api/library/track-source", response_model=TrackSourceOut)
async def track_source(nd_song_id: str, user: User = Depends(current_user),
                       s: AsyncSession = Depends(get_session, scope="function")):
    f = (await s.execute(select(TrackFile).where(TrackFile.nd_song_id == nd_song_id).limit(1))).scalar_one_or_none()
    if f is None:
        raise HTTPException(404, "Трек не найден")
    return TrackSourceOut(source=f.source, source_url=f.source_url, acquired_at=f.acquired_at)


@router.get("/api/library/albums/{album_id}", response_model=LibAlbumOut)
async def get_album(album_id: int, user: User = Depends(current_user), s: AsyncSession = Depends(get_session, scope="function")):
    return await album_out(s, await access.need_album(s, user, album_id), user, with_tracks=True)


@router.patch("/api/library/albums/{album_id}", response_model=LibAlbumOut)
async def patch_album(album_id: int, body: AlbumPatchIn, user: User = Depends(current_user),
                      s: AsyncSession = Depends(get_session, scope="function")):
    a = await _album(s, album_id)
    await edits.update_album(s, user, a, **body.model_dump(exclude_unset=True))
    await _after_edit(s, user, a.id)
    return await album_out(s, a, user, with_tracks=True)


@router.put("/api/library/albums/{album_id}/cover", response_model=LibAlbumOut)
async def put_cover(album_id: int, file: UploadFile = File(...), user: User = Depends(current_user),
                    s: AsyncSession = Depends(get_session, scope="function")):
    a = await _album(s, album_id)
    data = await file.read()
    if len(data) > 20 * 1024 * 1024:
        raise HTTPException(413, "Картинка слишком большая")
    try:
        await edits.set_album_cover(s, user, a, data)
    except Exception as e:
        raise HTTPException(400, f"Не удалось прочитать картинку: {e}") from e
    await _after_edit(s, user, a.id)
    return await album_out(s, a, user, with_tracks=True)


@router.get("/api/library/albums/{album_id}/cover")
async def get_cover(album_id: int, size: int | None = Query(None, ge=32, le=1000), user: User = Depends(current_user),
                    s: AsyncSession = Depends(get_session, scope="function")):
    a = await s.get(Album, album_id)  # a picture is shown wherever the album is, to anybody
    if a is None:
        raise HTTPException(404, "Альбом не найден")
    p = library_abs(album_reldir(a)) / "cover.jpg"
    data: bytes | None = await asyncio.to_thread(p.read_bytes) if p.is_file() else None
    if data is None:
        t = (await s.execute(select(Track).where(Track.album_id == a.id).limit(1))).scalar_one_or_none()
        if t is not None and library_abs(t.path).is_file():
            data = await asyncio.to_thread(tagger.read_cover, library_abs(t.path))
    if data is None:
        raise HTTPException(404, "Нет обложки")
    if size:
        data = await asyncio.to_thread(_resize, data, size)
    return Response(data, media_type="image/jpeg", headers={"Cache-Control": "private, max-age=3600"})


def _resize(data: bytes, size: int) -> bytes:
    from PIL import Image

    im = Image.open(io.BytesIO(data)).convert("RGB")
    im.thumbnail((size, size))
    buf = io.BytesIO()
    im.save(buf, "JPEG", quality=85)
    return buf.getvalue()


@router.post("/api/library/albums/merge", response_model=LibAlbumOut)
async def merge(body: AlbumMergeIn, user: User = Depends(current_user), s: AsyncSession = Depends(get_session, scope="function")):
    target = await access.need_own_album(s, user, body.target_id)
    for source_id in body.source_ids:
        await access.need_own_album(s, user, source_id)
    await editor.merge_albums(s, body.source_ids, target)
    await _after_edit(s, user, target.id)
    return await album_out(s, target, user, with_tracks=True)


@router.post("/api/library/tracks/move", response_model=LibAlbumOut)
async def move(body: TracksMoveIn, user: User = Depends(current_user), s: AsyncSession = Depends(get_session, scope="function")):
    target = await access.need_album(s, user, body.album_id)
    for track_id in body.track_ids:
        await access.need_own_track(s, user, track_id)
    await editor.move_tracks(s, body.track_ids, target)
    await _after_edit(s, user, target.id)
    return await album_out(s, target, user, with_tracks=True)


@router.patch("/api/library/tracks/{track_id}", response_model=LibTrackOut)
async def patch_track(track_id: int, body: TrackPatchIn, user: User = Depends(current_user),
                      s: AsyncSession = Depends(get_session, scope="function")):
    t = await _track(s, track_id)
    try:
        await edits.update_track(s, user, t, **body.model_dump(exclude_unset=True))
    except ValueError as e:
        raise HTTPException(422, str(e)) from e
    await _after_edit(s, user, t.album_id)
    await s.refresh(t)
    return track_out(t, await current_file(s, t.id))


@router.get("/api/library/track-edit", response_model=TrackEditOut)
async def track_edit(track_id: int | None = None, ref: str | None = None, nd_song_id: str | None = None,
                     user: User = Depends(current_user), s: AsyncSession = Depends(get_session, scope="function")):
    """A track's names as they are, what the catalog calls it and who changed it last — for the window that changes
    them. A song of the catalog the Hub has not met yet becomes a track here: it can be renamed as any other."""
    from app.lyrics.service import track_for

    t = await s.get(Track, track_id) if track_id else await track_for(s, nd_song_id=nd_song_id, ref=ref, user_id=user.id)
    if t is None:
        raise HTTPException(404, "Трек не найден")
    out = await edits.info(s, t)
    await s.commit()
    return out


@router.put("/api/library/tracks/{track_id}/cover", response_model=TrackEditOut)
async def put_track_cover(track_id: int, file: UploadFile = File(...), user: User = Depends(current_user),
                          s: AsyncSession = Depends(get_session, scope="function")):
    """The track's own cover, for everyone: where the album's picture is not its."""
    t = await _track(s, track_id)
    data = await file.read()
    if len(data) > 20 * 1024 * 1024:
        raise HTTPException(413, "Картинка слишком большая")
    try:
        await edits.set_track_cover(s, user, t, data)
    except Exception as e:
        raise HTTPException(400, f"Не удалось прочитать картинку: {e}") from e
    await _after_edit(s, user, t.album_id)
    return await edits.info(s, t)


@router.delete("/api/library/tracks/{track_id}/cover", response_model=TrackEditOut)
async def delete_track_cover(track_id: int, user: User = Depends(current_user),
                             s: AsyncSession = Depends(get_session, scope="function")):
    """No cover of its own any more: the album's again."""
    t = await _track(s, track_id)
    await edits.set_track_cover(s, user, t, None)
    await _after_edit(s, user, t.album_id)
    return await edits.info(s, t)


@router.get("/api/library/tracks/{track_id}/cover")
async def get_track_cover(track_id: int, size: int | None = Query(None, ge=32, le=1000), user: User = Depends(current_user),
                          s: AsyncSession = Depends(get_session, scope="function")):
    data = await edits.track_cover(s, track_id)
    if data is None:
        raise HTTPException(404, "Нет обложки")
    if size:
        data = await asyncio.to_thread(_resize, data, size)
    return Response(data, media_type="image/jpeg", headers={"Cache-Control": "private, max-age=31536000, immutable"})


@router.get("/api/library/tracks/{track_id}/journal", response_model=FileJournalOut)
async def track_journal(track_id: int, user: User = Depends(current_user),
                        s: AsyncSession = Depends(get_session, scope="function")):
    """How the track's file came to the library: the journal of the download that brought it (the last one that
    kept a journal). The library is everybody's, so anybody may read how a file of it was picked."""
    t = await s.get(Track, track_id)
    if t is None:
        raise HTTPException(404, "Трек не найден")
    f = await current_file(s, t.id)
    item = (await s.execute(select(DownloadItem).where(
        DownloadItem.track_id == t.id, func.json_array_length(DownloadItem.log_json) > 0)
        .order_by(DownloadItem.id.desc()).limit(1))).scalar_one_or_none()
    file = file_out(f) if f else None
    if file:
        file.journal = item is not None
    return FileJournalOut(track_id=t.id, artist=t.artist, title=t.title, file=file,
                          steps=list(item.log_json or []) if item else [],
                          download_id=item.id if item and item.user_id == user.id and not item.hidden else None)


@router.delete("/api/library/tracks/{track_id}")
async def delete_track(track_id: int, user: User = Depends(current_user), s: AsyncSession = Depends(get_session, scope="function")):
    t = await access.need_own_track(s, user, track_id)
    album_id = t.album_id
    await editor.trash_track(s, t)
    await _after_edit(s, user, album_id)
    return {"ok": True}


@router.get("/api/starred", response_model=StarredOut)
async def starred(user: User = Depends(current_user), s: AsyncSession = Depends(get_session, scope="function")):
    from app.api.schemas import FavoriteTarget
    from app import playlists
    from app.favorites import ND_FIELDS, AliasIndex, imported_targets, project_liked, snapshot, subjects_of

    # what the buttons of tracks go by is the Hub's own list; Navidrome is asked only about albums and artists
    nd = user_navidrome(user)
    try:
        body = await nd.get_starred2()
    except Exception as e:
        log.info("starred: Navidrome did not say what is starred: %r", e)
        body = None
    finally:
        await nd.aclose()
    await project_liked(s, user.id, body.get("song") or [] if body is not None else None)
    await s.commit()
    body = body or {}
    liked = [t for _, t in await playlists.items(s, (await playlists.liked(s, user.id)).id)]
    states = await snapshot(s, user.id)
    subjects = [dict(target_type=x.target_type, key=x.key, ref=x.ref, nd_id=x.nd_id,
                     liked=x.liked, liked_since=x.since, target_data=x.data,
                     event_id=x.event.event_id, aliases=x.aliases, provenance="own") for x in states.values()]
    index = AliasIndex([x.event for x in states.values()], await subjects_of(s))  # albums and artists
    for state in states.values():
        index.join(state.target_type, set(state.aliases))
    imported = {index.root(k) for k in await imported_targets(s, user.id)}
    for typ, field in (("album", "album"), ("artist", "artist")):
        for obj in body.get(field, []):
            if not obj.get("id"):
                continue
            target = FavoriteTarget(target_type=typ, **{ND_FIELDS[typ]: obj["id"]})
            key = index.key(target)
            if key in imported and key not in states:
                subjects.append(dict(target_type=typ, key=key[1], nd_id=obj["id"], liked=True,
                                     aliases=[f"nd:{obj['id']}"], provenance="import"))
    refs = {t.ref for t in liked if t.ref and not t.nd_song_id}  # liked tracks the library has no song of yet
    unliked = {a for x in states.values() if x.target_type == "track" and not x.liked for a in x.aliases}
    foreign = [x["id"] for x in body.get("song", []) if x.get("id") and f"nd:{x['id']}" not in unliked]
    return StarredOut(song_ids=list(dict.fromkeys([*(t.nd_song_id for t in liked if t.nd_song_id), *foreign])),
                      album_ids=[x["id"] for x in body.get("album", []) if x.get("id")],
                      artist_ids=[x["id"] for x in body.get("artist", []) if x.get("id")],
                      refs=sorted(refs), subjects=subjects)


@router.post("/api/star")
async def star(body: StarIn, user: User = Depends(current_user), s: AsyncSession = Depends(get_session, scope="function")):
    from app.api.schemas import FavoriteTarget
    from app.favorites import command

    targets = list(body.targets)
    targets += [FavoriteTarget(nd_song_id=x) for x in body.song_ids]
    targets += [FavoriteTarget(target_type="album", nd_album_id=x) for x in body.album_ids]
    targets += [FavoriteTarget(target_type="artist", nd_artist_id=x) for x in body.artist_ids]
    if body.refs and not body.unstar:
        raise HTTPException(400, "Чтобы отметить трек каталога, поставьте его в загрузку с ♥")
    targets += [FavoriteTarget(ref=x) for x in body.refs]
    nd = user_navidrome(user)
    try:
        duplicate_unlike = False
        if body.unstar and body.event_id and len(targets) == 1:
            from app.models import PlayEvent
            duplicate_unlike = (await s.execute(select(PlayEvent.id).where(
                PlayEvent.user_id == user.id, PlayEvent.event_id == body.event_id))).first() is not None
        if body.catalog_album_ref:
            src, id_ = split_ref(body.catalog_album_ref)
            q = select(Track.nd_song_id).join(Album, Album.id == Track.album_id).where(Track.nd_song_id.is_not(None))
            q = q.where(Album.deezer_album_id == id_) if src == "dz" else q.where(Album.id == -1)
            song_id = (await s.execute(q.limit(1))).scalar_one_or_none()
            if song_id is None:
                raise HTTPException(409, "Этого альбома ещё нет в библиотеке — сначала скачайте его")
            song = await nd.get_song(song_id)
            if not song.get("albumId"):
                raise HTTPException(409, "Альбом ещё не появился в Navidrome")
            targets.append(FavoriteTarget(target_type="album", ref=body.catalog_album_ref,
                                          nd_album_id=song["albumId"], target_data={"artist": song.get("artist")}))
        events = await command(s, user, targets, liked=not body.unstar, operation=body.event_id,
                               ts=body.ts, client=body.client, context=body.context, nd=nd)
        # Telemetry may have stored the same decision before the command. Pending downloads
        # still need cancellation, but an older unlike must not cancel a newer like.
        if body.unstar and body.event_id:
            from app.favorites import clear_pending, snapshot
            states = await snapshot(s, user.id)
            for target in targets:
                if target.target_type != "track" or not target.ref:
                    continue
                state = next((x for x in states.values() if x.target_type == "track" and x.ref == target.ref), None)
                if state and not state.liked:
                    await clear_pending(s, user.id, target.ref)
                    if duplicate_unlike and state.nd_id:
                        await nd.unstar(song_ids=[state.nd_id])
    except Exception:
        # Preserve successful chunks of a bulk request even if a later chunk fails.
        await s.commit()
        raise
    finally:
        await nd.aclose()
    from app.favorites import project_liked

    await project_liked(s, user.id)
    await s.commit()
    bus.publish(user.id, "library", {"kind": "unstarred" if body.unstar else "starred"})
    return {"ok": True, "event_ids": [e.event_id for e in events]}
