"""Yandex Music as a source of files: the official file of a track, through an account with Яндекс Плюс.

The account is one connected in «Интеграции» (`integration_connections`, provider `yandex`) with the plan `plus` —
one shared with everyone first, else the newest. Without one the source finds nothing (an account without Plus is
given 30-second previews only — such a file would not pass the check of its length anyway).

A track of Yandex (`TrackQuery.yandex_id`: the catalog's record is Yandex's) is taken by its id; any other is looked
for by its names, as every source is, and ranked by `app.matching` (the length, the names). The file: lossless
(FLAC) where Yandex has it, else MP3 320 (`get-file-info`, transport `raw`: not encrypted).

Yandex replaces the audio of its own tracks in place since the 2026 law (the same id, the same date), so a copy of
Yandex is never trusted to be the original of an explicit Russian track (`extra.source` "yandex",
`app.matching.select.trusted_original`): such a track takes it only when no copy known to be uncensored is found.

Unofficial and not liked by Yandex: an account that downloads thousands of tracks in a row may be limited. So the
files come slowly — `MAX_PARALLEL` at once, `GAP_S` between two.
"""

from __future__ import annotations

import asyncio
import logging
import time
from pathlib import Path
from typing import Any

from app.net import RateLimiter, make_client
from app.providers.base import Candidate, ProviderError, StreamSource, TrackQuery

log = logging.getLogger(__name__)

MAX_PARALLEL = 2
GAP_S = 2.0  # between two files
SEARCH_PER_S = 2.0
TOKEN_TTL_S = 600  # the account is looked up again this often (connected, taken away)
CODECS = ["flac", "mp3"]  # what the library keeps as it is: no MP4 container, no HE-AAC
TIMEOUT_S = 60


async def plus_token() -> tuple[int, str] | None:
    """(the account, its token) of a connected Yandex account with Plus: a shared one first, then the newest."""
    from sqlalchemy import select

    from app import integrations
    from app.db import session_scope
    from app.models import IntegrationConnection as C

    async with session_scope() as s:
        rows = list(await s.scalars(select(C).where(
            C.provider == "yandex", C.state == "connected", C.plan == "plus", C.retained.is_(True),
        ).order_by(C.shared.desc(), C.connected_at.desc(), C.id.desc())))
        for row in rows:
            if token := await integrations.credential(s, row.id):
                return row.id, token
    return None


def _explicit(t: Any) -> bool | None:
    warning = getattr(t, "content_warning", None)
    return True if warning == "explicit" or getattr(t, "explicit", None) else False if warning == "clean" else None


def _candidate(t: Any) -> Candidate | None:
    if t is None or getattr(t, "available", True) is False or getattr(t, "type", None) not in (None, "music"):
        return None
    title = (t.title or "").strip()
    if not title:
        return None
    if version := (getattr(t, "version", None) or "").strip():
        title = f"{title} ({version})"
    artists = [a.name for a in (t.artists or []) if getattr(a, "name", None)]
    album = (t.albums or [None])[0]
    ms = getattr(t, "duration_ms", None)
    album_id = getattr(album, "id", None)
    return Candidate(
        provider="yandex", provider_id=str(t.id), artist=", ".join(artists), title=title,
        duration_s=round(ms / 1000) if ms else None, bitrate_kbps=320, explicit=_explicit(t),
        page_url=f"https://music.yandex.ru/album/{album_id}/track/{t.id}" if album_id else f"https://music.yandex.ru/track/{t.id}",
        album=getattr(album, "title", None), year=getattr(album, "year", None),
        extra={"source": "yandex"},
    )


