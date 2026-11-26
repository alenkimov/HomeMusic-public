"""The Hub's requests to outside services: which proxy each goes through, and the journal of them.

**Proxies** (`proxies`): of an account (`integration_connections.proxy_id` — the requests made with it), else of the
whole service (`service_proxies`), else none: direct. A request is of a service by its host (`SERVICES`); of an
account while the code runs with it (`account`: set by `app.integrations.credential` when an account's secret is
read, so the requests that follow are its). What is set is read from the database into memory (`refresh`): at
start, every `REFRESH_S`, and at once on a change (the other process is told: `app.relay`, «proxies»).

**The journal** (`outbound_requests`): one row a request — the service, method, host, path with the names of its
query (never the values: tokens and keys travel there), what it was made for (`purpose`: the job, the page, the
loop), the account or key, the proxy, the answer's status or the error, how long it took. Written in batches every
`FLUSH_S` (a row a request would load the database the journal is to watch), kept `KEEP_DAYS`.

The ways out, each of them here:
- httpx (most services: `app.net.make_client`) — `RoutingTransport` picks the proxy of every request and writes it;
- `requests` (the Yandex Music client, in this process) — `install_requests_hook`;
- yt-dlp, the Spotify session (another process), Telegram — the proxy from `proxy_url`, one row a call (`record`).
Requests to the Hub's own services (Navidrome, the embedder, the PO-token server) are none of this.
"""

from __future__ import annotations

import asyncio
import contextvars
import ipaddress
import logging
import re
import time
from collections import deque
from contextlib import contextmanager
from dataclasses import dataclass, field
from datetime import timedelta
from typing import Any
from urllib.parse import parse_qsl, quote, urlsplit

import httpx

log = logging.getLogger(__name__)

FLUSH_S = 3.0
KEEP_DAYS = 14
REFRESH_S = 30.0
MAX_BUFFER = 20_000

# a service: the hosts it is reached at (exact, or «*.» — the host and every name under it). The first that fits.
SERVICES: dict[str, tuple[str, ...]] = {
    "yandex": ("*.music.yandex.net", "music.yandex.ru", "*.music.yandex.ru", "oauth.yandex.ru", "ya.ru",
               "*.storage.yandex.net", "*.yandex.net", "*.yandex.ru"),
    "spotify": ("*.spotify.com", "*.spotifycdn.com", "*.scdn.co"),
    "deezer": ("*.deezer.com", "*.dzcdn.net"),
    "lastfm": ("ws.audioscrobbler.com", "*.last.fm", "*.lastfm.freetls.fastly.net"),
    "musicbrainz": ("*.musicbrainz.org", "musicbrainz.org", "coverartarchive.org", "*.coverartarchive.org", "*.archive.org"),
    "listenbrainz": ("*.listenbrainz.org",),
    "vk": ("*.vk.com", "vk.com", "*.vk.ru", "*.userapi.com", "*.vkuser.net"),
    "youtube": ("*.youtube.com", "youtube.com", "youtu.be", "*.googlevideo.com", "*.ytimg.com", "*.ggpht.com"),
    "soundcloud": ("*.soundcloud.com", "soundcloud.com", "*.sndcdn.com"),
    "hitmos_fm": ("*.hitmos.fm",),
    "hitmoz": ("*.hitmoz.org", "*.hitmotop.com", "hitmos.me", "*.hitmos.me"),
    "ai": ("openrouter.ai", "*.openrouter.ai", "api.openai.com", "api.anthropic.com", "generativelanguage.googleapis.com"),
    "google": ("*.googleapis.com", "accounts.google.com", "*.google.com", "*.googleusercontent.com"),
    "telegram": ("api.telegram.org",),
    "lyrics": ("lrclib.net", "*.lrclib.net", "lrc.red", "*.lrc.red", "music.163.com", "*.music.163.com",
               "api.lyrics.ovh", "lyricsplus.prjktla.my.id", "api.mymemory.translated.net"),
    "ipinfo": ("ipinfo.io",),
}
# what the admin page offers to give a proxy of its own
SERVICE_NAMES = {
    "yandex": "Яндекс Музыка", "spotify": "Spotify", "deezer": "Deezer", "lastfm": "Last.fm",
    "musicbrainz": "MusicBrainz", "listenbrainz": "ListenBrainz", "vk": "ВКонтакте", "youtube": "YouTube",
    "soundcloud": "SoundCloud", "hitmos_fm": "hitmos.fm", "hitmoz": "hitmoz", "ai": "ИИ", "google": "Google",
    "telegram": "Telegram", "lyrics": "Тексты песен", "ipinfo": "ipinfo.io",
}
_SECRET_NAMES = ("token", "key", "secret", "password", "sign", "signature", "auth", "session", "sig", "code")


