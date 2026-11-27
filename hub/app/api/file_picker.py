"""Prepare, compare and select a file for an existing library track."""

from __future__ import annotations

import asyncio
import math
import re
import shutil
import uuid
from pathlib import Path
from tempfile import TemporaryDirectory, mkdtemp

from fastapi import APIRouter, Depends, File, HTTPException, UploadFile
from fastapi.responses import FileResponse
from mutagen import MutagenError
from pydantic import BaseModel, Field
from sqlalchemy import update
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession
from starlette.background import BackgroundTask

from app import listen
from app.api.quality import _cover
from app.auth import current_user
from app.catalog.models import CatalogNotFound
from app.config import get_settings
from app.covers import fetch_cover
from app.db import get_session
from app.events import bus
from app.imports import review
from app.jobs.downloads import enqueue_scan
from app.jobs.queue import notify
from app.library import acoustic, probe, tagger, upgrade
from app.library.files import main_of
from app.library.importer import TrackAlreadyFiled, cleanup_dir, copy_limited, library_abs, staging_dir_for
from app.library.tracks import by_ref, of_catalog
from app.models import ImportItem, Track, User
from app.providers.base import Candidate, TrackQuery, file_ref

router = APIRouter(prefix="/api/quality/track", tags=["file picker"])
_UPLOAD_REF = re.compile(r"upload:([0-9a-f]{32}\.[a-z0-9]+)\Z")
_take_lock = asyncio.Lock()


class TrackFileOut(BaseModel):
    track_id: int
    file_uid: str | None
    title: str
    artist: str
    duration_s: int | None = None
    cover_url: str | None = None
    codec: str | None = None
    bitrate_kbps: int | None = None
    source: str | None = None


class PickIn(BaseModel):
    ref: str | None = Field(default=None, max_length=300)
    url: str | None = Field(default=None, max_length=2048)


class TargetIn(BaseModel):
    ref: str = Field(pattern=r"^(dz|ym|sp):[^:]+$", max_length=100)


class CompareIn(BaseModel):
    ref: str = Field(max_length=300)
    baseline_ref: str | None = Field(default=None, max_length=300)
    expected_file_uid: str | None = Field(default=None, max_length=36)


class TakeIn(BaseModel):
    ref: str = Field(max_length=300)
    expected_file_uid: str | None = Field(max_length=36)
    sure: bool = False
    import_review_id: int | None = None


class PickOut(BaseModel):
    ref: str
    title: str
    artist: str
    provider: str
    cover_url: str | None = None
    page_url: str | None = None
    codec: str | None = None
    bitrate_kbps: int | None = None
    duration_s: int


class ComparisonOut(BaseModel):
    likeness: float | None
    verdict: str
    gaps: list[dict]
    peaks: dict[str, list[int]]
    length: dict[str, float]


async def _track(s: AsyncSession, track_id: int) -> Track:
    track = await s.get(Track, track_id)
    if track is None:
        raise HTTPException(404, "Трек не найден")
    return track


@router.post("/resolve")
async def resolve_target(body: TargetIn, user: User = Depends(current_user),
                         s: AsyncSession = Depends(get_session, scope="function")):
    from app.mixes.resolve import remembered_track
    from app.services import svc

    if (existing := await by_ref(s, body.ref)) is not None and existing.artist:
        return {"track_id": existing.id}
    user_id = user.id
    await s.commit()
    try:
        catalog = await remembered_track(body.ref) or await svc.track(body.ref)
    except CatalogNotFound as exc:
        raise HTTPException(404, "Запись в каталоге не найдена") from exc
    except Exception as exc:
        raise HTTPException(502, "Не удалось открыть запись в каталоге") from exc
    try:
        track = await of_catalog(s, catalog, user_id=user_id)
        await s.commit()
    except IntegrityError:
        await s.rollback()
        track = await by_ref(s, catalog.ref)
        if track is None:
            raise
    return {"track_id": track.id}


