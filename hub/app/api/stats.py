"""«Статистика» (app/stats.py): the page, the numbers of the «Прослушивания» column, the history of Spotify."""

from __future__ import annotations

import asyncio
import zipfile
from typing import Any

from fastapi import APIRouter, Depends, File, HTTPException, Query, UploadFile
from pydantic import BaseModel
from sqlalchemy.ext.asyncio import AsyncSession

from app import stats
from app.auth import current_user
from app.db import get_session
from app.models import User

router = APIRouter(prefix="/api/stats", tags=["stats"])

MAX_UPLOAD_BYTES = 500 * 1024 * 1024


class ImportOut(BaseModel):
    found: int  # listens in the files
    added: int
    duplicates: int  # already there
    linked: int  # of the added: tracks the library has
    outside: int  # of the added: only in the statistics
    listens: int  # of the added: 30 seconds and longer


@router.get("")
async def page(period: str = Query("month", pattern="^(week|month|halfyear|year|all)$"),
               by: str = Query("plays", pattern="^(plays|time)$"), tz: str = Query("UTC", max_length=64),
               limit: int = Query(5, ge=1, le=500), user: User = Depends(current_user),
               s: AsyncSession = Depends(get_session, scope="function")) -> dict[str, Any]:
    return await stats.overview(s, user.id, period=period, by=by, timezone=tz, limit=limit)


@router.get("/counts")
async def counts(user: User = Depends(current_user),
                 s: AsyncSession = Depends(get_session, scope="function")) -> dict[str, int]:
    """Navidrome song id -> listens in all the time."""
    return await stats.counts(s, user.id)


@router.post("/import", response_model=ImportOut)
async def import_history(files: list[UploadFile] = File(...), user: User = Depends(current_user),
                         s: AsyncSession = Depends(get_session, scope="function")):
    """Spotify's history: the archive it sends (.zip) or the .json files of it."""
    if sum(f.size or 0 for f in files) > MAX_UPLOAD_BYTES:
        raise HTTPException(413, "Файлы слишком большие")
    try:
        rows = await asyncio.to_thread(stats.parse_history, [(f.filename or "file", f.file) for f in files])
    except (ValueError, zipfile.BadZipFile) as e:
        raise HTTPException(400, str(e) if isinstance(e, ValueError) else "Архив повреждён") from e
    if not rows:
        raise HTTPException(400, "В файлах нет истории прослушиваний Spotify. Нужны файлы Streaming_History_Audio_….json "
                                 "(или весь архив целиком).")
    out = await stats.import_history(s, user.id, rows)
    await s.commit()
    return ImportOut(**out)


@router.delete("/import")
async def forget(user: User = Depends(current_user), s: AsyncSession = Depends(get_session, scope="function")):
    """The imported history is gone; what was listened to here stays."""
    removed = await stats.forget_history(s, user.id)
    await s.commit()
    return {"ok": True, "removed": removed}