def service_of(host: str | None) -> str | None:
    """The service of a host; None: the Hub's own (a name without a dot, a private address) — not journaled."""
    h = (host or "").lower().rstrip(".")
    if not h or "." not in h or h in ("host.docker.internal",):
        return None
    try:
        if not ipaddress.ip_address(h).is_global:
            return None
    except ValueError:
        pass
    for service, patterns in SERVICES.items():
        for p in patterns:
            if (p.startswith("*.") and (h == p[2:] or h.endswith(p[1:]))) or h == p:
                return service
    parts = h.split(".")
    return ".".join(parts[-2:])[:32]  # another: its domain


def clean_path(url: str) -> str:
    """The path and the names of the query, without their values."""
    u = urlsplit(url)
    names = list(dict.fromkeys(k for k, _ in parse_qsl(u.query, keep_blank_values=True)))
    path = quote(u.path or "/", safe="/:@-._~!$&'()*+,;=%")
    return (path + ("?" + "&".join(names) if names else ""))[:500]


# --- what the code runs for, and with ---------------------------------------------------------------------------

_purpose: contextvars.ContextVar[str | None] = contextvars.ContextVar("hm_purpose", default=None)
_account: contextvars.ContextVar[int | None] = contextvars.ContextVar("hm_account", default=None)


@contextmanager
def purpose(what: str):
    token = _purpose.set(what[:100])
    try:
        yield
    finally:
        _purpose.reset(token)


def set_purpose(what: str | None) -> None:
    _purpose.set(what[:100] if what else None)


def use_account(connection_id: int | None) -> None:
    """The requests that follow, in this task, are made with this account (`integrations.credential`)."""
    _account.set(connection_id)


@contextmanager
def account(connection_id: int | None):
    token = _account.set(connection_id)
    try:
        yield
    finally:
        _account.reset(token)


def current_account() -> int | None:
    return _account.get()


_ID_SEGMENT = re.compile(r"^(?=.*[0-9:]).+$")


class PurposeMiddleware:
    """A request to the Hub's API: its outside requests were made for it — «GET /api/catalog/artist/{}»."""

    def __init__(self, app):
        self.app = app

    async def __call__(self, scope, receive, send):
        if scope["type"] == "http" and scope["path"].startswith("/api/"):
            path = "/".join("{}" if _ID_SEGMENT.match(p) else p for p in scope["path"].split("/"))
            token = _purpose.set(f"{scope['method']} {path}"[:100])
            try:
                return await self.app(scope, receive, send)
            finally:
                _purpose.reset(token)
        return await self.app(scope, receive, send)


# --- the proxies, as set ----------------------------------------------------------------------------------------

@dataclass
class _Set:
    urls: dict[int, str] = field(default_factory=dict)  # proxy -> its address with the login
    services: dict[str, int] = field(default_factory=dict)
    accounts: dict[int, int] = field(default_factory=dict)


_set = _Set()
_refreshed_at = 0.0


def proxy_url(service: str | None, connection_id: int | None = None) -> tuple[int | None, str | None]:
    """(the proxy's id, its address) for a request to `service`, made with the account `connection_id` (by default
    the current one); (None, None): direct."""
    cid = connection_id if connection_id is not None else _account.get()
    pid = _set.accounts.get(cid) if cid is not None else None
    if pid is None and service is not None:
        pid = _set.services.get(service)
    url = _set.urls.get(pid) if pid is not None else None
    return (pid, url) if url else (None, None)