@router.get("/{track_id}", response_model=TrackFileOut)
async def track_file(track_id: int, user: User = Depends(current_user),
                     s: AsyncSession = Depends(get_session, scope="function")):
    track = await _track(s, track_id)
    file = await main_of(s, track_id)
    return TrackFileOut(track_id=track_id, file_uid=file.uid if file else None, title=track.title, artist=track.artist,
                        duration_s=track.duration_s, cover_url=_cover(track), codec=file.codec if file else None,
                        bitrate_kbps=file.bitrate_kbps if file else None, source=file.source if file else None)


def _upload_path(user_id: int, track_id: int, ref: str) -> Path | None:
    match = _UPLOAD_REF.fullmatch(ref)
    return get_settings().staging_dir / "uploads" / "file-picker" / str(user_id) / str(track_id) / match[1] if match else None


def _prepared_file(user_id: int, track_id: int, ref: str) -> listen.PreparedAudio:
    if (path := _upload_path(user_id, track_id, ref)) is not None and path.is_file():
        return listen.PreparedAudio(path, path.suffix)
    if listen.is_file_ref(ref) and (audio := listen.prepared_file(ref)) is not None:
        return audio
    raise HTTPException(410, "Временной копии нет. Подготовьте файл заново.")


async def _audio_file(audio: listen.PreparedAudio, start: float | None, end: float | None) -> FileResponse:
    headers = {"Cache-Control": "no-store"}
    if not audio.path.is_file():
        raise HTTPException(404, "Файла нет")
    if start is None and end is None:
        return FileResponse(audio.path, media_type=listen.local_media_type(Path("audio" + audio.suffix)), headers=headers)
    if (start is None or end is None or not math.isfinite(start) or not math.isfinite(end)
            or not 0 <= start < end or end - start > acoustic.WHOLE_S + 2):
        raise HTTPException(400, "Неверные границы отрывка")
    folder = Path(mkdtemp(dir=staging_dir_for("uploads", "file-excerpts")))
    dest = folder / "excerpt.wav"
    try:
        await probe.write_audio_excerpt(audio.path, dest, start, end)
        if dest.stat().st_size <= 78:
            raise probe.ProbeError("empty excerpt")
    except BaseException as exc:
        cleanup_dir(folder)
        if isinstance(exc, (probe.ProbeError, TimeoutError)):
            raise HTTPException(422, "Не удалось воспроизвести отрывок") from exc
        raise
    return FileResponse(dest, media_type="audio/wav", headers=headers, background=BackgroundTask(cleanup_dir, folder))


@router.get("/{track_id}/audio")
async def track_audio(track_id: int, start: float | None = None, end: float | None = None,
                      file_uid: str | None = None, user: User = Depends(current_user),
                      s: AsyncSession = Depends(get_session, scope="function")):
    file = await main_of(s, track_id)
    if file is None or (file_uid is not None and file.uid != file_uid):
        raise HTTPException(404, "Файл трека изменился")
    path = library_abs(file.path)
    await s.commit()
    return await _audio_file(listen.PreparedAudio(path, path.suffix), start, end)


@router.get("/{track_id}/picked/audio")
async def picked_audio(track_id: int, ref: str, start: float | None = None, end: float | None = None,
                       user: User = Depends(current_user)):
    return await _audio_file(_prepared_file(user.id, track_id, ref), start, end)


async def _candidate(ref: str) -> Candidate:
    candidate = await listen.file_candidate(ref) if listen.is_file_ref(ref) else None
    if candidate is None:
        raise HTTPException(404, "Файл не найден: повторите поиск")
    return candidate


async def _probe_audio(path: Path) -> probe.ProbeInfo:
    try:
        info = await probe.probe(path)
        if not info.duration_s:
            raise probe.ProbeError("empty audio")
        return info
    except (probe.ProbeError, TimeoutError) as exc:
        raise HTTPException(422, "Не удалось прочитать аудиофайл") from exc


def _file_out(ref: str, cand: Candidate, info: probe.ProbeInfo) -> PickOut:
    return PickOut(ref=ref, title=cand.title, artist=cand.artist, provider=cand.provider, cover_url=cand.cover_url,
                   page_url=cand.page_url or None, codec=info.codec, bitrate_kbps=info.bitrate_kbps,
                   duration_s=round(info.duration_s))


