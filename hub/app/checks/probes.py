"""The nightly check of the outside services (`app.checks`): the worker asks each for a well-known record — «Кино —
Группа крови», its album, its artist, its text, the first half a megabyte of its audio — the very way the Hub's work
asks (the same clients and sources, their own proxies and accounts), only anew, past the cache.

Only what the day did not show working is asked (`run`): a check with a result of the real work in the last day is
left alone, so a Hub in use asks almost nothing at night. «Проверить сейчас» (`force`) asks for everything. A
service the Hub has no use of here (no account, not set up) is not asked (`unavailable`): «не подключён».
"""

from __future__ import annotations

import asyncio
import logging
import os
import time
from collections.abc import Awaitable, Callable
from datetime import datetime, timedelta
from typing import Any
from zoneinfo import ZoneInfo

from sqlalchemy import delete, func, select

from app import checks
from app.jobs.queue import handler
from app.models import AppSetting, Job, ServiceCheck, utcnow

log = logging.getLogger(__name__)

JOB = "service_checks"
STATUS_KEY = "checks:status"
HOUR = 4  # local time (`Settings.timezone`)
FRESH_FOR = timedelta(hours=24)  # a check worked this recently by the real work: not asked at night
GROUP_TIMEOUT_S = 180
FIRST_BYTES = 512 * 1024
ENOUGH_BYTES = 64 * 1024
REF_ARTIST, REF_TITLE = "Кино", "Группа крови"
LYRICS_SOURCES = ("lrclib", "lrcred", "lyricsplus", "netease", "ovh")
LYRICS_SPARE = ("Queen", "Bohemian Rhapsody", "A Night at the Opera", 355)  # artist, title, album, seconds


class Recorder:
    """What one group found, written at once; only the checks this run is for (`todo`)."""

    def __init__(self, todo: set[str]) -> None:
        self.todo = todo
        self.done: set[str] = set()

    async def __call__(self, check: str, ok: bool, detail: str | None = None, t0: float | None = None) -> None:
        if check not in self.todo or check in self.done:
            return
        self.done.add(check)
        from app.db import session_scope

        elapsed = int((time.monotonic() - t0) * 1000) if t0 is not None else None
        async with session_scope() as s:
            s.add(ServiceCheck(check=check, at=utcnow(), ok=ok, source="probe", detail=(detail or None) and detail[:300],
                               elapsed_ms=elapsed))
            await s.commit()

    async def failed(self, checks_: tuple[str, ...], why: str) -> None:
        for c in checks_:
            await self(c, False, why)


# --- what is asked --------------------------------------------------------------------------------------------

async def _first_bytes(source: Any) -> int:
    """The first `FIRST_BYTES` of a file opened by a source (`StreamSource`): how many came."""
    headers = {**(source.headers or {}), "Range": f"bytes=0-{FIRST_BYTES - 1}"}
    got = 0
    async with source.client.stream("GET", source.url, headers=headers) as r:
        if r.status_code not in (200, 206):
            raise RuntimeError(f"файл ответил HTTP {r.status_code}")
        async for chunk in r.aiter_raw():
            got += len(chunk)
            if got >= FIRST_BYTES:
                break
    return got


async def _audio(inner: Any, c: Any) -> tuple[int, str]:
    """The audio of a found file: its first `FIRST_BYTES` where the source opens it for listening, else the whole
    file downloaded into a folder of its own and thrown away (SoundCloud gives its sound only so)."""
    import shutil
    import tempfile
    from pathlib import Path

    try:
        source = await inner.stream(c)
    except Exception:
        source = None
    if source is not None:
        got = await _first_bytes(source)
        return got, f"{got // 1024} КБ звука"
    folder = Path(tempfile.mkdtemp(prefix="hm-check-"))
    try:
        path = await inner.fetch(c, folder)
        size = (await asyncio.to_thread(path.stat)).st_size
        return size, f"файл скачан, {size // 1024} КБ"
    finally:
        await asyncio.to_thread(shutil.rmtree, folder, True)