def address(protocol: str, host: str, port: int, login: str | None, password: str | None) -> str:
    auth = f"{quote(login, safe='')}:{quote(password or '', safe='')}@" if login else ""
    return f"{protocol}://{auth}{host}:{port}"


async def refresh() -> None:
    """What is set, read anew."""
    global _set, _refreshed_at
    from sqlalchemy import select

    from app.db import session_scope
    from app.models import IntegrationConnection, Proxy, ServiceProxy
    from app.security import decrypt

    async with session_scope() as s:
        proxies = list(await s.scalars(select(Proxy)))
        services = (await s.execute(select(ServiceProxy.service, ServiceProxy.proxy_id))).all()
        accounts = (await s.execute(select(IntegrationConnection.id, IntegrationConnection.proxy_id)
                                    .where(IntegrationConnection.proxy_id.is_not(None)))).all()
    urls = {}
    for p in proxies:
        try:
            password = decrypt(p.password_enc) if p.password_enc else None
        except Exception:
            log.warning("proxy %s: its password is not read", p.id)
            continue
        urls[p.id] = address(p.protocol, p.host, p.port, p.login, password)
    _set = _Set(urls=urls, services=dict(services), accounts=dict(accounts))
    _refreshed_at = time.monotonic()


def changed() -> None:
    """Something was set: read anew here, and in the other process."""
    from app import relay

    relay.send("proxies")
    _later(refresh())


def _later(work) -> None:
    task = asyncio.get_running_loop().create_task(work)
    _tasks.add(task)
    task.add_done_callback(_tasks.discard)


# --- the journal ------------------------------------------------------------------------------------------------

_buffer: deque[dict[str, Any]] = deque(maxlen=MAX_BUFFER)
_tasks: set[asyncio.Task] = set()
_running: list[asyncio.Task] = []


def record(*, service: str, method: str, host: str, path: str, elapsed_s: float, status: int | None = None,
           error: str | None = None, size: int | None = None, proxy_id: int | None = None,
           auth: str | None = None, connection_id: int | None = None) -> None:
    """One request into the journal (written with the next batch). Thread-safe: `requests` calls it from threads."""
    from app.models import utcnow
    from app.relay import role

    cid = connection_id if connection_id is not None else _account.get()
    now = utcnow()
    _buffer.append({
        "started_at": now - timedelta(seconds=elapsed_s),
        "service": service[:32], "method": method[:10], "host": host[:255], "path": path[:500],
        "purpose": _purpose.get(), "auth": "account" if cid is not None else auth, "connection_id": cid,
        "proxy_id": proxy_id, "status": status, "error": error[:300] if error else None,
        "elapsed_ms": max(0, int(elapsed_s * 1000)), "bytes": size, "process": role(),
    })


def _keyed(headers: Any, url: str) -> str | None:
    """The request carries a key or a token of its own (an API key, not an account of the Hub's)."""
    if any(h in headers for h in ("authorization", "x-api-key", "api-key")):
        return "key"
    query = urlsplit(url).query.lower()
    return "key" if any(f"{n}=" in query for n in ("api_key", "apikey", "key", "token", "access_token")) else None


class RoutingTransport(httpx.AsyncBaseTransport):
    """httpx's transport that sends each request through the proxy set for it, or direct, and journals it."""

    def __init__(self, *, http2: bool = True, **kwargs: Any):
        self.http2, self.kwargs = http2, kwargs
        self._by_proxy: dict[str | None, httpx.AsyncHTTPTransport] = {}

    def _transport(self, url: str | None) -> httpx.AsyncHTTPTransport:
        t = self._by_proxy.get(url)
        if t is None:
            t = self._by_proxy[url] = httpx.AsyncHTTPTransport(proxy=url, http2=self.http2, **self.kwargs)
        return t

    async def handle_async_request(self, request: httpx.Request) -> httpx.Response:
        service = service_of(request.url.host)
        if service is None:
            return await self._transport(None).handle_async_request(request)
        pid, url = proxy_url(service)
        t0 = time.monotonic()
        common = {"service": service, "method": request.method, "host": request.url.host,
                  "path": clean_path(str(request.url)), "proxy_id": pid, "auth": _keyed(request.headers, str(request.url))}
        try:
            response = await self._transport(url).handle_async_request(request)
        except Exception as e:
            record(**common, elapsed_s=time.monotonic() - t0, error=f"{type(e).__name__}: {e}"[:300])
            raise
        size = response.headers.get("content-length")
        record(**common, elapsed_s=time.monotonic() - t0, status=response.status_code,
               size=int(size) if size and size.isdigit() else None)
        return response

    async def aclose(self) -> None:
        for t in self._by_proxy.values():
            await t.aclose()
        self._by_proxy.clear()


