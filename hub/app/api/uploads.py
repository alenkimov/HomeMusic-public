"""Uploads of own audio files from the phone (FR-5): stream to staging → edit tags → import into an album."""

from __future__ import annotations

import asyncio
import json
import logging
import shutil
import uuid
from datetime import timedelta
from pathlib import Path

from fastapi import APIRouter, Depends, File, Form, HTTPException, UploadFile
from fastapi.responses import Response
from pydantic import ValidationError
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.api.schemas import UploadCommitIn, UploadCommitOut, UploadOut
from app.auth import current_user
from app.config import get_settings
from app.db import get_session, session_scope
from app.events import bus
from app.jobs.downloads import enqueue_scan
from app.jobs.queue import RetryLater, enqueue, handler, notify
from app.library import probe, tagger
from app.library.importer import ImportSpec, import_file
from app.models import Album, Job, Track, Upload, User, utcnow

log = logging.getLogger(__name__)
PLAYLIST_ADD_RETRY_S = 15
PLAYLIST_ADD_WAIT_S = 30 * 60  # a scan of a big library is slow; then the playlist takes what is ready

router = APIRouter(prefix="/api/uploads", tags=["uploads"])

CHUNK = 1024 * 1024


def upload_out(u: Upload) -> UploadOut:
    return UploadOut(id=u.id, orig_name=u.orig_name, size=u.size, status=u.status, tags=u.probed_tags_json or {},
                     error=u.error, created_at=u.created_at)


def _upload_dir(user_id: int) -> Path:
    d = get_settings().staging_dir / "uploads" / str(user_id)
    d.mkdir(parents=True, exist_ok=True)
    return d


def _abs(u: Upload) -> Path:
    return get_settings().staging_dir / Path(u.staging_path)


def _copy_limited(src, dest: Path, limit: int) -> int:
    n = 0
    with dest.open("wb") as out:
        while chunk := src.read(CHUNK):
            n += len(chunk)
            if n > limit:
                raise ValueError("too large")
            out.write(chunk)
    return n


@router.post("", response_model=list[UploadOut])
async def upload(files: list[UploadFile] = File(...), user: User = Depends(current_user),
                 s: AsyncSession = Depends(get_session, scope="function")):
    cfg = get_settings().cfg.uploads
    limit = cfg.max_file_mb * 1024 * 1024
    out = []
    for f in files:
        name = Path(f.filename or "file").name
        ext = name.rsplit(".", 1)[-1].lower() if "." in name else ""
        if ext not in cfg.extensions:
            raise HTTPException(415, f"Формат не поддерживается: {name}")
        rel = Path("uploads") / str(user.id) / f"{uuid.uuid4().hex}.{ext}"
        dest = get_settings().staging_dir / rel
        _upload_dir(user.id)
        try:
            size = await asyncio.to_thread(_copy_limited, f.file, dest, limit)
        except ValueError:
            dest.unlink(missing_ok=True)
            raise HTTPException(413, f"Файл больше {cfg.max_file_mb} МБ: {name}") from None
        try:
            tags = await asyncio.to_thread(tagger.read_tags, dest)
        except Exception as e:
            dest.unlink(missing_ok=True)
            raise HTTPException(400, f"Не удалось прочитать файл {name}: {e}") from e
        if not tags.get("title"):
            tags["title"] = name.rsplit(".", 1)[0]
        u = Upload(user_id=user.id, staging_path=rel.as_posix(), orig_name=name, size=size,
                   probed_tags_json=json.loads(json.dumps(tags, default=str)))
        s.add(u)
        await s.flush()
        out.append(upload_out(u))
    return out


@router.get("", response_model=list[UploadOut])
async def list_uploads(status: str | None = "uploaded", user: User = Depends(current_user),
                       s: AsyncSession = Depends(get_session, scope="function")):
    stmt = select(Upload).where(Upload.user_id == user.id)
    if status:
        stmt = stmt.where(Upload.status == status)
    return [upload_out(u) for u in (await s.execute(stmt.order_by(Upload.id.desc()).limit(200))).scalars()]


async def _own(s: AsyncSession, user: User, upload_id: int) -> Upload:
    u = await s.get(Upload, upload_id)
    if u is None or u.user_id != user.id:
        raise HTTPException(404, "Файл не найден")
    return u


@router.get("/{upload_id}/cover")
async def cover(upload_id: int, user: User = Depends(current_user), s: AsyncSession = Depends(get_session, scope="function")):
    u = await _own(s, user, upload_id)
    p = _abs(u)
    data = await asyncio.to_thread(tagger.read_cover, p) if p.is_file() else None
    if not data:
        raise HTTPException(404, "Нет обложки")
    return Response(data, media_type="image/jpeg")


@router.delete("/{upload_id}")
async def remove(upload_id: int, user: User = Depends(current_user), s: AsyncSession = Depends(get_session, scope="function")):
    u = await _own(s, user, upload_id)
    _abs(u).unlink(missing_ok=True)
    u.status = "discarded"
    return {"ok": True}


