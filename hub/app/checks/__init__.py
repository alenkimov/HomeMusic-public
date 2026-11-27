"""Is each outside service the Hub depends on working, and answering as it used to — «Проверка сервисов» (admin).

Most of what the Hub reads it reads from services that promise nothing: YouTube through yt-dlp, Spotify's own
inner API, Yandex Music's unofficial one, the pages of hitmos and hitmoz. They change without a word. So each
thing the Hub does with them is a check (`CHECKS`: «YouTube: поиск», «Spotify: альбом», «Тексты: lrclib»), and a
check is known two ways — passive and synthetic monitoring, as the practice calls them:

- **By the real work** (`note`, source «live»): a download that went through, a catalog page read, a text found
  says the check works, with the time. Recorded where all of it passes: the catalog cache (`catalog.cache.cached`:
  what is read anew), the download sources (`Watched`, around each of `svc.providers`), the lyrics sources
  (`lyrics.providers.ask`). An answer of nothing («not found») says nothing either way. The minute's successes of
  a check are one row (`count`); each failure is a row of its own.
- **By the nightly check** (`app.checks.probes`, source «probe»): at night the worker asks for known records (a
  well-known song, its album, its text) — only what the day did not show working; «Проверить сейчас» asks for all.

**The fields**: what a service answers is read into the Hub's own models; a field the service stops sending does
not fail anything, it just stays empty (Spotify's play counts gone, and nobody told). So of each answer the paths
of its filled fields are taken (`paths`: «tracks[].isrc») and written down with when each was last there
(`ServiceShape`). A field no answer has had for a day while many answers came (`MISSING_AFTER`) is one the service
stopped sending: the check is «отвечает иначе».

A check's state (`state_of`): working, by its last result; broken — its last result a failure of the nightly
check, or three failures in a row of the real work (one failure of a download may be that one video's); between —
«сбоит». A service's is its worst check's. Results are kept `KEEP_DAYS`.
"""

from __future__ import annotations

import asyncio
import contextvars
import logging
import time
from dataclasses import dataclass, field
from datetime import timedelta
from typing import Any

from app.models import utcnow

log = logging.getLogger(__name__)

KEEP_DAYS = 90
FLUSH_S = 60.0
SHAPE_EVERY_S = 60.0  # the fields of a check's answers taken at most so often
MISSING_AFTER = (20, timedelta(hours=24))  # answers since a field was last there, and how long ago
BROKEN_AFTER = 3  # failures of the real work in a row
MAX_PATHS = 300
MAX_DEPTH = 5


@dataclass(frozen=True)
class Check:
    key: str
    service: str
    title: str


SERVICES: dict[str, str] = {
    "youtube": "YouTube", "soundcloud": "SoundCloud", "yandex": "Яндекс Музыка", "spotify": "Spotify",
    "deezer": "Deezer", "hitmos_fm": "hitmos.fm", "hitmoz": "hitmoz", "musicbrainz": "MusicBrainz",
    "lastfm": "Last.fm", "lyrics": "Тексты песен", "telegram": "Telegram",
}

CHECKS: tuple[Check, ...] = (
    Check("ytdlp.find", "youtube", "Поиск"),
    Check("ytdlp.file", "youtube", "Файл: открывается и отдаёт звук"),
    Check("youtube.pot", "youtube", "Сервер PO-токенов"),
    Check("soundcloud.find", "soundcloud", "Поиск"),
    Check("soundcloud.file", "soundcloud", "Файл: открывается и отдаёт звук"),
    Check("yandex.search", "yandex", "Поиск в каталоге"),
    Check("yandex.album", "yandex", "Альбом"),
    Check("yandex.track", "yandex", "Трек"),
    Check("yandex.find", "yandex", "Поиск файла (аккаунт с Плюсом)"),
    Check("yandex.file", "yandex", "Файл: открывается и отдаёт звук"),
    Check("spotify.search", "spotify", "Поиск"),
    Check("spotify.album", "spotify", "Альбом"),
    Check("spotify.artist", "spotify", "Исполнитель"),
    Check("spotify.track", "spotify", "Трек"),
    Check("deezer.search", "deezer", "Поиск"),
    Check("deezer.album", "deezer", "Альбом"),
    Check("deezer.track", "deezer", "Трек"),
    Check("hitmos_fm.find", "hitmos_fm", "Поиск"),
    Check("hitmos_fm.file", "hitmos_fm", "Файл: открывается и отдаёт звук"),
    Check("hitmoz.find", "hitmoz", "Поиск"),
    Check("hitmoz.page", "hitmoz", "Страницы треков и альбомов"),
    Check("hitmoz.file", "hitmoz", "Файл: открывается и отдаёт звук"),
    Check("musicbrainz.recordings", "musicbrainz", "Поиск записи"),
    Check("lastfm.api", "lastfm", "Теги и похожие"),
    Check("lyrics.lrclib", "lyrics", "lrclib.net"),
    Check("lyrics.lrcred", "lyrics", "lrc.red"),
    Check("lyrics.lyricsplus", "lyrics", "lyricsplus"),
    Check("lyrics.netease", "lyrics", "NetEase"),
    Check("lyrics.ovh", "lyrics", "lyrics.ovh"),
    Check("telegram.bot", "telegram", "Бот отвечает"),
)
BY_KEY = {c.key: c for c in CHECKS}

