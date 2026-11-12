"""A user's ListenBrainz account (app/listenbrainz.py): connect, see how it stands, send the history anew, turn off."""

from __future__ import annotations

from datetime import datetime

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel, Field
from sqlalchemy.ext.asyncio import AsyncSession

from app import listenbrainz as lb
from app.auth import current_user
from app.db import get_session
from app.models import User
from app.security import encrypt

router = APIRouter(prefix="/api/listenbrainz", tags=["listenbrainz"])


class StatusOut(BaseModel):
    connected: bool
    user: str | None = None
    sent_until: datetime | None = None  # the last listen that went there (UTC)


class ConnectIn(BaseModel):
    token: str = Field(min_length=8, max_length=200)


class SentOut(StatusOut):
    sent: int = 0


async def _me(s: AsyncSession, user: User) -> User:
    me = await s.get(User, user.id)
    if me is None:
        raise HTTPException(401, "Нет такого пользователя")
    return me


def _out(u: User) -> StatusOut:
    return StatusOut(connected=bool(u.lb_token_enc), user=u.lb_user, sent_until=u.lb_sent_until)


@router.get("", response_model=StatusOut)
async def status(user: User = Depends(current_user), s: AsyncSession = Depends(get_session, scope="function")):
    return _out(await _me(s, user))


@router.post("/connect", response_model=StatusOut)
async def connect(body: ConnectIn, user: User = Depends(current_user),
                  s: AsyncSession = Depends(get_session, scope="function")):
    token = body.token.strip()
    try:
        name = await lb.validate(token)
    except Exception as e:
        raise HTTPException(502, "ListenBrainz не отвечает. Попробуйте позже.") from e
    if not name:
        raise HTTPException(400, "ListenBrainz не принял этот токен. Скопируйте его целиком: listenbrainz.org → Settings → User token.")
    u = await _me(s, user)
    u.lb_token_enc, u.lb_user, u.lb_sent_until = encrypt(token), name, None
    await s.commit()
    return _out(u)


@router.post("/send", response_model=SentOut)
async def send(again: bool = False, user: User = Depends(current_user),
               s: AsyncSession = Depends(get_session, scope="function")):
    """Send what has not been sent yet; `again` — the whole history anew (ListenBrainz keeps one of each listen)."""
    u = await _me(s, user)
    if not u.lb_token_enc:
        raise HTTPException(400, "ListenBrainz не подключён")
    if again:
        u.lb_sent_until = None
    await s.commit()
    try:
        sent = await lb.sync_user(user.id)
    except Exception as e:
        raise HTTPException(502, "ListenBrainz не принял прослушивания. Попробуйте позже.") from e
    await s.refresh(u)
    return SentOut(**_out(u).model_dump(), sent=sent)


@router.delete("", response_model=StatusOut)
async def disconnect(user: User = Depends(current_user), s: AsyncSession = Depends(get_session, scope="function")):
    u = await _me(s, user)
    u.lb_token_enc = u.lb_user = u.lb_sent_until = None
    await s.commit()
    return _out(u)
