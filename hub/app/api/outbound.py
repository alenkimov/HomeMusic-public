"""The admin's page of the Hub's outside requests (`app.outbound`): the proxies, what goes through them, the journal."""

from __future__ import annotations

from datetime import datetime, timedelta
from typing import Literal
from urllib.parse import unquote, urlsplit

import httpx
from fastapi import APIRouter, Depends, HTTPException, Query
from pydantic import BaseModel, Field
from sqlalchemy import delete, func, select, text
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession

from app import outbound
from app.auth import admin_user
from app.db import get_session
from app.models import IntegrationConnection, OutboundRequest, Proxy, ServiceProxy, User, utcnow
from app.security import decrypt, encrypt

router = APIRouter(prefix="/api/admin", tags=["admin"])

PROTOCOLS = ("http", "https", "socks5", "socks5h")
CHECK_URL = "https://ipinfo.io/json"


class ProxyOut(BaseModel):
    id: int
    name: str | None
    protocol: str
    host: str
    port: int
    login: str | None
    has_password: bool
    ip: str | None
    country_code: str | None
    checked_at: datetime | None
    check_ok: bool | None
    check_error: str | None


class ServiceOut(BaseModel):
    service: str
    name: str
    proxy_id: int | None


class AccountOut(BaseModel):
    id: int
    provider: str
    account: str | None
    owner: str | None
    plan: str | None
    proxy_id: int | None


class ProxiesOut(BaseModel):
    proxies: list[ProxyOut]
    services: list[ServiceOut]
    accounts: list[AccountOut]


class ProxyIn(BaseModel):
    """`url`: «socks5://login:password@host:port» (the protocol by default http)."""

    url: str = Field(min_length=3, max_length=600)
    name: str | None = Field(default=None, max_length=100)


class ProxyChoice(BaseModel):
    proxy_id: int | None


def _out(p: Proxy) -> ProxyOut:
    return ProxyOut(id=p.id, name=p.name, protocol=p.protocol, host=p.host, port=p.port, login=p.login,
                    has_password=bool(p.password_enc), ip=p.ip, country_code=p.country_code, checked_at=p.checked_at,
                    check_ok=p.check_ok, check_error=p.check_error)


def parse_url(url: str) -> tuple[str, str, int, str | None, str | None]:
    raw = url.strip()
    if "://" not in raw:
        raw = "http://" + raw
    u = urlsplit(raw)
    protocol = u.scheme.lower()
    if protocol not in PROTOCOLS:
        raise HTTPException(400, f"Протокол прокси: {', '.join(PROTOCOLS)}")
    try:
        port = u.port
    except ValueError:
        port = None
    if not u.hostname or not port:
        raise HTTPException(400, "Нужен адрес вида protocol://логин:пароль@хост:порт")
    return protocol, u.hostname, port, unquote(u.username) if u.username else None, unquote(u.password) if u.password else None


@router.get("/proxies", response_model=ProxiesOut)
async def proxies(_: User = Depends(admin_user), s: AsyncSession = Depends(get_session, scope="function")):
    rows = list(await s.scalars(select(Proxy).order_by(Proxy.id)))
    bound = dict((await s.execute(select(ServiceProxy.service, ServiceProxy.proxy_id))).all())
    accounts = (await s.execute(
        select(IntegrationConnection, User.nd_username).outerjoin(User, User.id == IntegrationConnection.owner_user_id)
        .where(IntegrationConnection.retained.is_(True), IntegrationConnection.provider != "lastfm")
        .order_by(IntegrationConnection.provider, IntegrationConnection.id))).all()
    return ProxiesOut(
        proxies=[_out(p) for p in rows],
        services=[ServiceOut(service=k, name=n, proxy_id=bound.get(k)) for k, n in outbound.SERVICE_NAMES.items()],
        accounts=[AccountOut(id=c.id, provider=c.provider, account=c.account, owner=owner, plan=c.plan,
                             proxy_id=c.proxy_id) for c, owner in accounts],
    )


