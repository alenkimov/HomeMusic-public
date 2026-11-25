"""«Резервные копии» of the admin's page: Google Drive connected or not, the last copy, a copy now (app/backup)."""

from __future__ import annotations

from datetime import datetime

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel
from sqlalchemy.ext.asyncio import AsyncSession

from app.auth import admin_user
from app.backup import drive, service
from app.db import get_session
from app.jobs.queue import notify
from app.models import User

router = APIRouter(prefix="/api/backup", tags=["backup"])


class BackupCopyOut(BaseModel):
    day: str  # YYYY-MM-DD
    size: int


class BackupPendingOut(BaseModel):
    user_code: str
    url: str


class BackupOut(BaseModel):
    configured: bool  # GOOGLE_DRIVE_CLIENT_ID and _SECRET in .env
    connected: bool
    email: str | None = None
    running: bool
    last_ok: datetime | None = None
    last_try: datetime | None = None
    error: str | None = None
    stale: bool  # connected, and nothing sent for two days
    copies: list[BackupCopyOut] = []
    files_count: int = 0
    files_size: int = 0
    drive_used: int | None = None
    drive_limit: int | None = None
    pending: BackupPendingOut | None = None  # a sign-in waiting for the code to be typed


class ConnectOut(BaseModel):
    user_code: str
    url: str
    interval: int


class PollOut(BaseModel):
    state: str  # pending / connected / failed
    error: str | None = None
    backup: BackupOut


@router.get("", response_model=BackupOut)
async def status(_: User = Depends(admin_user), s: AsyncSession = Depends(get_session, scope="function")):
    return await service.status(s)


@router.post("/connect", response_model=ConnectOut)
async def connect(_: User = Depends(admin_user), s: AsyncSession = Depends(get_session, scope="function")):
    """A code for google.com/device: the admin types it and allows the Hub its own files of their Drive."""
    if not drive.configured():
        raise HTTPException(400, "В .env нет GOOGLE_DRIVE_CLIENT_ID и GOOGLE_DRIVE_CLIENT_SECRET (README, «Резервные копии»)")
    try:
        out = await service.connect_start(s)
    except drive.DriveError as e:
        raise HTTPException(502, str(e)) from e
    await s.commit()
    return out


@router.post("/connect/poll", response_model=PollOut)
async def poll(_: User = Depends(admin_user), s: AsyncSession = Depends(get_session, scope="function")):
    state = await service.connect_poll(s)
    await s.commit()
    notify()
    kind, _, why = state.partition(":")
    return PollOut(state=kind, error=why or None, backup=await service.status(s))


@router.delete("/connect", response_model=BackupOut)
async def disconnect(_: User = Depends(admin_user), s: AsyncSession = Depends(get_session, scope="function")):
    await service.disconnect(s)
    await s.commit()
    return await service.status(s)


@router.post("/run", response_model=BackupOut)
async def run_now(_: User = Depends(admin_user), s: AsyncSession = Depends(get_session, scope="function")):
    """«Сделать копию сейчас»: a fresh dump of the database, sent with what is not on the Drive yet."""
    if not (await service.state(s)).get("token"):
        raise HTTPException(400, "Сначала подключите Google Drive")
    await service.request(s, dump=True)
    await s.commit()
    notify()
    return await service.status(s)