@router.post("/{track_id}/prepare", response_model=PickOut)
async def prepare(track_id: int, body: PickIn, user: User = Depends(current_user),
                  s: AsyncSession = Depends(get_session, scope="function")):
    from app.api.downloads import candidate_from_url

    track = await _track(s, track_id)
    query = TrackQuery(artist=track.artist, title=track.title)
    await s.commit()
    if body.url:
        cand = await candidate_from_url(body.url, query)
    elif body.ref:
        cand = await _candidate(body.ref)
    else:
        raise HTTPException(400, "Не указан файл")
    try:
        audio = await listen.prepare_file(cand)
    except listen.ListenError as exc:
        raise HTTPException(exc.status, str(exc)) from exc
    return _file_out(file_ref(cand.provider, cand.provider_id), cand, await _probe_audio(audio.path))


@router.post("/{track_id}/upload", response_model=PickOut)
async def upload_pick(track_id: int, file: UploadFile = File(...), user: User = Depends(current_user),
                      s: AsyncSession = Depends(get_session, scope="function")):
    track = await _track(s, track_id)
    artist = track.artist
    await s.commit()
    cfg = get_settings().cfg.uploads
    name = Path((file.filename or "").replace("\\", "/")).name
    ext = Path(name).suffix.lstrip(".").lower()
    if ext not in cfg.extensions:
        raise HTTPException(415, "Формат не поддерживается")
    ref = f"upload:{uuid.uuid4().hex}.{ext}"
    path = _upload_path(user.id, track_id, ref)
    assert path is not None
    path.parent.mkdir(parents=True, exist_ok=True)
    try:
        await asyncio.to_thread(copy_limited, file.file, path, cfg.max_file_mb * 1024 * 1024)
        info = await _probe_audio(path)
    except BaseException as exc:
        path.unlink(missing_ok=True)
        if isinstance(exc, ValueError):
            raise HTTPException(413, f"Файл больше {cfg.max_file_mb} МБ") from exc
        raise
    return _file_out(ref, Candidate(provider="upload", provider_id=ref, title=name, artist=artist, page_url=""), info)


async def _likeness(mine: Path, other: Path) -> float | None:
    a, b = await asyncio.gather(acoustic.of_file(mine), acoustic.of_file(other))
    return round(await asyncio.to_thread(acoustic.likeness, a[0], b[0]), 3) if a and b else None


def _verdict(alike: float | None) -> str:
    if alike is None:
        return "unknown"
    return "same" if alike >= acoustic.SAME else "alike" if alike >= acoustic.ALIKE else "other"


async def _attachment_cover(path: Path, *urls: str | None) -> bytes | None:
    """An attached recording's picture, ready to use if the library has no cover."""
    try:
        if embedded := await asyncio.to_thread(tagger.read_cover, path):
            return await asyncio.to_thread(tagger.prepare_cover, embedded)
    except (MutagenError, OSError, ValueError):
        pass
    for url in dict.fromkeys(u for u in urls if u):
        try:
            if image := await asyncio.wait_for(fetch_cover(url), 6):
                return await asyncio.to_thread(tagger.prepare_cover, image)
        except (TimeoutError, OSError, ValueError):
            continue
    return None


@router.post("/{track_id}/compare", response_model=ComparisonOut)
async def compare(track_id: int, body: CompareIn, user: User = Depends(current_user),
                  s: AsyncSession = Depends(get_session, scope="function")):
    await _track(s, track_id)
    other = _prepared_file(user.id, track_id, body.ref).path
    if body.baseline_ref:
        mine = _prepared_file(user.id, track_id, body.baseline_ref).path
    else:
        file = await main_of(s, track_id)
        if file is None:
            raise HTTPException(400, "Выберите основной файл")
        if body.expected_file_uid is not None and body.expected_file_uid != file.uid:
            raise HTTPException(409, "Файл трека уже изменился. Откройте окно заново.")
        mine = library_abs(file.path)
    await s.commit()
    if mine == other:
        raise HTTPException(400, "Выберите два разных файла")
    a, b, peaks_a, peaks_b, alike = await asyncio.gather(
        _probe_audio(mine), _probe_audio(other), upgrade.peaks(mine), upgrade.peaks(other), _likeness(mine, other))
    verdict = _verdict(alike)
    gaps = await acoustic.differences(mine, other) if verdict == "same" else []
    return ComparisonOut(likeness=alike, verdict=verdict, gaps=gaps, peaks={"mine": peaks_a, "other": peaks_b},
                         length={"mine": a.duration_s, "other": b.duration_s})


