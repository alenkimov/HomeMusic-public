"""«Качество звука»: the weak files of the library looked for anew (app/library/upgrade.py) — the queue, what was
found, and the listener's word on the files that are only alike."""

from __future__ import annotations

from datetime import datetime
from typing import Literal

from fastapi import APIRouter, Depends, HTTPException
from fastapi.responses import FileResponse
from pydantic import BaseModel, Field
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.api.progress import SourceState
from app.auth import current_user
from app.catalog.models import CATALOGS
from app.config import get_settings
from app.db import get_session
from app.events import bus
from app.jobs.downloads import enqueue_scan
from app.jobs.queue import notify
from app.library import access, upgrade
from app.library.importer import library_abs
from app.library.tracks import current_file, current_files
from app.models import DownloadItem, Track, TrackFile, Upgrade, User

router = APIRouter(prefix="/api/quality", tags=["quality"])


class QualityIn(BaseModel):
    """The tracks to look for a better file of (none: every weak file the asker looks after)."""

    track_ids: list[int] | None = Field(default=None, max_length=20000)


class QualityRow(BaseModel):
    id: int
    track_id: int
    title: str
    artist: str
    cover_url: str | None = None
    state: str  # queued / searching / found / replaced / none / kept
    kind: str = "better"  # "identify": whether the track is the catalog's record `record`, told by the sound
    record: str | None = None  # that record, in words: «Название — Исполнитель · альбом»
    record_title: str | None = None
    record_ref: str | None = None
    progress: float = 0  # of the download under way, 0…1
    phase: Literal["searching", "downloading", "checking"] | None = None
    search_sources: dict[str, SourceState] = Field(default_factory=dict)
    known: bool = False  # the catalog knows the song: it is found sooner and surer
    was_codec: str | None = None
    was_kbps: int | None = None
    codec: str | None = None  # of the file found
    bitrate_kbps: int | None = None
    likeness: float | None = None
    sure: bool = False  # by the sound it is the same recording (though the two files may differ in length or at places)
    better: bool = False  # the file found is the better one of the two: there is something to replace the track's with
    provider: str | None = None
    note: str | None = None
    duration_s: int | None = None
    can_revert: bool = False  # the file it had is still kept: both can be heard, the old one brought back
    updated_at: datetime


class QualityOut(BaseModel):
    rows: list[QualityRow]
    weak: int  # weak files the asker looks after that nothing was looked for yet


def _record(meta: dict | None) -> str | None:
    m = meta or {}
    if not m.get("title"):
        return None
    return f"{m['title']} — {m.get('artist') or ''}" + (f" · «{m['album']}»" if m.get("album") else "")


def _cover(t: Track) -> str | None:
    if t.cover_key:
        return f"/api/library/tracks/{t.id}/cover?v={t.cover_key}"
    return f"/api/library/albums/{t.album_id}/cover" if t.album_id else None


async def _weak_ids(s: AsyncSession, user: User) -> list[int]:
    ids = list((await s.execute(select(Track.id).join(TrackFile, TrackFile.track_id == Track.id).where(access.tracks_of(user)).distinct())).scalars())
    files = await current_files(s, ids)
    return [i for i in ids if upgrade.wanted(files.get(i))]


@router.get("", response_model=QualityOut)
async def listed(user: User = Depends(current_user), s: AsyncSession = Depends(get_session, scope="function")):
    rows = (await s.execute(
        select(Upgrade, Track, DownloadItem.status, DownloadItem.progress, DownloadItem.provider,
               DownloadItem.search_sources_json).join(Track, Track.id == Upgrade.track_id)
        .join(DownloadItem, DownloadItem.id == Upgrade.item_id, isouter=True)
        .where(Upgrade.user_id == user.id).order_by(Upgrade.updated_at.desc(), Upgrade.id.desc()))).all()
    from app.library import acoustic, quality
    from app.library.tracks import current_files

    files = await current_files(s, [t.id for row, t, *_ in rows if row.state == "found"])
    seen: set[int] = set()
    out = []
    for row, t, status, progress, active_provider, sources in rows:  # of a track looked for twice, the last time
        if t.id in seen:
            continue
        seen.add(t.id)
        phase = {"searching": "searching", "downloading": "downloading", "tagging": "checking"}.get(status)
        if row.state != "searching":
            phase = None
        out.append(QualityRow(
            id=row.id, track_id=t.id, title=t.title, artist=t.artist, cover_url=_cover(t), state=row.state,
            kind=row.kind, record=_record(row.meta_json) if row.kind == "identify" else None,
            record_title=(row.meta_json or {}).get("title") if row.kind == "identify" else None,
            record_ref=(row.meta_json or {}).get("track_ref") if row.kind == "identify" else None,
            progress=float(progress or 0) if phase == "downloading" else 0, phase=phase,
            search_sources=(sources or {}) if phase == "searching" else {},
            known=(t.ref or "").partition(":")[0] in CATALOGS,
            was_codec=row.was_codec, was_kbps=row.was_kbps, codec=row.codec, bitrate_kbps=row.bitrate_kbps,
            likeness=row.likeness, provider=active_provider if phase in ("downloading", "checking") else row.provider,
            note=row.note, duration_s=t.duration_s,
            sure=row.likeness is not None and row.likeness >= acoustic.SAME,
            better=row.state == "found" and (f := files.get(t.id)) is not None and quality.better(row.codec, row.bitrate_kbps, f.codec, f.bitrate_kbps),
            can_revert=row.state == "replaced" and upgrade.was(row) is not None, updated_at=row.updated_at))
    return QualityOut(rows=out, weak=len([i for i in await _weak_ids(s, user) if i not in seen]))


