"""Manual playback queue shared by a user's players."""

from fastapi import APIRouter, Depends, HTTPException, Response
from pydantic import BaseModel, Field
from sqlalchemy.ext.asyncio import AsyncSession

from app import playback_queue as queue
from app.auth import current_user
from app.db import get_session
from app.models import User

router = APIRouter(prefix="/api/playback/queue", tags=["playback"])


class AppendIn(BaseModel):
    tracks: list[queue.QueueTrack] = Field(min_length=1, max_length=400)
    expected_user_id: queue.QueueId
    request_id: str = Field(min_length=1, max_length=48)


class RemoveIn(BaseModel):
    ids: list[queue.QueueId] = Field(min_length=1, max_length=400)


class MoveIn(BaseModel):
    before_id: queue.QueueId | None = None


class ClaimIn(BaseModel):
    token: queue.ClaimToken


@router.get("", response_model=list[queue.QueueEntry], response_model_exclude_none=True)
async def list_queue(response: Response, user: User = Depends(current_user),
                     s: AsyncSession = Depends(get_session, scope="function")):
    response.headers["Cache-Control"] = "no-store"
    return await queue.entries(s, user.id)


@router.post("")
async def append(body: AppendIn, user: User = Depends(current_user),
                 s: AsyncSession = Depends(get_session, scope="function")):
    if body.expected_user_id != user.id:
        raise HTTPException(409, "Очередь принадлежит другому аккаунту")
    count = await queue.append(s, user.id, body.tracks, source_key=f"web:{body.request_id}")
    if count:
        await s.commit()
        queue.publish(user.id)
    return {"count": count}


@router.post("/remove")
async def remove(body: RemoveIn, user: User = Depends(current_user),
                 s: AsyncSession = Depends(get_session, scope="function")):
    count = await queue.remove(s, user.id, body.ids)
    if count:
        await s.commit()
        queue.publish(user.id)
    return {"count": count}


@router.post("/{entry_id}/move")
async def move(entry_id: queue.QueueId, body: MoveIn, user: User = Depends(current_user),
               s: AsyncSession = Depends(get_session, scope="function")):
    if not await queue.move(s, user.id, entry_id, body.before_id):
        raise HTTPException(409, "Очередь уже изменилась")
    await s.commit()
    queue.publish(user.id)
    return {"ok": True}


@router.post("/{entry_id}/claim")
async def claim(entry_id: queue.QueueId, body: ClaimIn, user: User = Depends(current_user),
                s: AsyncSession = Depends(get_session, scope="function")):
    return {"ok": await queue.claim(s, user.id, entry_id, body.token)}


@router.post("/{entry_id}/release")
async def release(entry_id: queue.QueueId, body: ClaimIn, user: User = Depends(current_user),
                  s: AsyncSession = Depends(get_session, scope="function")):
    await queue.release(s, user.id, entry_id, body.token)
    return {"ok": True}


@router.post("/{entry_id}/played")
async def played(entry_id: queue.QueueId, body: ClaimIn, user: User = Depends(current_user),
                 s: AsyncSession = Depends(get_session, scope="function")):
    if await queue.finish(s, user.id, entry_id, body.token):
        await s.commit()
        queue.publish(user.id)
    return {"ok": True}