@router.post("/proxies", response_model=ProxyOut)
async def add_proxy(body: ProxyIn, _: User = Depends(admin_user), s: AsyncSession = Depends(get_session, scope="function")):
    protocol, host, port, login, password = parse_url(body.url)
    p = Proxy(name=(body.name or "").strip() or None, protocol=protocol, host=host, port=port, login=login,
              password_enc=encrypt(password) if password else None)
    s.add(p)
    try:
        await s.commit()
    except IntegrityError:
        raise HTTPException(409, "Такой прокси уже есть") from None
    outbound.changed()
    return _out(p)


@router.patch("/proxies/{proxy_id}", response_model=ProxyOut)
async def change_proxy(proxy_id: int, body: ProxyIn, _: User = Depends(admin_user),
                       s: AsyncSession = Depends(get_session, scope="function")):
    p = await s.get(Proxy, proxy_id)
    if p is None:
        raise HTTPException(404, "Прокси не найден")
    protocol, host, port, login, password = parse_url(body.url)
    p.name = (body.name or "").strip() or None
    p.protocol, p.host, p.port, p.login = protocol, host, port, login
    if password is not None or login is None:
        p.password_enc = encrypt(password) if password else None
    p.ip = p.country_code = p.checked_at = p.check_ok = p.check_error = None
    try:
        await s.commit()
    except IntegrityError:
        raise HTTPException(409, "Такой прокси уже есть") from None
    outbound.changed()
    return _out(p)


@router.delete("/proxies/{proxy_id}", status_code=204)
async def drop_proxy(proxy_id: int, _: User = Depends(admin_user), s: AsyncSession = Depends(get_session, scope="function")):
    await s.execute(delete(Proxy).where(Proxy.id == proxy_id))
    await s.commit()
    outbound.changed()


@router.post("/proxies/{proxy_id}/check", response_model=ProxyOut)
async def check_proxy(proxy_id: int, _: User = Depends(admin_user), s: AsyncSession = Depends(get_session, scope="function")):
    """The proxy asked for the address the services see through it, and its country."""
    import time

    p = await s.get(Proxy, proxy_id)
    if p is None:
        raise HTTPException(404, "Прокси не найден")
    url = outbound.address(p.protocol, p.host, p.port, p.login, decrypt(p.password_enc) if p.password_enc else None)
    t0 = time.monotonic()
    status, error = None, None
    try:
        async with httpx.AsyncClient(proxy=url, timeout=15) as c:
            r = await c.get(CHECK_URL)
        status = r.status_code
        r.raise_for_status()
        data = r.json()
        p.ip, p.country_code, p.check_ok, p.check_error = data.get("ip"), (data.get("country") or None), True, None
    except Exception as e:
        error = f"{type(e).__name__}: {e}"[:300]
        p.check_ok, p.check_error = False, error
    p.checked_at = utcnow()
    outbound.record(service="ipinfo", method="GET", host="ipinfo.io", path="/json", elapsed_s=time.monotonic() - t0,
                    status=status, error=error, proxy_id=p.id)
    await s.commit()
    return _out(p)


@router.put("/proxies/services/{service}", status_code=204)
async def service_proxy(service: str, body: ProxyChoice, _: User = Depends(admin_user),
                        s: AsyncSession = Depends(get_session, scope="function")):
    if service not in outbound.SERVICE_NAMES:
        raise HTTPException(404, "Нет такого сервиса")
    await s.execute(delete(ServiceProxy).where(ServiceProxy.service == service))
    if body.proxy_id is not None:
        if await s.get(Proxy, body.proxy_id) is None:
            raise HTTPException(404, "Прокси не найден")
        s.add(ServiceProxy(service=service, proxy_id=body.proxy_id))
    await s.commit()
    outbound.changed()


@router.put("/proxies/accounts/{connection_id}", status_code=204)
async def account_proxy(connection_id: int, body: ProxyChoice, _: User = Depends(admin_user),
                        s: AsyncSession = Depends(get_session, scope="function")):
    c = await s.get(IntegrationConnection, connection_id)
    if c is None:
        raise HTTPException(404, "Подключение не найдено")
    if body.proxy_id is not None and await s.get(Proxy, body.proxy_id) is None:
        raise HTTPException(404, "Прокси не найден")
    c.proxy_id = body.proxy_id
    await s.commit()
    outbound.changed()


