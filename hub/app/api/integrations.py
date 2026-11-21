"""User-owned provider sessions and the Hub's common credentials."""

from __future__ import annotations

import asyncio
import logging
import re
from datetime import datetime, timedelta
from secrets import token_urlsafe
from typing import Literal
from urllib.parse import urlsplit

from fastapi import APIRouter, Depends, HTTPException, Query, Request
from fastapi.responses import HTMLResponse
from pydantic import BaseModel, Field, SecretStr
from sqlalchemy import delete, or_, select, update
from sqlalchemy.ext.asyncio import AsyncSession
from yandex_music.exceptions import UnauthorizedError

from app import integrations, security
from app.auth import admin_user, current_user
from app.catalog import spotify_session
from app.catalog.spotify import resource
from app.catalog.yandex import yandex_client
from app.db import get_session
from app.imports.sources import yandex
from app.imports.sources.base import AuthPending, SourceError
from app.mixes.sources import SourceDown, _lastfm
from app.models import IntegrationAuthAttempt, IntegrationConnection, User, utcnow
from app.services import svc

router = APIRouter(prefix="/api/integrations", tags=["integrations"])
callback_router = APIRouter(include_in_schema=False)
log = logging.getLogger(__name__)


class ConnectIn(BaseModel):
    shared: bool = False


class TicketIn(BaseModel):
    ticket: str = Field(pattern=r"^[A-Za-z0-9_-]{43}$")


class KeyIn(BaseModel):
    key: SecretStr = Field(min_length=1, max_length=256)


class CheckIn(BaseModel):
    playlist: str = Field(default="", max_length=512)


class ConnectionOut(BaseModel):
    id: int
    provider: str
    owner: str | None
    account: str | None
    plan: str | None
    shared: bool
    can_manage: bool
    state: str
    connected_at: datetime | None
    checked_at: datetime | None


class BuiltInOut(BaseModel):
    service: str
    available: bool


class ConnectionsOut(BaseModel):
    connections: list[ConnectionOut]
    catalogs: list[BuiltInOut]
    sources: list[BuiltInOut]


def _out(row: IntegrationConnection, user: User, name: str | None = None) -> ConnectionOut:
    mine = row.owner_user_id == user.id
    manageable = mine or (row.provider == "lastfm" and user.is_admin)
    return ConnectionOut(
        id=row.id, provider=row.provider, owner=name or (user.nd_username if mine else None),
        account=row.account if mine else None, plan=row.plan, shared=row.shared,
        can_manage=manageable, state=row.state, connected_at=row.connected_at, checked_at=row.checked_at,
    )


@router.get("", response_model=ConnectionsOut)
async def list_connections(user: User = Depends(current_user),
                           s: AsyncSession = Depends(get_session, scope="function")):
    row = IntegrationConnection
    connections = (await s.execute(
        select(row, User.nd_username).outerjoin(User, User.id == row.owner_user_id)
        .where(row.retained.is_(True), row.provider != "vk",
               or_(row.owner_user_id == user.id, row.shared.is_(True)))
        .order_by(row.provider, row.connected_at.desc(), row.id.desc())
    )).all()
    catalogs = [BuiltInOut(service=key, available=client is not None) for key, client in (
        ("deezer", svc.deezer), ("spotify", svc.spotify), ("yandex", svc.yandex), ("musicbrainz", svc.musicbrainz),
    )]
    sources = [BuiltInOut(service=key, available=name in svc.providers) for key, name in (
        ("hitmoz", "hitmoz"), ("hitmos_fm", "hitmos_fm"), ("youtube", "ytdlp"), ("soundcloud", "soundcloud"),
    )]
    return ConnectionsOut(connections=[_out(entry, user, owner) for entry, owner in connections],
                          catalogs=catalogs, sources=sources)


async def _editable(s: AsyncSession, user: User, connection_id: int) -> IntegrationConnection:
    row = await s.get(IntegrationConnection, connection_id)
    if row is None or row.provider == "vk" or not row.retained:
        raise HTTPException(404, "Подключение не найдено")
    if row.owner_user_id == user.id or (row.provider == "lastfm" and user.is_admin):
        return row
    raise HTTPException(404, "Подключение не найдено")


@router.patch("/{connection_id:int}", response_model=ConnectionOut)
async def change_access(connection_id: int, body: ConnectIn, user: User = Depends(current_user),
                        s: AsyncSession = Depends(get_session, scope="function")):
    row = await _editable(s, user, connection_id)
    if row.provider not in integrations.SHAREABLE or row.owner_user_id != user.id:
        raise HTTPException(409, "Доступ к этому подключению изменить нельзя")
    row.shared = body.shared
    return _out(row, user)