@router.post("/{track_id}/take")
async def take(track_id: int, body: TakeIn, user: User = Depends(current_user),
               s: AsyncSession = Depends(get_session, scope="function")):
    from app.imports.core import enqueue_once

    track = await _track(s, track_id)
    user_id = user.id
    audio = _prepared_file(user_id, track_id, body.ref)
    source_cover = None
    if body.ref.startswith("upload:"):
        provider, page_url = "upload", None
    else:
        cand = await _candidate(body.ref)
        provider, page_url = cand.provider, cand.page_url
        source_cover = cand.cover_url
    catalog_cover = (track.catalog or {}).get("cover_url")
    await s.commit()
    root = get_settings().staging_dir
    root.mkdir(parents=True, exist_ok=True)
    with TemporaryDirectory(prefix="replace-", dir=root) as folder:
        try:
            if provider == "upload":
                path = Path(folder) / ("audio" + audio.suffix)
                await asyncio.to_thread(shutil.copyfile, audio.path, path)
            else:
                path = await listen.stage_prepared_file(body.ref, Path(folder))
        except FileNotFoundError as exc:
            raise HTTPException(410, "Временной копии нет. Загрузите файл ещё раз.") from exc
        except listen.ListenError as exc:
            raise HTTPException(exc.status, str(exc)) from exc
        info = await _probe_audio(path)
        fallback_cover = None
        if body.expected_file_uid is None and not track.cover_key:
            fallback_cover = await _attachment_cover(path, source_cover, catalog_cover)
        async with _take_lock:
            file = await main_of(s, track_id)
            if (file.uid if file else None) != body.expected_file_uid:
                raise HTTPException(409, "Файл трека уже изменился. Откройте окно заново.")
            fields = dict(codec=info.codec, bitrate_kbps=info.bitrate_kbps, provider=provider,
                          source_url=page_url, duration_s=info.duration_s)
            if file is not None:
                alike = await _likeness(library_abs(file.path), path)
                if _verdict(alike) != "same" and not body.sure:
                    raise HTTPException(409, {"likeness": alike, "verdict": _verdict(alike)})
                try:
                    await upgrade.take_picked(s, user_id, track, path, **fields, likeness=alike, expected_file_uid=file.uid)
                except TrackAlreadyFiled as exc:
                    raise HTTPException(409, "Файл трека уже изменился. Откройте окно заново.") from exc
            else:
                try:
                    await upgrade.put(s, track_id, path, user_id=user_id, **fields, attach=True, fallback_cover=fallback_cover)
                except TrackAlreadyFiled as exc:
                    raise HTTPException(409, "У трека уже появился файл. Откройте окно заново.") from exc
                imports = (await s.execute(update(ImportItem)
                    .where(ImportItem.track_id == track_id, ImportItem.status.in_(("not_found", "failed", "waiting", "queued")))
                    .values(status="done").returning(ImportItem.import_id))).scalars().all()
                for import_id in set(imports):
                    await enqueue_once(s, "import_playlists", import_id)
            if body.import_review_id is not None:
                await review.acknowledge(s, user_id, body.import_review_id, track_id=track_id)
            await enqueue_scan(s)
            await s.commit()
    notify()
    bus.publish(user_id, "library", {"kind": "imported", "track_id": track_id})
    if body.import_review_id is not None:  # the question of «Наведение порядка» («Импорт») is answered by the file
        bus.publish(user_id, "tidy", {"kind": "answered"})
    await listen.forget(f"lib:{track_id}")
    if track.ref:
        await listen.forget(track.ref)
    return {"ok": True}