def _provider(name: str, prefix: str) -> Callable[[Recorder], Awaitable[None]]:
    async def probe(rec: Recorder) -> None:
        from app.providers.base import TrackQuery
        from app.services import svc

        source = svc.providers.get(name)
        inner = getattr(source, "inner", source)
        t0 = time.monotonic()
        try:
            found = await inner.search(TrackQuery(artist=REF_ARTIST, title=REF_TITLE), limit=5)
        except Exception as e:
            await rec(f"{prefix}.find", False, checks.error_words(e), t0)
            await rec.failed((f"{prefix}.file", f"{prefix}.page"), "не проверить: поиск не работает")
            return
        if not found:
            await rec(f"{prefix}.find", False, f"по запросу «{REF_ARTIST} — {REF_TITLE}» ничего не нашлось", t0)
            await rec.failed((f"{prefix}.file", f"{prefix}.page"), "не проверить: поиск ничего не нашёл")
            return
        await rec(f"{prefix}.find", True, f"нашлось {len(found)}", t0)
        checks.observe(f"{prefix}.find", [c.model_dump(mode="json") for c in found])
        if f"{prefix}.page" in rec.todo:  # hitmoz: the track's page
            t0 = time.monotonic()
            try:
                page = await inner.enrich(found[0])
                await rec(f"{prefix}.page", bool(page), None if page else "страница трека пуста", t0)
            except Exception as e:
                await rec(f"{prefix}.page", False, checks.error_words(e), t0)
        if f"{prefix}.file" not in rec.todo:
            return
        why = "файл не открылся"
        for c in found[:3]:
            t0 = time.monotonic()
            try:
                got, how = await _audio(inner, c)
            except Exception as e:
                why = checks.error_words(e)
                continue
            if got >= ENOUGH_BYTES:
                await rec(f"{prefix}.file", True, how, t0)
                return
            why = f"пришло только {got} байт"
        await rec(f"{prefix}.file", False, why)

    return probe


async def _pot(rec: Recorder) -> None:
    from app.net import make_client

    base = os.environ.get("YTDLP_POT_BASE_URL", "").rstrip("/")
    t0 = time.monotonic()
    try:
        async with make_client(timeout=10) as c:
            r = await c.get(f"{base}/ping")
        await rec("youtube.pot", r.status_code == 200, None if r.status_code == 200 else f"HTTP {r.status_code}", t0)
    except Exception as e:
        await rec("youtube.pot", False, checks.error_words(e), t0)


_reference: dict[str, Any] = {}  # the known song as a catalog told it: the lyrics are asked with its album and length


def _catalog(service: str) -> Callable[[Recorder], Awaitable[None]]:
    async def probe(rec: Recorder) -> None:
        from app.services import svc

        t0 = time.monotonic()
        try:
            if service == "deezer":
                tracks = await svc.deezer.search_tracks(REF_ARTIST, REF_TITLE)
            elif service == "spotify":
                tracks = (await svc.spotify.search(f"{REF_ARTIST} {REF_TITLE}", kinds=("track",), limit=5)).tracks
            else:
                tracks = (await svc.yandex.search(f"{REF_ARTIST} {REF_TITLE}")).tracks
        except Exception as e:
            await rec(f"{service}.search", False, checks.error_words(e), t0)
            await rec.failed((f"{service}.album", f"{service}.track", f"{service}.artist"), "не проверить: поиск не работает")
            return
        if not tracks:
            await rec(f"{service}.search", False, f"«{REF_ARTIST} — {REF_TITLE}» не нашлось", t0)
            await rec.failed((f"{service}.album", f"{service}.track", f"{service}.artist"), "не проверить: поиск ничего не нашёл")
            return
        await rec(f"{service}.search", True, f"нашлось {len(tracks)}", t0)
        t = tracks[0]
        _reference.setdefault("album", t.album_title)
        _reference.setdefault("duration_s", t.duration_s)
        _reference.setdefault("isrc", getattr(t, "isrc", None))
        steps: list[tuple[str, str | None, Callable[[str], Awaitable[Any]]]] = [
            (f"{service}.album", t.album_ref, lambda ref: svc.album(ref)),
            (f"{service}.track", t.ref, lambda ref: svc.track(ref)),
            (f"{service}.artist", t.artist_ref, lambda ref: svc.artist_page(ref)),
        ]
        for check, ref, read in steps:
            if check not in rec.todo:
                continue
            if not ref:
                await rec(check, False, "поиск не назвал запись")
                continue
            t0 = time.monotonic()
            try:
                got = await read(ref)
                tracks_of = getattr(got, "tracks", None)
                await rec(check, True, f"{len(tracks_of)} треков" if isinstance(tracks_of, list) else None, t0)
            except Exception as e:
                await rec(check, False, checks.error_words(e), t0)

    return probe


async def _musicbrainz(rec: Recorder) -> None:
    from app.services import svc

    t0 = time.monotonic()
    try:
        found = await svc.musicbrainz.search_recordings(REF_ARTIST, REF_TITLE)
        await rec("musicbrainz.recordings", bool(found), f"нашлось {len(found)}" if found else "запись не нашлась", t0)
    except Exception as e:
        await rec("musicbrainz.recordings", False, checks.error_words(e), t0)