@router.delete("/{connection_id:int}")
async def disconnect(connection_id: int, user: User = Depends(current_user),
                     s: AsyncSession = Depends(get_session, scope="function")):
    row = await _editable(s, user, connection_id)
    await integrations.remove(s, row)
    return {"ok": True}


@router.post("/lastfm", response_model=ConnectionOut)
async def connect_lastfm(body: KeyIn, user: User = Depends(admin_user),
                         s: AsyncSession = Depends(get_session, scope="function")):
    key = body.key.get_secret_value().strip()
    if not key:
        raise HTTPException(400, "Укажите ключ Last.fm")
    try:
        await _lastfm("artist.getInfo", artist="The Beatles", key=key)
    except SourceDown:
        raise HTTPException(400, "Last.fm не подтвердил ключ. Проверьте его и попробуйте снова.") from None
    row = await integrations.save(s, provider="lastfm", owner_id=None, secret=key)
    return _out(row, user)


@router.post("/{provider}/connect")
async def start_auth(provider: Literal["spotify", "yandex"], body: ConnectIn,
                     user: User = Depends(current_user), s: AsyncSession = Depends(get_session, scope="function")):
    payload: dict = {}
    seconds = 300
    ticket, digest = security.new_token()
    authorization_url = None
    if provider == "yandex":
        try:
            response = await yandex.device_start(f"homemusic-services-{user.id}")
        except SourceError:
            raise HTTPException(502, "Яндекс не выдал код входа") from None
        address = response.get("verification_url") or "https://ya.ru/device"
        parsed = urlsplit(address)
        if parsed.scheme != "https" or parsed.netloc not in {"ya.ru", "oauth.yandex.ru", "yandex.ru"}:
            address = "https://ya.ru/device"
        payload = {"device_code": security.encrypt(response["device_code"]), "user_code": response["user_code"],
                   "url": address, "interval": max(3, int(response.get("interval") or 5))}
        seconds = min(600, int(response.get("expires_in") or 300))
    else:
        verifier = token_urlsafe(48)
        payload = {"verifier": security.encrypt(verifier)}
        authorization_url = spotify_session.authorize(ticket, verifier)
    now = utcnow()
    await s.execute(delete(IntegrationAuthAttempt).where(or_(
        IntegrationAuthAttempt.expires_at <= now,
        (IntegrationAuthAttempt.owner_user_id == user.id) & (IntegrationAuthAttempt.provider == provider),
    )))
    attempt = IntegrationAuthAttempt(
        digest=digest, provider=provider, owner_user_id=user.id, shared=body.shared,
        expires_at=now + timedelta(seconds=seconds), next_poll_at=now + timedelta(seconds=payload.get("interval", 0)),
        payload=payload)
    s.add(attempt)
    return {"ticket": ticket, "expires_at": attempt.expires_at, "interval": payload.get("interval", 3),
            "user_code": payload.get("user_code"), "verification_url": payload.get("url"),
            "authorization_url": authorization_url}


async def _auth(s: AsyncSession, ticket: str, *, owner_id: int | None = None,
                provider: str | None = None) -> IntegrationAuthAttempt:
    row = await s.get(IntegrationAuthAttempt, security.hash_token(ticket))
    if (row is None or row.expires_at <= utcnow() or (owner_id is not None and row.owner_user_id != owner_id)
            or (provider is not None and row.provider != provider)):
        raise HTTPException(404, "Ссылка для подключения истекла")
    return row


async def _advance(s: AsyncSession, row: IntegrationAuthAttempt, before: str, after: str) -> bool:
    result = await s.execute(update(IntegrationAuthAttempt).where(
        IntegrationAuthAttempt.digest == row.digest, IntegrationAuthAttempt.stage == before,
        IntegrationAuthAttempt.expires_at > utcnow(),
    ).values(stage=after))
    await s.commit()
    return result.rowcount == 1


async def _pending_result(s: AsyncSession, digest: str) -> IntegrationAuthAttempt | None:
    # The user may have started a fresh sign-in while the network request was in flight.
    return await s.scalar(select(IntegrationAuthAttempt).where(
        IntegrationAuthAttempt.digest == digest, IntegrationAuthAttempt.stage == "validating",
        IntegrationAuthAttempt.expires_at > utcnow(),
    ).execution_options(populate_existing=True))


def _yandex_identity(token: str) -> tuple[str, str | None, str | None]:
    subject, account, plan = yandex.account_details(yandex_client(token).init())
    if not subject:
        raise ValueError("Yandex account identifier missing")
    return subject, account, plan