class YandexProvider:
    name = "yandex"

    def __init__(self) -> None:
        self._token: str | None = None
        self._account: int | None = None
        self._proxy: str | None = None
        self._token_at: float | None = None  # when the account was looked up (None: not yet)
        self._client: Any = None
        self._lock = asyncio.Lock()
        self._files = asyncio.Semaphore(MAX_PARALLEL)
        self._gap = RateLimiter(1 / GAP_S)
        self._search = RateLimiter(SEARCH_PER_S)
        self.http = make_client()

    async def _signed_in(self) -> Any | None:
        """The client of the account with Plus (None: there is none)."""
        async with self._lock:
            if self._token_at is None or time.monotonic() - self._token_at > TOKEN_TTL_S:
                found = await plus_token()
                self._token_at = time.monotonic()
                account, token = found if found else (None, None)
                if token != self._token:
                    self._token, self._account, self._client = token, account, None
            from app.outbound import proxy_url, use_account

            use_account(self._account)  # what follows is the account's: its proxy, its rows of the journal
            _, proxy = proxy_url("yandex", self._account)
            if proxy != self._proxy:  # the proxy was changed: a client of the new one
                self._proxy, self._client = proxy, None
            if self._token and self._client is None:
                from app.catalog.yandex import yandex_client

                self._client = yandex_client(self._token, self._account)
            return self._client

    async def search(self, q: TrackQuery, limit: int = 10) -> list[Candidate]:
        client = await self._signed_in()
        if client is None:
            return []
        await self._search.wait()
        try:
            if q.yandex_id:
                found = await asyncio.to_thread(client.tracks, [q.yandex_id])
                return [c for t in found or [] if (c := _candidate(t))]
            text = " ".join(f"{q.artist} {q.title}".split())
            if not text:
                return []
            res = await asyncio.to_thread(client.search, text, type_="track")
        except Exception as e:
            raise ProviderError(f"Яндекс Музыка: {e!r}") from e
        tracks = res.tracks.results if res is not None and res.tracks is not None else []
        return [c for t in tracks[:limit] if (c := _candidate(t))]

    async def enrich(self, c: Candidate) -> Candidate:
        return c.model_copy(update={"enriched": True})

    async def _file(self, c: Candidate) -> Any:
        client = await self._signed_in()
        if client is None:
            raise ProviderError("Яндекс Музыка: нет подключённого аккаунта с Плюсом")
        try:
            info = await asyncio.to_thread(client.tracks_file_info, c.provider_id, "lossless", CODECS, "raw")
        except Exception as e:
            raise ProviderError(f"Яндекс Музыка: файл не выдан: {e!r}") from e
        file = getattr(info, "download_info", None)
        if file is None or not file.url:
            raise ProviderError("Яндекс Музыка: файл не выдан")
        return file

    async def fetch(self, c: Candidate, dest_dir: Path, on_progress: Any = None) -> Path:
        async with self._files:
            await self._gap.wait()
            file = await self._file(c)
            ext = "flac" if file.codec.startswith("flac") else "mp3" if file.codec == "mp3" else "m4a"
            dest = Path(dest_dir) / f"yandex_{c.provider_id}.{ext}"
            part = dest.with_name(dest.name + ".part")
            try:
                await asyncio.to_thread(file.download, str(part), TIMEOUT_S)
                if not part.is_file() or part.stat().st_size < 64 * 1024:
                    raise ProviderError("Яндекс Музыка: файл пустой")
                part.replace(dest)
            except ProviderError:
                raise
            except Exception as e:
                raise ProviderError(f"Яндекс Музыка: ошибка при скачивании: {e!r}") from e
            finally:
                part.unlink(missing_ok=True)
            log.info("yandex: %s %s %s kbps -> %s", c.provider_id, file.codec, file.bitrate, dest.name)
            return dest

    async def stream(self, c: Candidate) -> StreamSource:
        file = await self._file(c)
        media = "audio/flac" if file.codec.startswith("flac") else "audio/mpeg" if file.codec == "mp3" else "audio/mp4"
        return StreamSource(url=file.url, client=self.http, media_type=media)

    async def aclose(self) -> None:
        await self.http.aclose()