async def _lastfm(rec: Recorder) -> None:
    from app.mixes.sources import lastfm_artist_tags

    t0 = time.monotonic()
    try:
        tags = await lastfm_artist_tags(REF_ARTIST)
        await rec("lastfm.api", bool(tags), ", ".join(tags[:3]) if tags else "тегов нет", t0)
    except Exception as e:
        await rec("lastfm.api", False, checks.error_words(e), t0)


async def _lyrics(rec: Recorder) -> None:
    from app.lyrics.providers import PROVIDERS, Query

    # a source that has not got the Russian song may have the English one: either says it works
    songs = [Query(artist=REF_ARTIST, title=REF_TITLE, album=_reference.get("album"), duration_s=_reference.get("duration_s"),
                   isrc=_reference.get("isrc")), Query(*LYRICS_SPARE)]
    for source in LYRICS_SOURCES:
        check = f"lyrics.{source}"
        if check not in rec.todo or source not in PROVIDERS:
            continue
        t0 = time.monotonic()
        found, error = None, None
        for q in songs:
            try:
                found = await PROVIDERS[source](q)
            except Exception as e:
                error = checks.error_words(e)
                continue
            if found is not None:
                break
        if found is None:
            await rec(check, False, error or "не нашёл текст двух известных песен", t0)
        else:
            await rec(check, True, {"word": "текст по словам", "synced": "текст с метками времени",
                                    "plain": "текст без меток времени"}.get(found.kind, found.kind), t0)


async def _telegram(rec: Recorder) -> None:
    from app.config import get_settings
    from app.net import make_client

    t0 = time.monotonic()
    try:
        async with make_client(timeout=15) as c:
            r = await c.get(f"https://api.telegram.org/bot{get_settings().telegram_bot_token.strip()}/getMe")
        body = r.json() if r.status_code == 200 else {}
        ok = bool(body.get("ok"))
        name = (body.get("result") or {}).get("username")
        await rec("telegram.bot", ok, f"@{name}" if ok and name else f"HTTP {r.status_code}", t0)
    except Exception as e:
        await rec("telegram.bot", False, checks.error_words(e), t0)


# the groups, in order: a catalog before the lyrics (they are asked with the song as a catalog told it)
GROUPS: tuple[tuple[tuple[str, ...], Callable[[Recorder], Awaitable[None]]], ...] = (
    (("ytdlp.find", "ytdlp.file"), _provider("ytdlp", "ytdlp")),
    (("youtube.pot",), _pot),
    (("soundcloud.find", "soundcloud.file"), _provider("soundcloud", "soundcloud")),
    (("deezer.search", "deezer.album", "deezer.track"), _catalog("deezer")),
    (("spotify.search", "spotify.album", "spotify.artist", "spotify.track"), _catalog("spotify")),
    (("yandex.search", "yandex.album", "yandex.track"), _catalog("yandex")),
    (("yandex.find", "yandex.file"), _provider("yandex", "yandex")),
    (("hitmos_fm.find", "hitmos_fm.file"), _provider("hitmos_fm", "hitmos_fm")),
    (("hitmoz.find", "hitmoz.page", "hitmoz.file"), _provider("hitmoz", "hitmoz")),
    (("musicbrainz.recordings",), _musicbrainz),
    (("lastfm.api",), _lastfm),
    (tuple(f"lyrics.{s}" for s in LYRICS_SOURCES), _lyrics),
    (("telegram.bot",), _telegram),
)


async def unavailable() -> dict[str, str]:
    """The checks of what the Hub has no use of here, and why («не подключён»)."""
    from app.config import get_settings
    from app.services import svc

    off: dict[str, str] = {}

    def no(keys: tuple[str, ...] | list[str], why: str) -> None:
        for k in keys:
            off.setdefault(k, why)

    if not os.environ.get("YTDLP_POT_BASE_URL"):
        no(("youtube.pot",), "сервер PO-токенов не запущен")
    for name, prefix in (("ytdlp", "ytdlp"), ("soundcloud", "soundcloud"), ("hitmos_fm", "hitmos_fm"), ("hitmoz", "hitmoz")):
        if name not in svc.providers:
            no([c.key for c in checks.CHECKS if c.key.startswith(f"{prefix}.")], "источник выключен")
    if svc.deezer is None:
        no(("deezer.search", "deezer.album", "deezer.track"), "каталог Deezer выключен")
    if svc.yandex is None:
        no(("yandex.search", "yandex.album", "yandex.track", "yandex.find", "yandex.file"), "каталог Яндекса выключен")
    elif "yandex" not in svc.providers:
        no(("yandex.find", "yandex.file"), "источник выключен")
    else:
        try:
            from app.providers.yandex import plus_token

            if await plus_token() is None:
                no(("yandex.find", "yandex.file"), "нет аккаунта Яндекса с Плюсом")
        except Exception as e:
            log.info("checks: the Yandex account not looked up: %r", e)
    if svc.spotify is None:
        no(("spotify.search", "spotify.album", "spotify.artist", "spotify.track"), "каталог Spotify выключен")
    try:
        from app.mixes.sources import lastfm_configured

        if not await lastfm_configured():
            no(("lastfm.api",), "Last.fm не подключён")
    except Exception as e:
        log.info("checks: Last.fm not looked up: %r", e)
    if not get_settings().telegram_bot_token.strip():
        no(("telegram.bot",), "бот не настроен")
    return off


