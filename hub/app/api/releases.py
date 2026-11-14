"""«Релизы» (app/releases.py): the feed of what followed artists have put out."""

from __future__ import annotations

from typing import Any

from fastapi import APIRouter, Depends
from sqlalchemy.ext.asyncio import AsyncSession

from app import releases
from app.auth import current_user
from app.db import get_session
from app.models import User

router = APIRouter(prefix="/api/releases", tags=["releases"])


@router.get("")
async def feed(user: User = Depends(current_user), s: AsyncSession = Depends(get_session, scope="function")) -> dict[str, Any]:
    await releases.refresh(user.id, only_new=True)  # an artist followed since the last look is in the feed at once
    await releases.fill_tracks(user.id, releases.FILL_AT_ONCE)  # the newest releases are shown with their tracks
    return await releases.feed(s, user.id)


@router.get("/count")
async def count(user: User = Depends(current_user), s: AsyncSession = Depends(get_session, scope="function")) -> dict[str, int]:
    artists = await releases.followed(s, user.id)
    return {"unseen": await releases.unseen(s, user.id, artists), "artists": len(artists)}


@router.post("/seen")
async def seen(user: User = Depends(current_user), s: AsyncSession = Depends(get_session, scope="function")) -> dict[str, bool]:
    await releases.mark_seen(s, user.id)
    return {"ok": True}