@router.post("/auth/poll")
async def poll_auth(body: TicketIn, user: User = Depends(current_user),
                    s: AsyncSession = Depends(get_session, scope="function")):
    row = await _auth(s, body.ticket, owner_id=user.id)
    if row.provider == "spotify" or row.stage != "waiting" or (row.next_poll_at and row.next_poll_at > utcnow()):
        return {"stage": row.stage}
    if not await _advance(s, row, "waiting", "validating"):
        return {"stage": "validating"}
    pending = None
    try:
        token = await yandex.device_poll(security.decrypt(row.payload["device_code"]))
        subject, name, plan = await asyncio.to_thread(_yandex_identity, token)
    except AuthPending as error:
        pending = error
    except Exception:
        if current := await _pending_result(s, row.digest):
            current.stage, current.payload = "failed", {}
        return {"stage": "failed"}
    if (current := await _pending_result(s, row.digest)) is None:
        raise HTTPException(404, "Код входа устарел")
    if pending is not None:
        interval = current.payload["interval"] + (5 if str(pending) == "slow_down" else 0)
        current.payload = {**current.payload, "interval": interval}
        current.next_poll_at, current.stage = utcnow() + timedelta(seconds=interval), "waiting"
    else:
        await integrations.save(s, provider="yandex", owner_id=user.id, secret=token, subject=subject,
                                account=name, plan=plan, shared=current.shared)
        current.stage, current.payload = "connected", {}
    return {"stage": current.stage}


@callback_router.get("/login")
async def spotify_callback(request: Request, state: str = Query(default="", max_length=43),
                           code: str = Query(default="", max_length=4096),
                           s: AsyncSession = Depends(get_session, scope="function")):
    if request.headers.get("host") != f"127.0.0.1:{spotify_session.PORT}":
        raise HTTPException(404, "Страница не найдена")
    if not re.fullmatch(r"[A-Za-z0-9_-]{43}", state):
        raise HTTPException(400, "Ссылка для входа недействительна")
    row = await _auth(s, state, provider="spotify")
    if not await _advance(s, row, "waiting", "validating"):
        raise HTTPException(403, "Ссылка для входа уже использована")
    identity = None
    try:
        if code:
            identity = await spotify_session.exchange(code, security.decrypt(row.payload["verifier"]))
    except Exception as error:
        log.info("Spotify sign-in failed (%s)", type(error).__name__)
    if (current := await _pending_result(s, row.digest)) is None:
        raise HTTPException(404, "Ссылка для подключения истекла")
    if identity:
        await integrations.save(s, provider="spotify", owner_id=current.owner_user_id, subject=identity["subject"],
                                account=identity["subject"], plan=identity["plan"], shared=current.shared,
                                secret=identity["credential"])
    current.stage, current.payload = ("connected" if identity else "failed"), {}
    message = "Spotify подключён" if identity else "Вход в Spotify не завершился"
    return HTMLResponse(
        "<!doctype html><html lang='ru'><meta charset='utf-8'><meta name='viewport' content='width=device-width'>"
        "<title>Spotify · Хаб</title><body style='color-scheme:dark;font:1rem system-ui;margin:4rem 1.5rem'>"
        f"<h1>{message}</h1><p>Закройте вкладку и вернитесь в Хаб.</p></body></html>",
        headers={"Cache-Control": "no-store", "Referrer-Policy": "no-referrer"},
    )


@router.post("/{connection_id:int}/check")
async def check_connection(connection_id: int, body: CheckIn, user: User = Depends(current_user),
                           s: AsyncSession = Depends(get_session, scope="function")):
    row = await _editable(s, user, connection_id)
    if row.owner_user_id != user.id or row.provider not in integrations.SHAREABLE:
        raise HTTPException(409, "Проверка доступна владельцу аккаунта")
    link = body.playlist.strip()
    target = resource(link) if link and row.provider == "spotify" else None
    if link and (target is None or target[0] != "playlist"):
        raise HTTPException(422, "Вставьте ссылку на плейлист Spotify")
    result: dict = {}
    token = await integrations.credential(s, row.id)
    if not token:
        row.state, row.checked_at = "reauth", utcnow()
        return {"connection": _out(row, user)}
    try:
        if row.provider == "spotify":
            checked = await spotify_session.check(token, target[1] if target else None)
            subject = name = checked["subject"]
            plan = checked["plan"]
            if target:
                result["playlist"] = checked["playlist"]
            else:
                result["liked_read"] = checked["liked_read"]
        else:
            subject, name, plan = await asyncio.to_thread(_yandex_identity, token)
        if row.subject and row.subject != subject:
            raise ValueError("account identity changed")
        row.account, row.plan = name, plan
        row.state = "connected"
    except UnauthorizedError:
        row.state = "reauth"
    except spotify_session.SessionError as error:
        if error.expired:
            row.state = "reauth"
        elif target:
            result["playlist_error"] = "Не удалось прочитать этот плейлист"
        else:
            row.state = "unavailable"
    except Exception:
        row.state = "unavailable"
    row.checked_at = utcnow()
    return {"connection": _out(row, user), **result}