@router.post("/commit", response_model=UploadCommitOut)
async def commit(payload: str = Form(...), cover: UploadFile | None = File(None), user: User = Depends(current_user),
                 s: AsyncSession = Depends(get_session, scope="function")):
    try:
        body = UploadCommitIn.model_validate_json(payload)
    except ValidationError as e:
        raise HTTPException(422, f"Неверные данные формы: {e.errors()[:3]}") from e
    if not body.tracks:
        raise HTTPException(400, "Не выбраны файлы")

    if body.album.mode == "existing":
        album = await s.get(Album, body.album.album_id or 0)
        if album is None:
            raise HTTPException(404, "Альбом не найден")
    else:
        if not body.album.title or not body.album.album_artist:
            raise HTTPException(400, "Укажите название и исполнителя альбома")
        album = Album(uid=str(uuid.uuid4()), title=body.album.title.strip(),
                      album_artist=body.album.album_artist.strip(), year=body.album.year, genre=body.album.genre,
                      release_type=body.album.release_type or "album", created_by=user.id)
        s.add(album)
        await s.flush()

    cover_bytes: bytes | None = await cover.read() if cover is not None else None
    if not cover_bytes and body.cover_from_upload_id:
        cu = await _own(s, user, body.cover_from_upload_id)
        cover_bytes = await asyncio.to_thread(tagger.read_cover, _abs(cu))

    imported = 0
    track_ids: list[int] = []
    for i, tr in enumerate(body.tracks):
        u = await _own(s, user, tr.upload_id)
        if u.status != "uploaded":
            continue
        src = _abs(u)
        if not src.is_file():
            u.status, u.error = "failed", "файл пропал из staging"
            continue
        try:
            info = await probe.probe(src)
        except Exception as e:
            log.warning("probe failed for upload %s: %r", u.id, e)
            info = None
        spec = ImportSpec(
            artist=tr.artist.strip() or album.album_artist,
            title=tr.title.strip() or u.orig_name,
            album_id=album.id,
            track_no=tr.track_no or (i + 1),
            disc_no=tr.disc_no or 1,
            genre=tr.genre or album.genre,
            explicit=tr.explicit,
            cover=cover_bytes if i == 0 or cover_bytes else None,
            source="upload",
            source_url=u.orig_name,
            duration_s=round(info.duration_s) if info and info.duration_s else None,
            bitrate_kbps=info.bitrate_kbps if info else None,
            codec=info.codec if info else None,
            added_by=user.id,
        )
        try:
            track = await import_file(s, src, spec)
        except Exception as e:
            log.exception("upload import failed")
            u.status, u.error = "failed", str(e)[:500]
            continue
        u.status, u.track_id = "imported", track.id
        imported += 1
        track_ids.append(track.id)
    await enqueue_scan(s)
    if body.tag_ids and track_ids:
        from app import tags

        await tags.assign_tracks(s, user.id, track_ids, body.tag_ids)
    if (body.playlist_id or body.star) and track_ids:
        # Navidrome knows the new files only after its scan: the playlist and the ♥ get them then
        await enqueue(s, "upload_playlist_add", {
            "user_id": user.id, "playlist_id": body.playlist_id, "star": body.star, "track_ids": track_ids,
            "until": (utcnow() + timedelta(seconds=PLAYLIST_ADD_WAIT_S)).isoformat()}, delay_s=PLAYLIST_ADD_RETRY_S)
    await s.commit()
    notify()
    bus.publish(user.id, "library", {"kind": "imported", "album_id": album.id})
    return UploadCommitOut(album_id=album.id, imported=imported)


@handler("upload_playlist_add")
async def handle_playlist_add(job: Job) -> None:
    """«Добавить ещё и в плейлист» / «в Любимое» of an import: the imported tracks go into the playlist and
    get their ♥ in their order once the library scan has given them Navidrome song ids. After `until` the ones that
    have ids go, the rest are left out."""
    from app.api.schemas import FavoriteTarget
    from app.auth import user_navidrome
    from app.favorites import command

    p = job.payload_json
    async with session_scope() as s:
        user = await s.get(User, int(p["user_id"]))
        tracks = [await s.get(Track, int(i)) for i in p["track_ids"]]
        songs = [t.nd_song_id for t in tracks if t is not None]  # a track deleted meanwhile is not waited for
        names = {t.nd_song_id: {"title": t.title, "artist": t.artist} for t in tracks if t is not None and t.nd_song_id}
    if user is None or not songs:
        return
    if any(x is None for x in songs) and utcnow().isoformat() < p["until"]:
        raise RetryLater(PLAYLIST_ADD_RETRY_S, "waiting for the library scan")
    ready = [x for x in songs if x]
    if not ready:
        log.warning("upload: no song ids for playlist %s after the wait", p.get("playlist_id"))
        return
    nd = user_navidrome(user)
    try:
        if p.get("playlist_id"):
            await nd.update_playlist(p["playlist_id"], add=ready)
        if p.get("star"):
            # through the favorites journal, as a ♥ set by hand: mixes and «Любимое» see it the same way
            async with session_scope() as s:
                await command(s, user, [FavoriteTarget(nd_song_id=x, target_data=names.get(x)) for x in ready],
                              liked=True, client="hub", context="upload", nd=nd)
    finally:
        await nd.aclose()
    bus.publish(user.id, "library", {"kind": "playlist", "playlist_id": p.get("playlist_id"), "star": bool(p.get("star"))})


def purge_stale_uploads(days: int = 7) -> int:
    """Remove staged upload files never committed (called by the daily housekeeping)."""
    import time

    root = get_settings().staging_dir / "uploads"
    if not root.is_dir():
        return 0
    cutoff = time.time() - days * 86400
    n = 0
    for p in root.rglob("*"):
        if p.is_file() and p.stat().st_mtime < cutoff:
            p.unlink(missing_ok=True)
            n += 1
    shutil.rmtree(root / "_tmp", ignore_errors=True)
    return n
