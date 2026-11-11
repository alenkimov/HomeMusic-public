"""Lyrics of a library track (app/lyrics): for the player, the user's own choices and the admin's sources."""

from __future__ import annotations

from typing import Any

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel, Field
from sqlalchemy.ext.asyncio import AsyncSession

from app.auth import admin_user, current_user
from app.db import get_session
from app.jobs.queue import notify
from app.lyrics import service
from app.lyrics.providers import TITLES
from app.models import Lyrics, Track, User

router = APIRouter(prefix="/api/lyrics", tags=["lyrics"])


class LyricsOut(BaseModel):
    track_id: int | None = None  # None: the track is not in the Hub's library (no lyrics for it)
    kind: str = "none"  # word / synced / plain / none
    source: str | None = None
    source_title: str | None = None
    lines: list[dict[str, Any]] | None = None
    plain: str | None = None
    offset_ms: int = 0
    pinned: bool = False


class CandidateOut(BaseModel):
    source: str
    source_title: str
    kind: str
    preview: str


class OffsetIn(BaseModel):
    track_id: int
    offset_ms: int = Field(ge=-15_000, le=15_000)


class ChooseIn(BaseModel):
    track_id: int
    source: str | None = None  # None: this track has no lyrics (an instrumental, a wrong text)


class SourceIn(BaseModel):
    id: str
    on: bool = True


class SettingsIn(BaseModel):
    sources: list[SourceIn]
    retry_days: int = 7
    on_add: bool = True


class SettingsOut(BaseModel):
    sources: list[dict[str, Any]]  # {id, title, on}
    retry_days: int
    on_add: bool
    stats: dict[str, int]


def _out(row: Lyrics) -> LyricsOut:
    return LyricsOut(track_id=row.track_id, kind=row.kind, source=row.source, source_title=TITLES.get(row.source or ""),
                     lines=row.lines_json, plain=row.plain, offset_ms=row.offset_ms, pinned=row.pinned)


async def _own(s: AsyncSession, track_id: int) -> Track:
    track = await s.get(Track, track_id)
    if track is None:
        raise HTTPException(404, "Трек не найден в библиотеке")
    return track


@router.get("", response_model=LyricsOut)
async def get(nd_song_id: str | None = None, ref: str | None = None, user: User = Depends(current_user),
              s: AsyncSession = Depends(get_session, scope="function")):
    """The lyrics of a track the page names: from the Hub's database, or found now (seconds) and kept."""
    track = await service.track_of(s, nd_song_id=nd_song_id, ref=ref)
    if track is None:
        return LyricsOut()
    row = await service.ensure(s, track)
    await s.commit()
    return _out(row)


@router.get("/candidates", response_model=list[CandidateOut])
async def candidates(track_id: int, user: User = Depends(current_user),
                     s: AsyncSession = Depends(get_session, scope="function")):
    found = await service.candidates(s, await _own(s, track_id))
    def preview(f) -> str:
        texts = [x["text"] for x in f.lines] if f.lines else (f.plain or "").splitlines()
        return "\n".join(texts[:4])

    return [CandidateOut(source=f.source, source_title=TITLES[f.source], kind=f.kind, preview=preview(f)) for f in found]


@router.post("/choose", response_model=LyricsOut)
async def choose(body: ChooseIn, user: User = Depends(current_user),
                 s: AsyncSession = Depends(get_session, scope="function")):
    if body.source is not None and body.source not in TITLES:
        raise HTTPException(400, "Неизвестный источник")
    try:
        row = await service.choose(s, await _own(s, body.track_id), body.source)
    except LookupError as e:
        raise HTTPException(404, str(e)) from e
    await s.commit()
    return _out(row)


@router.post("/offset", response_model=LyricsOut)
async def offset(body: OffsetIn, user: User = Depends(current_user),
                 s: AsyncSession = Depends(get_session, scope="function")):
    """The text runs early or late against this file: shift it (kept with the text, for everyone)."""
    row = await s.get(Lyrics, body.track_id)
    if row is None:
        raise HTTPException(404, "У этого трека нет текста")
    row.offset_ms = body.offset_ms
    await s.commit()
    return _out(row)


async def _settings_out(s: AsyncSession) -> SettingsOut:
    v = await service.get_settings_row(s)
    return SettingsOut(sources=[{**x, "title": TITLES[x["id"]]} for x in v["sources"]], retry_days=v["retry_days"],
                       on_add=v["on_add"], stats=await service.stats(s))


@router.get("/settings", response_model=SettingsOut)
async def settings(user: User = Depends(admin_user), s: AsyncSession = Depends(get_session, scope="function")):
    return await _settings_out(s)


@router.put("/settings", response_model=SettingsOut)
async def save_settings(body: SettingsIn, user: User = Depends(admin_user),
                        s: AsyncSession = Depends(get_session, scope="function")):
    await service.save_settings(s, body.model_dump())
    await s.commit()
    return await _settings_out(s)


@router.post("/scan", response_model=SettingsOut)
async def scan(user: User = Depends(admin_user), s: AsyncSession = Depends(get_session, scope="function")):
    """Look for the lyrics of every track that was not checked yet, in the background."""
    await service.enqueue_scan_once(s, forced=True)
    await s.commit()
    notify()
    return await _settings_out(s)