# --- the journal ------------------------------------------------------------------------------------------------

class SummaryRow(BaseModel):
    key: str  # the service, the proxy's id or the purpose
    requests: int
    errors: int  # an error, or a status of 400 and over
    limited: int  # 429: a limit of the service reached
    avg_ms: int
    p95_ms: int
    last_at: datetime | None


class SummaryOut(BaseModel):
    hours: int
    services: list[SummaryRow]
    proxies: list[SummaryRow]
    purposes: list[SummaryRow]


async def _grouped(s: AsyncSession, column: str, since: datetime, limit: int) -> list[SummaryRow]:
    rows = (await s.execute(text(f"""
        SELECT COALESCE({column}::text, '') AS key, count(*) AS n,
               count(*) FILTER (WHERE error IS NOT NULL OR status >= 400) AS errors,
               count(*) FILTER (WHERE status = 429) AS limited,
               COALESCE(avg(elapsed_ms), 0)::int AS avg_ms,
               COALESCE(percentile_cont(0.95) WITHIN GROUP (ORDER BY elapsed_ms), 0)::int AS p95_ms,
               max(started_at) AS last_at
        FROM outbound_requests WHERE started_at >= :since
        GROUP BY 1 ORDER BY n DESC LIMIT :limit
    """), {"since": since, "limit": limit})).all()  # `column` is one of ours
    return [SummaryRow(key=r.key, requests=r.n, errors=r.errors, limited=r.limited, avg_ms=r.avg_ms, p95_ms=r.p95_ms,
                       last_at=r.last_at) for r in rows]


@router.get("/outbound/summary", response_model=SummaryOut)
async def summary(hours: int = Query(24, ge=1, le=24 * outbound.KEEP_DAYS), _: User = Depends(admin_user),
                  s: AsyncSession = Depends(get_session, scope="function")):
    await outbound.flush()  # what was just made is counted too
    since = utcnow() - timedelta(hours=hours)
    return SummaryOut(hours=hours, services=await _grouped(s, "service", since, 50),
                      proxies=await _grouped(s, "proxy_id", since, 50),
                      purposes=await _grouped(s, "purpose", since, 30))


class RequestOut(BaseModel):
    id: int
    started_at: datetime
    service: str
    method: str
    host: str
    path: str
    purpose: str | None
    auth: str | None
    connection_id: int | None
    proxy_id: int | None
    status: int | None
    error: str | None
    elapsed_ms: int
    bytes: int | None
    process: str | None


class RequestsOut(BaseModel):
    items: list[RequestOut]
    more: bool


@router.get("/outbound/requests", response_model=RequestsOut)
async def requests_list(
    service: str | None = None, outcome: Literal["ok", "error", "limited"] | None = None,
    proxy_id: int | None = None, connection_id: int | None = None, purpose: str | None = None,
    before: int | None = None, limit: int = Query(100, ge=1, le=500),
    _: User = Depends(admin_user), s: AsyncSession = Depends(get_session, scope="function"),
):
    await outbound.flush()
    q = select(OutboundRequest)
    if service:
        q = q.where(OutboundRequest.service == service)
    if outcome == "ok":
        q = q.where(OutboundRequest.error.is_(None), func.coalesce(OutboundRequest.status, 0) < 400)
    elif outcome == "error":
        q = q.where((OutboundRequest.error.is_not(None)) | (OutboundRequest.status >= 400))
    elif outcome == "limited":
        q = q.where(OutboundRequest.status == 429)
    if proxy_id is not None:
        # 0: the ones made direct
        q = q.where(OutboundRequest.proxy_id == proxy_id) if proxy_id else q.where(OutboundRequest.proxy_id.is_(None))
    if connection_id is not None:
        q = q.where(OutboundRequest.connection_id == connection_id)
    if purpose:
        q = q.where(OutboundRequest.purpose == purpose)
    if before is not None:
        q = q.where(OutboundRequest.id < before)
    rows = list(await s.scalars(q.order_by(OutboundRequest.id.desc()).limit(limit + 1)))
    return RequestsOut(items=[RequestOut.model_validate(r, from_attributes=True) for r in rows[:limit]],
                       more=len(rows) > limit)