# a download source's checks are named by it; a re-upload of YouTube is YouTube
PROVIDER_CHECKS = {"ytdlp": "ytdlp", "yt_reupload": "ytdlp", "soundcloud": "soundcloud", "yandex": "yandex",
                   "hitmos_fm": "hitmos_fm", "hitmoz": "hitmoz"}
# a catalog's record by its cache key («sp:album:…»): the catalog, then the kind of the record
_CACHE_SOURCES = {"dz": "deezer", "sp": "spotify", "ym": "yandex", "mb": "musicbrainz", "hm": "hitmoz", "lfm": "lastfm"}
_CACHE_KINDS = {"album3": "album", "recordings": "recordings", "search": "search", "album": "album", "track": "track",
                "artist": "artist"}


def check_of_cache_key(key: str) -> str | None:
    """The check a catalog's cached record is of (None: none is kept of it)."""
    parts = key.split(":")
    if len(parts) < 2:
        return None
    src, kind = parts[0], parts[1]
    if (src == "mix" and kind.startswith("lfm_")) or src == "lfm":
        return "lastfm.api"
    if src == "hm":
        return "hitmoz.page" if kind in ("track", "album", "artist") else None
    service = _CACHE_SOURCES.get(src)
    name = _CACHE_KINDS.get(kind)
    key_ = f"{service}.{name}" if service and name else None
    return key_ if key_ in BY_KEY else None


# --- the fields of an answer ------------------------------------------------------------------------------------

def _filled(v: Any) -> bool:
    return v is not None and v != "" and v != [] and v != {}


def paths(value: Any, prefix: str = "", depth: int = 0, out: set[str] | None = None) -> set[str]:
    """The paths of the filled fields of an answer: «title», «tracks[].isrc», «artists[].ref». A list is all its
    items at once (a field of one of them is the list's)."""
    out = set() if out is None else out
    if depth > MAX_DEPTH or len(out) >= MAX_PATHS:
        return out
    if isinstance(value, dict):
        for k, v in value.items():
            if not _filled(v):
                continue
            p = f"{prefix}.{k}" if prefix else str(k)
            out.add(p)
            paths(v, p, depth + 1, out)
    elif isinstance(value, list):
        p = f"{prefix}[]"
        for item in value[:50]:
            if isinstance(item, (dict, list)):
                paths(item, p, depth + 1, out)
    return out


# --- what the work found, gathered and written down every minute -------------------------------------------------

_probing: contextvars.ContextVar[bool] = contextvars.ContextVar("checks_probing", default=False)


@dataclass
class _Ok:
    count: int = 0
    elapsed_ms: int | None = None
    detail: str | None = None


@dataclass
class _Shape:
    paths: set[str] = field(default_factory=set)
    seen: int = 0
    taken_at: float = 0.0


_oks: dict[str, _Ok] = {}
_fails: list[dict[str, Any]] = []
_shapes: dict[str, _Shape] = {}
_task: asyncio.Task | None = None


def note(check: str, ok: bool, detail: str | None = None, elapsed_ms: int | None = None) -> None:
    """A result of the real work. The nightly check writes its own (`probes`), and what it asks is not noted twice."""
    if check not in BY_KEY or _probing.get():
        return
    if ok:
        o = _oks.setdefault(check, _Ok())
        o.count += 1
        o.elapsed_ms = elapsed_ms if elapsed_ms is not None else o.elapsed_ms
        o.detail = detail or o.detail
    elif len(_fails) < 500:
        _fails.append({"check": check, "at": utcnow(), "ok": False, "source": "live",
                       "detail": (detail or "ошибка")[:300], "elapsed_ms": elapsed_ms, "count": 1})


