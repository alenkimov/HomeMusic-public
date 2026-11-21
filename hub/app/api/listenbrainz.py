"""A user's ListenBrainz account (app/listenbrainz.py): connect, see how it stands, send the history anew, turn off."""

from __future__ import annotations

from datetime import datetime

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel, Field, SecretStr
from sqlalchemy.ext.asyncio import AsyncSession

from app import integrations
from app import listenbrainz as lb
from app.auth import current_user
from app.db import get_session
from app.models import ListenBrainzSync, User

router = APIRouter(prefix="/api/listenbrainz", tags=["listenbrainz"])


class StatusOut(BaseModel):
    connected: bool
    user: str | None = None
    sent_until: datetime | None = None  # the last listen that went there (UTC)


class ConnectIn(BaseModel):
    token: SecretStr = Field(min_length=8, max_length=200)


class SentOut(StatusOut):
    sent: int = 0


async def _out(s: AsyncSession, user_id: int) -> StatusOut:
    row = await integrations.latest_owned(s, user_id, "listenbrainz")
    if row is None:
        return StatusOut(connected=False)
    sync = await s.get(ListenBrainzSync, row.id)
    return StatusOut(connected=True, user=row.account, sent_until=sync.sent_until if sync else None)


@router.get("", response_model=StatusOut)
async def status(user: User = Depends(current_user), s: AsyncSession = Depends(get_session, scope="function")):
    return await _out(s, user.id)


@router.post("/connect", response_model=StatusOut)
async def connect(body: ConnectIn, user: User = Depends(current_user),
                  s: AsyncSession = Depends(get_session, scope="function")):
    token = body.token.get_secret_value().strip()
    try:
        name = await lb.validate(token)
    except Exception:
        raise HTTPException(502, "ListenBrainz не отвечает. Попробуйте позже.") from None
    if not name:
        raise HTTPException(400, "ListenBrainz не принял этот токен. Скопируйте его целиком: listenbrainz.org → Settings → User token.")
    old = await integrations.latest_owned(s, user.id, "listenbrainz")
    row = await integrations.save(s, provider="listenbrainz", owner_id=user.id, subject=name, account=name, secret=token)
    if old and old.id != row.id:
        await integrations.remove(s, old)
    return await _out(s, user.id)


@router.post("/send", response_model=SentOut)
async def send(again: bool = False, user: User = Depends(current_user),
               s: AsyncSession = Depends(get_session, scope="function")):
    """Send what has not been sent yet; `again` — the whole history anew (ListenBrainz keeps one of each listen)."""
    row = await integrations.latest_owned(s, user.id, "listenbrainz")
    if row is None or row.state == "reauth":
        raise HTTPException(400, "ListenBrainz не подключён")
    if again:
        checkpoint = await s.get(ListenBrainzSync, row.id)
        if checkpoint is not None:
            checkpoint.sent_until = None
    await s.commit()
    try:
        sent = await lb.sync_user(user.id)
    except Exception as e:
        raise HTTPException(502, "ListenBrainz не принял прослушивания. Попробуйте позже.") from e
    current = await _out(s, user.id)
    return SentOut(**current.model_dump(), sent=sent)


@router.delete("", response_model=StatusOut)
async def disconnect(user: User = Depends(current_user), s: AsyncSession = Depends(get_session, scope="function")):
    row = await integrations.latest_owned(s, user.id, "listenbrainz")
    if row is not None:
        await integrations.remove(s, row)
    return StatusOut(connected=False)