_requests_hooked = False


def install_requests_hook() -> None:
    """`requests` (the Yandex Music client) journaled as httpx is. Its proxy is the client's own (`proxy_url`)."""
    global _requests_hooked
    if _requests_hooked:
        return
    import requests

    send = requests.Session.send

    def journaled(session, req, **kwargs):
        u = urlsplit(req.url)
        service = service_of(u.hostname)
        if service is None:
            return send(session, req, **kwargs)
        proxies = kwargs.get("proxies") or session.proxies or {}
        used = proxies.get(u.scheme) or proxies.get("all")
        pid = next((i for i, a in _set.urls.items() if a == used), None) if used else None
        t0 = time.monotonic()
        common = {"service": service, "method": req.method or "GET", "host": u.hostname or "",
                  "path": clean_path(req.url), "proxy_id": pid, "auth": _keyed({k.lower() for k in req.headers}, req.url)}
        try:
            response = send(session, req, **kwargs)
        except Exception as e:
            record(**common, elapsed_s=time.monotonic() - t0, error=f"{type(e).__name__}: {e}"[:300])
            raise
        size = response.headers.get("content-length")
        record(**common, elapsed_s=time.monotonic() - t0, status=response.status_code,
               size=int(size) if size and size.isdigit() else None)
        return response

    requests.Session.send = journaled
    _requests_hooked = True


async def flush() -> int:
    if not _buffer:
        return 0
    rows = []
    while _buffer and len(rows) < 2000:
        rows.append(_buffer.popleft())
    from sqlalchemy import insert

    from app.db import session_scope
    from app.models import OutboundRequest

    try:
        async with session_scope() as s:
            await s.execute(insert(OutboundRequest), rows)
    except Exception as e:
        log.warning("outbound: %s requests not journaled: %r", len(rows), e)
        return 0
    return len(rows)


async def sweep() -> int:
    from sqlalchemy import text

    from app.db import session_scope
    from app.models import utcnow

    async with session_scope() as s:
        r = await s.execute(text("DELETE FROM outbound_requests WHERE started_at < :old"),
                            {"old": utcnow() - timedelta(days=KEEP_DAYS)})
    return r.rowcount or 0


async def _loop(sweeping: bool) -> None:
    swept_at = time.monotonic()
    while True:
        await asyncio.sleep(FLUSH_S)
        while await flush() >= 2000:
            pass
        if time.monotonic() - _refreshed_at > REFRESH_S:
            try:
                await refresh()
            except Exception as e:
                log.info("outbound: proxies not read: %r", e)
        if sweeping and time.monotonic() - swept_at > 3600:
            swept_at = time.monotonic()
            try:
                if n := await sweep():
                    log.info("outbound: %s old requests let go", n)
            except Exception as e:
                log.info("outbound: sweep failed: %r", e)


async def start(*, sweeping: bool) -> None:
    install_requests_hook()
    try:
        await refresh()
    except Exception as e:
        log.warning("outbound: proxies not read: %r", e)
    _running.append(asyncio.create_task(_loop(sweeping), name="outbound"))


async def stop() -> None:
    for t in _running:
        t.cancel()
    await asyncio.gather(*_running, return_exceptions=True)
    _running.clear()
    await flush()


def _relayed(msg: dict[str, Any]) -> None:
    _later(refresh())


def _register() -> None:
    from app import relay

    relay.on("proxies")(_relayed)


_register()