def observe(check: str, value: Any) -> None:
    """The fields of an answer of the check (taken at most once a `SHAPE_EVERY_S`; every answer is counted)."""
    if check not in BY_KEY:
        return
    sh = _shapes.setdefault(check, _Shape())
    now = time.monotonic()
    if now - sh.taken_at < SHAPE_EVERY_S and sh.seen:
        return
    sh.taken_at = now
    sh.seen += 1
    try:
        sh.paths |= paths(value)
    except Exception as e:  # never in the way of the work
        log.debug("checks: the fields of %s not taken: %r", check, e)


def error_words(e: BaseException) -> str:
    return (str(e) or type(e).__name__)[:300]


async def flush() -> None:
    """What was gathered, into the database."""
    from sqlalchemy import select

    from app.db import session_scope
    from app.models import ServiceCheck, ServiceShape

    oks, fails, shapes = dict(_oks), list(_fails), dict(_shapes)
    _oks.clear()
    _fails.clear()
    taken = {k: (set(v.paths), v.seen) for k, v in shapes.items() if v.seen}
    for k in taken:
        shapes[k].seen, shapes[k].paths = 0, set()
    if not (oks or fails or taken):
        return
    now = utcnow()
    try:
        async with session_scope() as s:
            for check, o in oks.items():
                s.add(ServiceCheck(check=check, at=now, ok=True, source="live", detail=o.detail, elapsed_ms=o.elapsed_ms,
                                   count=o.count))
            for row in fails:
                s.add(ServiceCheck(**row))
            if taken:
                have = {r.check: r for r in (await s.execute(select(ServiceShape).where(ServiceShape.check.in_(taken)))).scalars()}
                for check, (found, seen) in taken.items():
                    row = have.get(check) or ServiceShape(check=check, seen=0, paths={})
                    if check not in have:
                        s.add(row)
                    row.seen = (row.seen or 0) + seen
                    merged = dict(row.paths or {})
                    for p in found:
                        merged[p] = [now.isoformat(), row.seen]
                    row.paths = merged
                    row.updated_at = now
            await s.commit()
    except Exception as e:
        log.warning("checks: %s results not written: %r", len(oks) + len(fails), e)


async def _loop() -> None:
    while True:
        await asyncio.sleep(FLUSH_S)
        await flush()


def start() -> None:
    global _task
    if _task is None:
        _task = asyncio.create_task(_loop(), name="checks-flush")


async def stop() -> None:
    global _task
    if _task is not None:
        _task.cancel()
        await asyncio.gather(_task, return_exceptions=True)
        _task = None
    await flush()


# --- the download sources, watched ------------------------------------------------------------------------------

class Watched:
    """A download source as it is, each search, download and opening of a file noted (`note`) on the way. Anything
    else is the source's own."""

    def __init__(self, inner: Any, prefix: str) -> None:
        self.inner = inner
        self.name = inner.name
        self._prefix = prefix

    def __getattr__(self, attr: str) -> Any:
        return getattr(self.inner, attr)

    async def search(self, q: Any, limit: int = 10) -> Any:
        t0 = time.monotonic()
        try:
            found = await self.inner.search(q, limit=limit)
        except Exception as e:
            note(f"{self._prefix}.find", False, error_words(e), _ms(t0))
            raise
        if found:  # nothing found tells nothing
            note(f"{self._prefix}.find", True, None, _ms(t0))
            observe(f"{self._prefix}.find", [c.model_dump(mode="json") for c in found[:10]])
        return found

    async def fetch(self, c: Any, dest_dir: Any, *args: Any, **kwargs: Any) -> Any:
        t0 = time.monotonic()
        try:
            path = await self.inner.fetch(c, dest_dir, *args, **kwargs)
        except Exception as e:
            note(f"{self._prefix}.file", False, error_words(e), _ms(t0))
            raise
        note(f"{self._prefix}.file", True, None, _ms(t0))
        return path

    async def stream(self, c: Any) -> Any:
        t0 = time.monotonic()
        try:
            source = await self.inner.stream(c)
        except Exception as e:
            note(f"{self._prefix}.file", False, error_words(e), _ms(t0))
            raise
        note(f"{self._prefix}.file", True, None, _ms(t0))
        return source


def watch(provider: Any) -> Any:
    prefix = PROVIDER_CHECKS.get(provider.name)
    return Watched(provider, prefix) if prefix else provider


def _ms(t0: float) -> int:
    return int((time.monotonic() - t0) * 1000)
