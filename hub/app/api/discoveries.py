"""«Открытия недели» (app/mixes/discover.py): the issues and the tracks of one."""

from __future__ import annotations

from typing import Any

from fastapi import APIRouter, Depends, HTTPException, Query
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.auth import current_user
from app.db import get_session
from app.jobs.queue import notify
from app.library.owned import annotate_tracks
from app.mixes import discover
from app.models import DiscoveryIssue, User

router = APIRouter(prefix="/api/discoveries", tags=["discoveries"])


@router.get("")
async def page(issue: int | None = Query(None), user: User = Depends(current_user),
               s: AsyncSession = Depends(get_session, scope="function")) -> dict[str, Any]:
    """The user's issues, newest first, and the tracks of one (the newest unless `issue` names another)."""
    issues = list((await s.execute(select(DiscoveryIssue).where(DiscoveryIssue.user_id == user.id)
                                   .order_by(DiscoveryIssue.week.desc()))).scalars())
    shown = next((x for x in issues if x.id == issue), None) if issue is not None else (issues[0] if issues else None)
    if issue is not None and shown is None:
        raise HTTPException(404, "Такого выпуска нет")
    tracks = await annotate_tracks(s, discover.as_tracks(shown), user.id) if shown else []
    return {
        "issues": [{"id": x.id, "week": x.week.isoformat(), "count": len(x.tracks_json or [])} for x in issues],
        "issue": {"id": shown.id, "week": shown.week.isoformat(), "tracks": [t.model_dump(mode="json") for t in tracks]} if shown else None,
        "building": await discover.building(s, user.id),
    }


@router.post("/build")
async def build(user: User = Depends(current_user), s: AsyncSession = Depends(get_session, scope="function")) -> dict[str, bool]:
    """Build this week's issue now (anew, if the week has one)."""
    queued = await discover.enqueue_build(s, user.id)
    await s.commit()
    if queued:
        notify()
    return {"ok": True}