@router.post("/start")
async def start(body: QualityIn, user: User = Depends(current_user), s: AsyncSession = Depends(get_session, scope="function")) -> dict:
    """Look for a better file of these tracks (none: of every weak file the asker looks after)."""
    ids = body.track_ids if body.track_ids is not None else await _weak_ids(s, user)
    n = await upgrade.start(s, user.id, ids)
    await s.commit()
    notify()
    return {"queued": n}


async def _row(s: AsyncSession, user: User, row_id: int) -> Upgrade:
    row = await s.get(Upgrade, row_id)
    if row is None or row.user_id != user.id:
        raise HTTPException(404, "Не найдено")
    return row


@router.post("/{row_id}/accept")
async def accept(row_id: int, file: bool | None = None, user: User = Depends(current_user),
                 s: AsyncSession = Depends(get_session, scope="function")) -> dict:
    """Yes. `file`: the file found takes the place of the track's own — said apart for «is it that record?», where
    yes by itself is of the record only and the track keeps its file; a better file found for a weak one is taken."""
    row = await _row(s, user, row_id)
    try:
        await upgrade.accept(s, row, user.id, take_file=bool(file) if row.kind == "identify" else True)
    except ValueError as e:
        raise HTTPException(409, str(e)) from e
    await enqueue_scan(s)
    await s.commit()
    notify()
    bus.publish(user.id, "library", {"kind": "imported", "track_id": row.track_id})
    return {"ok": True}


@router.post("/{row_id}/revert")
async def revert(row_id: int, user: User = Depends(current_user), s: AsyncSession = Depends(get_session, scope="function")) -> dict:
    """Take back the file the track had before a better one was put in its place."""
    row = await _row(s, user, row_id)
    try:
        await upgrade.revert(s, row, user.id)
    except ValueError as e:
        raise HTTPException(409, str(e)) from e
    await enqueue_scan(s)
    await s.commit()
    notify()
    bus.publish(user.id, "library", {"kind": "imported", "track_id": row.track_id})
    return {"ok": True}


@router.post("/{row_id}/decline")
async def decline(row_id: int, user: User = Depends(current_user), s: AsyncSession = Depends(get_session, scope="function")) -> dict:
    row = await _row(s, user, row_id)
    try:
        await upgrade.decline(s, row)
    except ValueError as e:
        raise HTTPException(409, str(e)) from e
    return {"ok": True}


@router.post("/{row_id}/version")
async def version(row_id: int, user: User = Depends(current_user), s: AsyncSession = Depends(get_session, scope="function")) -> dict:
    """The third answer to «is it that record?»: another version of the same song — the track stays as it is, and
    the two are tied as versions."""
    row = await _row(s, user, row_id)
    try:
        await upgrade.other_version(s, user.id, row)
    except ValueError as e:
        raise HTTPException(409, str(e)) from e
    await s.commit()
    return {"ok": True}


async def _file(s: AsyncSession, row: Upgrade, which: str):
    if which == "found":
        path = upgrade.staged(row)
    elif which == "was":
        path = upgrade.was(row)
    else:
        f = await current_file(s, row.track_id)
        path = library_abs(f.path) if f else None
    if path is None or not path.is_file():
        raise HTTPException(404, "Файла нет")
    return path


@router.get("/{row_id}/audio/{which}")
async def audio(row_id: int, which: str, user: User = Depends(current_user), s: AsyncSession = Depends(get_session, scope="function")):
    """The file the track has («current»), the one found for it («found») or the one it had («was»), to listen to."""
    return FileResponse(await _file(s, await _row(s, user, row_id), which), headers={"Cache-Control": "no-store"})


@router.get("/{row_id}/peaks")
async def peaks(row_id: int, user: User = Depends(current_user), s: AsyncSession = Depends(get_session, scope="function")) -> dict:
    """The loudness of the two files along their length: what the listener moves through them by."""
    from app.library import acoustic

    row = await _row(s, user, row_id)
    out: dict = {}
    paths = {}
    for which in ("current", "found", "was"):
        try:
            paths[which] = await _file(s, row, which)
            out[which] = await upgrade.peaks(paths[which])
        except HTTPException:
            out[which] = []
    # where the two files heard side by side part: the upper one's moments (`at`…`to`) and the lower one's
    # (`other_at`…`other_to`), and which of them falls quiet there — «upper» / «lower»
    upper, lower = ("was", "current") if "was" in paths else ("current", "found")
    gaps = await acoustic.differences(paths[upper], paths[lower]) if upper in paths and lower in paths else []
    out["gaps"] = [{**g, "quieter": {"mine": "upper", "other": "lower"}.get(g.get("quieter"))} for g in gaps]
    whole = {w: await acoustic.of_whole(paths[w]) for w in (upper, lower) if w in paths}
    out["length"] = {w: (v[1] if v else None) for w, v in whole.items()}
    return out