# --- the run -----------------------------------------------------------------------------------------------------

async def run(force: bool = False) -> dict[str, Any]:
    """Ask what the day did not show working (everything, `force`). Returns the status written down."""
    from app.catalog.cache import fresh
    from app.db import session_scope
    from app.outbound import purpose

    started = utcnow()
    t0 = time.monotonic()
    off = await unavailable()
    async with session_scope() as s:
        recent = set((await s.execute(select(ServiceCheck.check).where(
            ServiceCheck.ok.is_(True), ServiceCheck.at >= started - FRESH_FOR).distinct())).scalars())
    _reference.clear()
    asked = 0
    probing = checks._probing.set(True)
    reading = fresh.set(True)
    try:
        with purpose("ночная проверка"):
            for keys, probe in GROUPS:
                # the lyrics are asked with the song as the catalogs told it, so a catalog's group goes first
                todo = {k for k in keys if k not in off and (force or k not in recent)}
                if not todo:
                    continue
                rec = Recorder(todo)
                try:
                    await asyncio.wait_for(probe(rec), GROUP_TIMEOUT_S)
                except TimeoutError:
                    await rec.failed(tuple(todo - rec.done), "не ответил за 3 минуты")
                except Exception as e:
                    log.warning("checks: the group %s failed: %r", keys[0], e)
                    await rec.failed(tuple(todo - rec.done), checks.error_words(e))
                asked += len(rec.done)
    finally:
        checks._probing.reset(probing)
        fresh.reset(reading)
    await checks.flush()  # the fields of what was asked
    status = {"at": started.isoformat(), "took_s": round(time.monotonic() - t0, 1), "asked": asked, "force": force}
    async with session_scope() as s:
        await s.execute(delete(ServiceCheck).where(ServiceCheck.at < utcnow() - timedelta(days=checks.KEEP_DAYS)))
        row = await s.get(AppSetting, STATUS_KEY)
        if row is None:
            s.add(AppSetting(key=STATUS_KEY, value_json=status))
        else:
            row.value_json = status
        await s.commit()
    log.info("checks: %s asked in %.0f s (%s)", asked, status["took_s"], "all" if force else "what the day did not show")
    return status


async def running(s: Any) -> bool:
    return (await s.scalar(select(func.count(Job.id)).where(Job.kind == JOB, Job.status.in_(("queued", "running"))))) > 0


async def request(s: Any, *, force: bool = True) -> bool:
    """Queue a run, unless one is queued or going."""
    from app.jobs.queue import enqueue

    if await running(s):
        return False
    await enqueue(s, JOB, {"force": force}, max_attempts=1)
    return True


@handler(JOB)
async def handle(job: Job) -> None:
    await run(bool((job.payload_json or {}).get("force")))


def next_night(now: datetime, zone: ZoneInfo) -> float:
    """Seconds until the next `HOUR` o'clock, local time."""
    local = now.astimezone(zone)
    at = local.replace(hour=HOUR, minute=0, second=0, microsecond=0)
    if at <= local:
        at += timedelta(days=1)
    return (at - local).total_seconds()


async def loop() -> None:
    """The worker: every night at `HOUR`, a run of what the day did not show."""
    from datetime import UTC

    from app.config import get_settings
    from app.db import session_scope
    from app.jobs.queue import notify

    zone = ZoneInfo(get_settings().timezone)
    while True:
        await asyncio.sleep(next_night(datetime.now(UTC), zone))
        try:
            async with session_scope() as s:
                if await request(s, force=False):
                    await s.commit()
                    notify()
        except Exception:
            log.exception("checks: the nightly run not queued")
        await asyncio.sleep(60)
