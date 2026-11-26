"""Listening before downloading (GET /api/listen): the file a download of a catalog track would pick, streamed
through the Hub with Range support.

The file is chosen the way the download pipeline does it (catalog meta → Cyrillic original → provider order →
search + rank). Picking it is what takes seconds, so it is kept off the tap:
- a pick is remembered for weeks (`catalog_cache`, key `listen:{ref}`): only the CDN link behind it expires, and
  that is renewed on the fly;
- the PWA names the tracks on the user's screen (`prefetch`) and the Hub picks them in the background, checking
  that the file opens. Background work yields to taps and downloads (`app.net.Priority`): a hitmoz request or a
  yt-dlp slot goes to a user request first;
- a tap that finds nothing ready searches all sources at once and takes the first one in source order that has
  the track. The stream itself opens the file; when it does not open, the next candidate takes over.
Ranking skips the track page (hitmoz: Explicit/Clean, real bitrate). Once the user has listened, the Hub checks
the chosen file in the background; a download of that ref tries the heard file first if the check passed.
Owned tracks play from the library. Catalog playback requires a full recording from a source.

A provider's file found in the Files tab of the search is listened to the same way, by its ref "file:<provider>:<id>"
(`remember_files`): no pick, the file itself, prefetched while on screen. A source that has no plain HTTP audio
(`streams = False`: SoundCloud, HLS) is listened to from a copy the Hub downloads first, kept for a few hours.

The stream of a picked file is served from its server copy (`_Copy`): the Hub downloads the whole file to its disk at
its own speed and the player reads the copy as it grows, so a CDN that drops the connection midway (googlevideo does)
is resumed by the Hub instead of reaching the player, and seeks and the next requests are served from disk. A copy for
the track being played goes first: copies loaded ahead (the app's precache, `X-HomeMusic-Prefetch`) wait while one
for a played track downloads.

What a queue plays next is loaded ahead (`load_ahead`, asked for by the web player with `GET /api/listen?ahead=`):
opening a source is what takes seconds (YouTube: some twenty), so a switch must find the file opened. The next
track is copied whole. The ones after it are opened and copied only as far as their start (`HEAD_BYTES`): a
listener who skips through the queue hears each at once, one who does not has cost the source next to nothing. A
copy stopped at its start goes on when the track is played or becomes the next one.

For a metered network the stream can be asked for the light variant (`quality="mobile"`, `app.light`): the Opus copy
of the file when it is heavy (the copy of a catalog track is made from its server copy). Copies are made ahead of
time (`warm_light`: a mix's songs, a wave's tracks); a stream that finds none ready waits a moment, then serves the
original and says so (`Media.variant`) while the copy is finished in the background.

A player says which formats it cannot play at all (`cant`: Safari of an iPhone and Ogg Opus) and what else it can
(`can`: «opus-mp4», Opus in MP4 — iOS 17 and later): a file in such a format is served as its copy, made from the
file on disk (a catalog track: its server copy, whole) and waited for — Opus put into MP4 as it is where the player
plays that (`light.OPUS_MP4`, a moment), else AAC (`light.PLAYABLE`); the next track of a queue gets its copy ahead
(`load_ahead`).
"""

from __future__ import annotations

import asyncio
import hashlib
import json
import logging
import os
import shutil
import time
from collections.abc import AsyncIterator, Awaitable, Callable, Coroutine
from dataclasses import dataclass, field, replace
from datetime import datetime, timedelta
from pathlib import Path
from tempfile import mkdtemp
from typing import Any, Literal

import httpx
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app import light
from app.catalog.cache import cache_delete, cache_get, cache_set, cache_set_many
from app.catalog.models import CATALOGS, CatalogNotFound, CatalogTrack, split_ref
from app.catalog.yandex import russian_original
from app.config import get_settings
from app.db import session_scope
from app.events import bus
from app.jobs.downloads import (
    MAX_TRIES_PER_PROVIDER,
    complete_meta,
    meta_from_catalog,
    query_from_track,
    search_provider,
)
from app.library.owned import find_owned
from app.library.tracks import by_ref
from app.matching.normalize import clean_isrc, explicit_marker, normalize_artist, normalize_title
from app.matching.select import needs_uncensored, rank_candidates, trusted_original
from app.models import CatalogCache, Track, utcnow
from app.net import Priority, in_background, make_client, priority_context
from app.providers.base import FILE_REF, Candidate, StreamSource, TrackQuery, file_ref
from app.services import svc

log = logging.getLogger(__name__)

MEM_TTL_S = 3 * 3600  # a pick in memory (with the upstream link it opened), since its last use
MEM_MAX = 2000
KEEP_S = 30 * 86400  # a remembered pick
MISSING_RECORDING_S = 86400
MISSING_RECORDING = "unavailable"
MISS_TTL_S = 30 * 60  # the prefetch skips a track that was not found for this long
# Retry background searches sooner if a source failed (network, site down).
TRANSIENT_TTL_S = 60
# Fewer search results than a download's 15 (when its API fails, YouTube Music looks up each one) and no track pages
SEARCH_LIMIT = 4
PROBE_RANGE = "bytes=0-0"
HEARD_CHECK_WAIT_S = 10  # a download waits this long for the check of the file just heard
PREFETCH_WORKERS = 3
PREFETCH_MAX = 40  # tracks per user
_PLAYABLE_TYPES = ("audio/", "video/")  # googlevideo may label audio-only formats video/*
LOCAL_DIR = "listen"  # under staging: server copies, and copies of files of sources without plain HTTP audio
# a copy, since its last use: a home server has the room, and a track heard again (a pause, a replay a week later)
# comes from its disk, not from the source anew
LOCAL_KEEP_S = 30 * 86400
LOCAL_MAX_BYTES = 30 * 2**30
LOCAL_MEDIA_TYPES = {".mp3": "audio/mpeg", ".m4a": "audio/mp4", ".mp4": "audio/mp4", ".opus": "audio/ogg",
                     ".ogg": "audio/ogg", ".webm": "audio/webm", ".flac": "audio/flac"}
STREAM_SUFFIXES = {"audio/mpeg": ".mp3", "audio/mp4": ".m4a", "audio/ogg": ".ogg", "audio/webm": ".webm",
                   "audio/flac": ".flac"}
COPY_NAME = "stream"  # a server copy, in the folder of its ref under LOCAL_DIR
COPY_NOTE = "stream.json"  # beside a whole copy: which file it is — found again after a restart or a pick forgotten
COPY_CHUNK = 64 * 1024  # what a stream reads from a server copy at once
HEAD_BYTES = 512 * 1024  # the start of a track loaded ahead: some ten seconds of a heavy file, more of a light one
HEAD_KEEP_S = 20 * 60  # how long a copy stopped at its start waits for the rest to be wanted
COPY_RESUMES = 4  # a dropped upstream connection is resumed this many times in a row (with no progress between)
FIRST_AUDIO_S = 15
SOURCE_BACKOFF_S = 90
# A request this far past what a server copy has waits for it; a seek farther ahead goes to the upstream itself
SEEK_WAIT_BYTES = 2 * 2**20
CALM_RECHECK_S = 1.0  # a copy loaded ahead that waits looks again this often whether it still has to

Kind = Literal["full", "library"]


class ListenError(Exception):
    """Nothing to play; `status` is the HTTP status for the API."""

    def __init__(self, status: int, message: str):
        super().__init__(message)
        self.status = status


class RecordingUnavailable(ListenError):
    """The catalog entry is gone or a completed search yielded no matching full recording."""

    def __init__(self, message: str):
        super().__init__(404, message)


@dataclass
class Choice:
    ref: str
    kind: Kind
    artist: str
    title: str
    duration_s: int | None = None
    candidate: Candidate | None = None  # kind "full"
    alternates: list[Candidate] = field(default_factory=list)  # "full": next files when `candidate` doesn't open
    query: dict | None = None  # catalog query + deezer_track_id (the library check of a remembered pick)
    pquery: dict | None = None  # the query the providers were searched with (Cyrillic original)
    recording: str | None = None  # shared by the catalog addresses of this recording
    path: Path | None = None  # kind "library"
    nd_song_id: str | None = None  # kind "library", once Navidrome has scanned the file
    source: StreamSource | None = None  # upstream that opened last (URL after redirects); renewed when it fails
    local: Path | None = None  # "full" from a source without plain HTTP audio: the copy the Hub downloaded
    heard: bool = False  # the user has listened to it
    verified: bool = False  # "full": the candidate passed the track-page check (or needs none)
    checking: asyncio.Task | None = None  # the check after the first listen
    sources_checked: bool = False
    at: float = field(default_factory=time.monotonic)

    def fresh(self) -> bool:
        return time.monotonic() - self.at < MEM_TTL_S


@dataclass
class _Job[T]:
    task: asyncio.Task[T]
    prio: Priority
    candidate: Candidate | None = None


@dataclass(eq=False)
class _Copy:
    """The server copy of a picked file: downloaded to the Hub's disk, read by streams while it grows."""

    cand: Candidate  # the file it is a copy of
    path: Path
    prio: Priority  # background: loaded ahead; it yields to copies of played tracks
    media_type: str = "audio/mpeg"
    size: int | None = None  # from the first upstream response
    have: int = 0  # bytes on disk
    done: bool = False
    failed: ListenError | None = None
    readers: int = 0  # streams of a played track reading it
    task: asyncio.Task | None = None
    cond: asyncio.Condition = field(default_factory=asyncio.Condition)
    # loaded ahead as a start only: the download stops at this many bytes until the rest is wanted (`go`)
    limit: int | None = None
    go: asyncio.Event = field(default_factory=asyncio.Event)

    def whole(self) -> None:
        """The rest of the file is wanted now."""
        self.limit = None
        self.go.set()

    def settled(self) -> bool:
        return self.done or self.failed is not None

    async def until(self, ready: Callable[[], bool]) -> None:
        async with self.cond:
            await self.cond.wait_for(ready)

    async def changed(self) -> None:
        async with self.cond:
            self.cond.notify_all()


@dataclass
class Media:
    """What a stream request gets: a file on disk (`path`), or a `body` (a growing server copy or the upstream)
    with its status and headers. Neither: an empty answer with `status` (416)."""

    choice: Choice
    path: Path | None = None
    body: AsyncIterator[bytes] | None = None
    status: int = 200
    headers: dict[str, str] = field(default_factory=dict)
    media_type: str | None = None  # None: by the file's name
    variant: str | None = None  # `light.LIGHT`/`ORIGINAL`/`PENDING` for a stream asked for the light variant
    close: Callable[[], Awaitable[None]] | None = None  # after the answer (the body may never have been read)


_choices: dict[str, Choice] = {}
_inflight: dict[str, _Job[Choice]] = {}
_fetching: dict[str, _Job[Path]] = {}  # ref -> download of a local copy (`_predownload`)
_copies: dict[str, _Copy] = {}  # ref -> server copy of its picked file
_calm: asyncio.Event | None = None  # set while no copy of a played track downloads (`_stir`)
_missed: dict[str, float] = {}  # ref -> monotonic time until which the prefetch skips it
_wanted: dict[int, list[str]] = {}  # user id -> refs on their screen still to pick, top first
_wake: asyncio.Event | None = None
_workers: list[asyncio.Task] = []
_tasks: set[asyncio.Task] = set()  # fire-and-forget work (kept referenced until done)
# Audio is proxied over HTTP/1.1: on a shared HTTP/2 connection a stream the player abandons (track switch, seek)
# leaves its unread data counted against the connection window, and after a couple of them the next stream gets
# headers but no data (seen with the hitmoz CDN).
_media: httpx.AsyncClient | None = None


async def choose(ref: str, *, background: bool = False, stale: Choice | None = None) -> Choice:
    """The pick for `ref`: in memory, remembered or found now. Concurrent callers share one resolution, which
    also survives the caller going away (the next tap finds it ready). `background`: a prefetch, which yields to
    user requests (a user request joining it lifts it). `stale`: this pick did not open, find another."""
    hit = _choices.get(ref)
    if hit and hit is not stale and hit.fresh() and _usable(hit):
        hit.at = time.monotonic()
        return hit
    job = _inflight.get(ref)
    if job is None:
        prio = Priority(background)
        coro = _resolve(ref, probe=background or stale is not None, remembered=stale is None)
        job = _Job(asyncio.create_task(coro, context=priority_context(prio)), prio)
        _inflight[ref] = job

        def done(t: asyncio.Task[Choice], _ref: str = ref) -> None:
            _inflight.pop(_ref, None)
            if not t.cancelled():
                t.exception()  # retrieved here: nobody may be awaiting it any more

        job.task.add_done_callback(done)
    elif not background:
        job.prio.foreground()
    return await asyncio.shield(job.task)


def _usable(c: Choice) -> bool:
    # an owned file Navidrome hadn't scanned yet is looked up again: its song id makes ♥ work
    return c.path is None or (c.path.is_file() and bool(c.nd_song_id))


MOBILE = "mobile"  # the quality of a stream for a metered network (`light`)
# What a player may say it cannot play (`cant`), by the media types of the files: a format each
FORMATS = {"audio/ogg": "ogg", "audio/opus": "ogg", "audio/webm": "webm", "video/webm": "webm", "audio/flac": "flac"}


CAN = {"opus-mp4"}  # what a player may say it can play besides (`can`)


def parse_cant(value: str | None) -> frozenset[str]:
    """`cant` of a request: «ogg,webm» — the formats known here, the rest ignored."""
    return frozenset(f for f in (value or "").split(",") if f in FORMATS.values())


def parse_can(value: str | None) -> frozenset[str]:
    """`can` of a request: «opus-mp4» — what is known here, the rest ignored."""
    return frozenset(f for f in (value or "").split(",") if f in CAN)


def _plays(media_type: str, cant: frozenset[str]) -> bool:
    return FORMATS.get(media_type) not in cant


def _light_encoding(cant: frozenset[str]) -> light.Encoding:
    return light.AAC if "ogg" in cant else light.OPUS


async def open_media(ref: str, range_header: str | None, *, ahead: bool = False, quality: str | None = None,
                     cant: frozenset[str] = frozenset(), can: frozenset[str] = frozenset()) -> Media:
    """The audio for the client's Range: the library file, a local copy, or the server copy of the picked file.
    `ahead`: the app loads a track of its queue ahead; its server copy waits for those of
    played tracks. `quality` "mobile": the light variant when the file is heavy (`app.light`). `cant`: the formats
    the player cannot play (`FORMATS`): a file in one of them comes as its copy (`can`: what that may be). A pick
    none of whose files opens is replaced by a new one (once)."""
    c = await choose(ref, background=ahead)
    searched_again = False
    while True:
        try:
            media = await _open_choice(c, range_header, ahead, quality == MOBILE, cant, can)
        except ListenError:
            # A range of a file already served must never become a range of another encoding.
            if c.candidate is None or (c.heard and range_header and not range_header.startswith("bytes=0-")):
                raise
            _defer_source(c, c.candidate)
            if backup := await _backup_choice(c):
                c = backup
                continue
            if searched_again:
                raise
            searched_again = True
            c = await choose(ref, background=ahead, stale=c)
        else:
            if not ahead and media.status in (200, 206):
                _heard(c)
                _ensure_sources(c)
            return media


async def _open_choice(c: Choice, range_header: str | None, ahead: bool, lite: bool, cant: frozenset[str],
                       can: frozenset[str]) -> Media:
    enc = _light_encoding(cant)
    if c.kind == "library" or _uses_local_copy(c):
        if c.kind == "library":
            assert c.path is not None
            path, ident, media = c.path, _library_ident(c.path), Media(c, path=c.path)
        else:
            await _predownload(c)
            assert c.local is not None
            path, ident = c.local, _file_ident(c)
            media = Media(c, path=path, media_type=local_media_type(path))
        if not _plays(local_media_type(path), cant):
            return await _playable(c, path, ident, lite, can)
        return await _lighten(c, media, path, ident, ahead, enc) if lite else media
    known = await light.cached(_file_ident(c), enc) if lite else None
    if known is not None and known[0] is not None:
        return _variant(c, known, enc)
    cp = _server_copy(c, background=ahead)
    await cp.until(lambda: cp.have > 0 or cp.settled() or (ahead and cp.size is not None))
    if not cp.have and (not ahead or cp.settled()):
        raise cp.failed or ListenError(502, "Источник не отдал аудио")
    if not _plays(cp.media_type, cant):
        cp.whole()  # the copy is made of the whole file
        await cp.until(cp.settled)
        if not cp.done:
            raise cp.failed or ListenError(502, "Источник не отдал аудио")
        return await _playable(c, cp.path, _file_ident(c), lite, can)
    variant = known[1] if known else light.PENDING
    if lite and known is None:
        media, variant = await _lighten_copy(c, cp, ahead, enc)
        if media is not None:
            return media
    if media := await _from_copy(c, cp, range_header, foreground=not ahead):
        return replace(media, variant=variant) if lite else media
    resp, src = await open_stream(c, range_header, allow_alternates=False)
    media = _upstream_media(c, resp, src)
    return replace(media, variant=light.PENDING) if lite else media


def _library_ident(path: Path) -> str:
    st = path.stat()
    return f"lib|{path}|{st.st_size}|{st.st_mtime_ns}"


def _file_ident(c: Choice) -> str:
    """What identifies the bytes of the picked file (the light copy is made from its server copy)."""
    cand = c.candidate
    return f"{c.ref}|{cand.provider}:{cand.provider_id}" if cand else f"{c.ref}|{c.kind}"


def _variant(c: Choice, hit: tuple[Path | None, str], enc: light.Encoding, original: Media | None = None) -> Media:
    path, variant = hit
    if path is not None:
        return Media(c, path=path, media_type=enc.media_type, variant=variant)
    assert original is not None
    return replace(original, variant=variant)


async def _playable_encoding(src: Path, ident: str, can: frozenset[str]) -> light.Encoding:
    """What a file the player cannot play is made into: Opus is put into MP4 as it is where the player plays that."""
    if "opus-mp4" in can and await light.codec(src, ident) == "opus":
        return light.OPUS_MP4
    return light.PLAYABLE


async def _playable(c: Choice, src: Path, ident: str, lite: bool, can: frozenset[str]) -> Media:
    """A file the player cannot play: its copy in a format it plays, waited for."""
    enc = await _playable_encoding(src, ident, can)
    try:
        path = await light.convert(src, ident, enc)
    except asyncio.CancelledError:
        raise
    except Exception as e:
        log.warning("listen %s: no playable copy of %s: %r", c.ref, src.name, e)
        raise ListenError(502, "Устройство не воспроизводит формат этого файла, а перекодировать его не получилось") from e
    # one and the same copy for every request of this player: as stable as an original for its cache
    return Media(c, path=path, media_type=enc.media_type, variant=light.ORIGINAL if lite else None)


async def _lighten(c: Choice, media: Media, src: Path, ident: str, ahead: bool, enc: light.Encoding) -> Media:
    """The answer for a light request when the file is on disk: its light copy, or `media` itself (light enough as
    it is, or the copy is not ready in time)."""
    hit = await light.get(src, ident, enc=enc, wait_s=light.WAIT_AHEAD_S if ahead else light.WAIT_S)
    return _variant(c, hit, enc, media)


async def _lighten_copy(c: Choice, cp: _Copy, ahead: bool, enc: light.Encoding) -> tuple[Media | None, str]:
    """The same for a catalog track, whose file is the server copy: the copy is waited for, then the encode. No
    media: the original plays (the variant to say), and the light copy is finished in the background."""
    ident = _file_ident(c)
    wait_s = light.WAIT_AHEAD_S if ahead else light.WAIT_S
    t0 = time.monotonic()
    try:
        await asyncio.wait_for(cp.until(cp.settled), wait_s)
    except TimeoutError:
        pass
    if cp.done:
        hit = await light.get(cp.path, ident, enc=enc, wait_s=max(wait_s - (time.monotonic() - t0), 0.2))
        if hit[0] is not None:
            return _variant(c, hit, enc), light.LIGHT
        return None, hit[1]
    light.served_original(ident, enc)
    if not cp.settled():
        _spawn(_make_light(c, cp, enc), background=True)
    return None, light.PENDING


async def _make_light(c: Choice, cp: _Copy, enc: light.Encoding) -> None:
    """Finish the light copy of a catalog track once its server copy is complete."""
    await cp.until(cp.settled)
    if cp.done and (task := light.ensure(cp.path, _file_ident(c), enc=enc)) is not None:
        await task


async def _light_source(c: Choice) -> tuple[Path, str] | None:
    """The file the light copy of a pick is made from, ready to be read (a catalog track: its server copy is loaded
    in the background first); None: nothing to make (a preview, a copy that did not load)."""
    if c.kind == "library" and c.path is not None:
        return c.path, _library_ident(c.path)
    if c.kind != "full":
        return None
    if _uses_local_copy(c):
        await _predownload(c)
        return (c.local, _file_ident(c)) if c.local is not None else None
    cp = _server_copy(c, background=True)
    await cp.until(cp.settled)
    return (cp.path, _file_ident(c)) if cp.done else None


def warm_light(refs: list[str]) -> None:
    """Make the light copies of these tracks in the background (a mix's songs, a wave's tracks), in order, so a
    stream asked for the light variant finds them ready. Songs of the library are `nd:<song>`."""
    _spawn(_warm_light(list(dict.fromkeys(refs))), background=True)


async def _warm_light(refs: list[str]) -> None:
    for ref in refs:
        try:
            c = await choose(ref, background=True)
            if c.kind == "full" and await light.cached(_file_ident(c)) is not None:
                continue  # made already (or light enough as it is)
            if (src := await _light_source(c)) is not None and (task := light.ensure(*src)) is not None:
                await task
        except asyncio.CancelledError:
            raise
        except Exception as e:
            log.info("listen %s: no light copy: %r", ref, e)


async def _make_playable(c: Choice, cant: frozenset[str], can: frozenset[str]) -> None:
    """The playable copy of the pick's file, if the player cannot play its format (`load_ahead`)."""
    if c.kind == "library" and c.path is not None and _plays(local_media_type(c.path), cant):
        return  # nothing to wait for
    try:
        src = await _light_source(c)
        if src is None:
            return
        cp = _copies.get(c.ref)
        media_type = cp.media_type if cp is not None and cp.path == src[0] else local_media_type(src[0])
        if _plays(media_type, cant):
            return
        enc = await _playable_encoding(*src, can)
        if (task := light.ensure(*src, enc=enc)) is not None:
            await task
    except asyncio.CancelledError:
        raise
    except Exception as e:
        log.info("listen %s: no playable copy ahead: %r", c.ref, e)


def _heard(c: Choice) -> None:
    if c.kind == "full" and not c.heard:
        c.heard = True
        c.checking = _spawn(_after_heard(c), background=True)


def local_media_type(path: Path) -> str:
    return LOCAL_MEDIA_TYPES.get(path.suffix.lower(), "audio/mpeg")


async def listened_candidate(ref: str | None) -> Candidate | None:
    """The file the user listened to for this catalog ref, if it passed the check (a download tries it first)."""
    if not ref:
        return None
    for key in await _track_addresses(ref):
        c = _choices.get(key)
        if c is None or not c.fresh():
            c = await _remembered(key, library=False)
        if c is None or c.kind != "full" or not c.heard:
            continue
        if c.checking is not None and not c.checking.done():
            await asyncio.wait([c.checking], timeout=HEARD_CHECK_WAIT_S)
        if c.verified:
            return c.candidate
    return None


async def _track_addresses(ref: str) -> list[str]:
    async with session_scope() as s:
        track = await by_ref(s, ref)
        if track is not None:
            return list(dict.fromkeys(r for r in (f"lib:{track.id}", ref, track.ref) if r))
    return [ref]


async def forget(ref: str) -> None:
    """Drop the pick (the track has just been imported: the library file plays from now on). A provider's file
    stays: it is that file the user asks for. Done by the worker (a download), the api's memory of it goes too."""
    if is_file_ref(ref):
        return
    for key in await _track_addresses(ref):
        _choices.pop(key, None)
        await cache_delete(_key(key))
    from app import relay

    if relay.role() == "worker":
        relay.send("forget", ref=ref)


def _forget_relayed(msg: dict) -> None:
    async def run() -> None:
        for key in await _track_addresses(msg["ref"]):
            _choices.pop(key, None)

    _spawn(run())


def is_file_ref(ref: str) -> bool:
    return ref.startswith(FILE_REF + ":")


async def remember_files(cands: list[Candidate]) -> None:
    """Files just shown in search or download details, playable and downloadable by their `file_ref`."""
    fresh: list[Choice] = []
    for cand in cands:
        ref = file_ref(cand.provider, cand.provider_id)
        hit = _choices.get(ref)
        if hit and hit.fresh() and hit.candidate == cand:
            continue  # the same file, maybe opened already
        c = Choice(ref, "full", cand.artist, cand.title, cand.duration_s, candidate=cand, verified=True,
                   local=hit.local if hit is not None else None)
        _choices[ref] = c
        fresh.append(c)
    if fresh:
        _prune()
        await cache_set_many({_key(c.ref): _payload(c) for c in fresh}, KEEP_S)


async def file_candidate(ref: str) -> Candidate | None:
    """The provider's file behind a `file_ref` (a download of it from the player), if the search showed it."""
    c = _choices.get(ref)
    if c is None or not c.fresh():
        c = await _remembered(ref, library=False)
    return c.candidate if c is not None else None


async def open_stream(choice: Choice, range_header: str | None, *, allow_alternates: bool = True) -> tuple[httpx.Response, StreamSource]:
    """Upstream response (not read yet) for the client's Range. A link that stopped working (expired CDN URL,
    dead mirror) is renewed from the candidate once; a candidate that still doesn't open gives way to the next."""
    last = ""
    while True:
        cand = choice.candidate
        for _ in range(2):
            try:
                src = choice.source or await _upstream(choice)
            except ListenError as e:
                last = str(e)
                break
            headers = httpx.Headers(src.client.headers)  # the provider's own (hitmoz: Accept-Language)
            headers.update(src.headers)
            headers["Accept-Encoding"] = "identity"
            if range_header:
                headers["Range"] = range_header
            media = _media_client()
            try:
                resp = await media.send(media.build_request("GET", src.url, headers=headers), stream=True,
                                        follow_redirects=True)
            except httpx.HTTPError as e:
                last = repr(e)
                choice.source = None
                continue
            ctype = resp.headers.get("content-type", "").split(";")[0].strip().lower()
            if resp.status_code == 416 or (
                resp.status_code in (200, 206)
                and (ctype.startswith(_PLAYABLE_TYPES) or ctype in ("", "application/octet-stream"))
            ):
                choice.source = replace(src, url=str(resp.url))  # later Range requests skip the redirects
                return resp, src
            last = f"HTTP {resp.status_code} {ctype}".strip()
            await resp.aclose()
            choice.source = None
        if choice.candidate is cand:  # (a concurrent request may have moved on already)
            if not allow_alternates or choice.kind != "full" or not choice.alternates:
                raise ListenError(502, f"Источник не отдаёт файл ({last})")
            if cand is not None:
                _defer_source(choice, cand)
            next_file = next((a for a in choice.alternates if not _deferred(choice.recording, a)), None)
            if next_file is None:
                raise ListenError(502, f"Источник не отдаёт файл ({last})")
            log.info("listen %s: %s does not open (%s), next file", choice.ref, cand.page_url if cand else "", last)
            choice.alternates.remove(next_file)
            choice.candidate = next_file
            choice.source = None
            choice.verified = choice.candidate.enriched


def _media_client() -> httpx.AsyncClient:
    global _media
    if _media is None:
        _media = make_client(timeout=30, http2=False)
    return _media


# --- prefetch --------------------------------------------------------------------------------------------------


def prefetch(user_id: int, refs: list[str]) -> int:
    """The catalog tracks on this user's screen, top first: their files are picked in the background. Replaces
    the user's previous list (tracks that scrolled away are dropped). Returns how many are still to pick."""
    global _wake
    if _wake is None:
        _wake = asyncio.Event()
    if not _workers:
        for _ in range(PREFETCH_WORKERS):
            _workers.append(asyncio.create_task(_worker(), context=priority_context(Priority(True))))
    todo = [r for r in dict.fromkeys(refs) if not _ready(r)][:PREFETCH_MAX]
    if todo:
        _wanted[user_id] = todo
        _wake.set()
    else:
        _wanted.pop(user_id, None)
    return len(todo)


def _ready(ref: str) -> bool:
    """Picked and opened already, being picked, or recently not found."""
    if ref in _inflight or _missed.get(ref, 0) > time.monotonic():
        return True
    hit = _choices.get(ref)
    return bool(hit and hit.fresh() and _usable(hit)
                and (hit.kind != "full" or hit.source is not None or hit.local is not None))


def _next_wanted() -> tuple[int, str] | None:
    """The next ref to pick; users take turns."""
    while _wanted:
        uid = next(iter(_wanted))
        refs = _wanted.pop(uid)
        while refs:
            ref = refs.pop(0)
            if not _ready(ref):
                if refs:
                    _wanted[uid] = refs
                return uid, ref
    return None


async def _worker() -> None:
    assert _wake is not None
    while True:
        wanted = _next_wanted()
        if wanted is None:
            _wake.clear()
            await _wake.wait()
            continue
        user_id, ref = wanted
        try:
            await _warm(ref, user_id)
        except asyncio.CancelledError:
            raise
        except Exception:
            log.exception("listen prefetch %s", ref)


async def _warm(ref: str, user_id: int) -> None:
    """Pick the file and open it once, so a tap streams at once."""
    try:
        c = await choose(ref, background=True)
        if c.kind == "full" and c.source is None and not await _opens(c):
            await choose(ref, background=True, stale=c)
    except ListenError as e:
        log.info("listen prefetch %s: %s", ref, e)
        _missed[ref] = time.monotonic() + (MISS_TTL_S if e.status == 404 else TRANSIENT_TTL_S)
        if isinstance(e, RecordingUnavailable):
            bus.publish(user_id, "recording", {"ref": ref, "unavailable": True})


def load_ahead(c: Choice, *, whole: bool, cant: frozenset[str] = frozenset(), can: frozenset[str] = frozenset()) -> None:
    """A queue plays this pick soon: its file is opened and copied to the Hub's disk now, behind the copies of
    played tracks. `whole`: it plays next — all of it; else only its start, enough for a switch to it to sound at
    once. A source with no plain HTTP audio has no start to take: its file is downloaded only when it plays next.
    `cant`, `can`: what the player cannot play, and what else it can — the next track in a format it cannot play gets
    its copy now."""
    if whole and cant:
        _spawn(_make_playable(c, cant, can), background=True)
    if c.kind != "full" or c.candidate is None:
        return  # a library file is on disk already
    if _uses_local_copy(c):
        if whole and c.ref not in _fetching:
            _spawn(_predownload(c), background=True)
        return
    _server_copy(c, background=True, head=not whole)


async def reset() -> None:
    """Forget picks in memory, stop the prefetch and server copies, close the clients (app shutdown, tests)."""
    global _media, _wake, _calm
    tasks = set([*_workers, *_tasks, *(j.task for j in [*_inflight.values(), *_fetching.values()])])
    for t in tasks:
        t.cancel()
    # Resolution may still be rolling back its DB session. Finish cancellation before
    # disposing the engine/closing clients; a second event-loop cancellation interrupts that cleanup.
    if tasks:
        await asyncio.gather(*tasks, return_exceptions=True)
    _workers.clear()
    _tasks.clear()
    _inflight.clear()
    _fetching.clear()
    _copies.clear()
    _choices.clear()
    _source_lookups.clear()
    _source_backoff.clear()
    _missed.clear()
    _wanted.clear()
    _wake = _calm = None
    light.reset()
    if _media is not None:
        await _media.aclose()
    _media = None


def _spawn(coro: Coroutine[Any, Any, Any], *, background: bool = False, prio: Priority | None = None) -> asyncio.Task:
    if prio is None and background:
        prio = Priority(True)
    t = asyncio.create_task(coro, context=priority_context(prio) if prio else None)
    _tasks.add(t)

    def done(t: asyncio.Task) -> None:
        _tasks.discard(t)
        if not t.cancelled() and (e := t.exception()):
            log.warning("listen: background work failed: %r", e)

    t.add_done_callback(done)
    return t


# --- resolution ------------------------------------------------------------------------------------------------


async def _resolve(ref: str, *, probe: bool, remembered: bool) -> Choice:
    """`probe`: sources one by one, each pick opened before it is taken (background, or a pick that failed);
    otherwise all sources at once and nothing opened (the stream opens it). `remembered`: a remembered pick
    may be used."""
    t0 = time.monotonic()
    if ref.startswith(UPLOAD_REF + ":"):
        return await _resolve_upload(ref)
    try:
        src, id_ = split_ref(ref)
    except ValueError as e:
        raise ListenError(400, "Неверная ссылка на трек") from e
    if src == FILE_REF:
        return await _resolve_file(ref, probe=probe, remembered=remembered)
    if src == "nd":
        return await _resolve_song(ref, id_)
    if src not in ("lib", "dz", "ym", "sp", "hm") or (src == "hm" and "hitmoz" not in svc.providers):
        raise ListenError(400, "Этот трек нельзя послушать")
    choice = await _remembered(ref) if remembered else None
    how = "remembered"
    if choice is not None and probe and choice.kind == "full" and not await _opens_bounded(choice):
        choice = None  # the remembered file is gone
    if choice is None:
        if not remembered:
            await cache_delete(_key(ref))
        try:
            choice = await (_resolve_hitmoz(ref, id_, probe) if src == "hm" else
                            _resolve_catalog(ref, probe, strict=not remembered))
        except RecordingUnavailable:
            await cache_set(_key(ref), {"kind": MISSING_RECORDING}, MISSING_RECORDING_S)
            raise
        how = "probed" if probe else "searched"
        await _save(choice)
    _prune()
    _choices[ref] = choice
    what = choice.candidate.page_url if choice.candidate else choice.path
    log.info("listen %s: %s %s (%s, %.1f s)", ref, choice.kind, what, how, time.monotonic() - t0)
    return choice


def _prune() -> None:
    for k in [k for k, c in _choices.items() if not c.fresh()]:
        del _choices[k]
    if len(_choices) >= MEM_MAX:
        for k, _ in sorted(_choices.items(), key=lambda kc: kc[1].at)[: len(_choices) - MEM_MAX + 1]:
            del _choices[k]
    now = time.monotonic()
    for k in [k for k, until in _missed.items() if until <= now]:
        del _missed[k]


async def _resolve_file(ref: str, *, probe: bool, remembered: bool) -> Choice:
    """A provider's file: nothing to pick, only to find it among the files the search showed."""
    if not remembered:  # it did not open: there is no other file to try
        raise ListenError(502, "Источник не отдаёт этот файл")
    choice = await _remembered(ref, library=False)
    if choice is None:
        raise ListenError(404, "Файл не найден: повторите поиск")
    if probe and not await _opens(choice):
        raise ListenError(502, "Источник не отдаёт этот файл")
    _choices[ref] = choice
    return choice


UPLOAD_REF = "up"  # "up:<upload>": a file brought to the Hub that is not in the library yet («Загрузить файлы»)


async def _resolve_upload(ref: str) -> Choice:
    """An uploaded file waiting to be brought in: it plays as it lies in staging."""
    from app.library.recognize import names_of
    from app.models import Upload

    ident = ref.split(":", 1)[1]
    async with session_scope() as s:
        u = await s.get(Upload, int(ident)) if ident.isdigit() else None
        found = (u.staging_path, u.probed_tags_json or {}, u.orig_name, u.duration_s) if u is not None and u.status == "uploaded" else None
    path = get_settings().staging_dir / found[0] if found else None
    if not found or path is None or not path.is_file():
        raise ListenError(404, "Этого файла уже нет среди загруженных")
    artist, title = names_of(found[1], found[2])
    return Choice(ref, "library", artist, title, round(found[3]) if found[3] else None, path=path)


async def _resolve_song(ref: str, song_id: str | None = None, *, track_id: int | None = None) -> Choice:
    """Play a library file, including one waiting for its Navidrome song ID."""
    async with session_scope() as s:
        if track_id is not None:
            t = await s.get(Track, track_id)
        else:
            t = (await s.execute(select(Track).where(Track.nd_song_id == song_id).limit(1))).scalar_one_or_none()
        found = (t.path, t.artist, t.title, t.duration_s, t.nd_song_id) if t else None
    path = get_settings().library_dir / found[0] if found else None
    if not found or not path or not path.is_file():
        raise ListenError(404, "Песни нет в библиотеке")
    choice = Choice(ref, "library", found[1], found[2], found[3], path=path, nd_song_id=found[4])
    _choices[ref] = choice
    return choice


async def _resolve_catalog(ref: str, probe: bool, *, strict: bool = False) -> Choice:
    """A track of a catalog (Deezer, Yandex): the catalog says what the song is, the Hub's sources have the file."""
    t0 = time.monotonic()
    async with session_scope() as s:
        track = await by_ref(s, ref)
        path = get_settings().library_dir / track.path if track and track.path else None
        if path and path.is_file():
            return Choice(ref, "library", track.artist, track.title, track.duration_s, path=path, nd_song_id=track.nd_song_id)
    recording = f"lib:{track.id}" if track else ref
    t = None
    if track and track.artist and track.title and track.title != track.ref:
        saved = track.catalog or {}
        record = track.ref or ref
        source, ident = split_ref(record)
        t = CatalogTrack.model_validate({
            **saved, "ref": record, "id": ident, "source": source if source in CATALOGS else "nd",
            "title": track.title, "artist": track.artist, "album_title": track.album,
            "album_artist": track.album_artist, "duration_s": track.duration_s, "isrc": track.isrc,
            "explicit": track.explicit,
        })
    if t is None or (not ref.startswith("lib:") and not (ref.startswith("dz:") and svc.deezer is None)):
        external = (track.ref if track else None) or ref
        try:
            t = await svc.track(external)
        except CatalogNotFound as e:
            if t is None:
                raise RecordingUnavailable("Трек не найден в каталоге") from e
        except Exception as e:
            if t is None:
                from app.errors import explain, service_of_ref

                raise ListenError(502, str(explain(e, service_of_ref(external)))) from e
    q = query_from_track(t)
    query = {**q.model_dump(), "catalog_ref": t.ref}
    # An owned song never waits for optional album/name metadata from external services.
    path, nd_song_id = await _library_file(query)
    if path:
        return Choice(ref, "library", q.artist, q.title, q.duration_s, path=path, nd_song_id=nd_song_id, query=query)

    # the same steps as handle_download_item: Cyrillic original, source order, ranking. A tap does not wait for
    # them before it searches: a name in Cyrillic already is searched as it is (the album's barcode the catalog is
    # asked for only tells a Latin name's country), and a Latin one is searched at once with the catalog's names
    # while its Cyrillic original is looked for — the search is kept if the original changes nothing
    steps = {"catalog": time.monotonic() - t0}
    spec: asyncio.Task[list[Candidate]] | None = None
    spec_trouble: list[str] = []
    from app.matching.normalize import is_russian_query

    if is_russian_query(q):
        pq = q
    else:
        if not probe:
            spec = asyncio.create_task(_search_all(ref, recording, svc.provider_order(q), q, spec_trouble))
        q0 = q
        try:
            meta = meta_from_catalog(t)
            if not ref.startswith("lib:"):
                meta = await asyncio.wait_for(complete_meta(meta), META_TIMEOUT_S)
        except TimeoutError:
            meta = {}
        try:
            q, pq = await asyncio.wait_for(russian_original(q, meta.get("upc")), NAME_TIMEOUT_S)
        except TimeoutError:
            from app.catalog.yandex import cis_release
            pq = q.model_copy(update={"lang": "ru"}) if cis_release(meta.get("upc"), q.isrc) else q
        steps["names"] = time.monotonic() - t0
        if spec is not None and not (q == q0 and pq == q0):
            spec.cancel()  # the original changed the search: it is made again with it
            spec = None
    providers = svc.provider_order(pq)
    regular = {p.name for p in providers}
    fallback = [p for p in svc.provider_order(pq, for_download=True) if p.name not in regular]
    if not providers and not fallback:
        raise ListenError(502, "Нет доступных источников для поиска файла")

    def full(cands: list[Candidate]) -> Choice:
        return Choice(ref, "full", q.artist, q.title, q.duration_s or cands[0].duration_s, candidate=cands[0],
                      alternates=cands[1:], query=query, pquery=pq.model_dump(), recording=recording,
                      verified=cands[0].enriched)

    trouble: list[str] = []  # sources that failed, files that didn't open: "not found" may not be true
    if probe:
        for p in providers:
            cands = await _search(ref, p, pq, trouble, recording=recording)
            if strict:
                cands = [cand for cand in cands if _same_recording(pq, cand)]
            if cands:
                choice = full(cands)
                if await _opens_bounded(choice):
                    return choice
                trouble.append(f"{p.name}: файлы не открываются")
    else:
        if spec is not None:
            cands = await spec
            trouble.extend(spec_trouble)
        else:
            cands = await _search_all(ref, recording, providers, pq, trouble)
        steps["search"] = time.monotonic() - t0
        log.info("listen %s: picked in %s", ref, ", ".join(f"{k} {v:.1f} s" for k, v in steps.items()))
        if cands:
            return full(cands)

    # The download fallback may have a full recording even when the regular sources fail.
    for p in fallback:
        cands = await _search(ref, p, pq, trouble, timeout_s=FALLBACK_TIMEOUT_S, recording=recording)
        if strict:
            cands = [cand for cand in cands if _same_recording(pq, cand)]
        if cands:
            choice = full(cands)
            if not probe or await _opens_bounded(choice):
                return choice
            trouble.append(f"{p.name}: файлы не открываются")

    # A queued heart may have finished importing while these sources were being searched.
    path, nd_song_id = await _library_file(query)
    if path:
        return Choice(ref, "library", q.artist, q.title, q.duration_s, path=path, nd_song_id=nd_song_id, query=query)
    if trouble:
        raise ListenError(502, "Источники недоступны: " + "; ".join(trouble))
    raise RecordingUnavailable("Полная версия трека не найдена ни в одном источнике")


META_TIMEOUT_S = 1.5
NAME_TIMEOUT_S = 3.0
SEARCH_TIMEOUT_S = 6.0
FALLBACK_TIMEOUT_S = 8.0
# A YouTube file takes ~8 s to extract, then its URL may wait for an ad before the video (`ytdlp.MAX_AD_WAIT_S`)
OPEN_TIMEOUT_S = 25.0


async def _opens_bounded(choice: Choice) -> bool:
    try:
        return await asyncio.wait_for(_opens(choice), OPEN_TIMEOUT_S)
    except TimeoutError:
        return False


async def _search(ref: str, p, pq: TrackQuery, trouble: list[str], *, timeout_s: float | None = None,
                  recording: str | None = None) -> list[Candidate]:
    try:
        cands, err = await asyncio.wait_for(search_provider(p, pq, 0, limit=SEARCH_LIMIT, enrich_top_n=0),
                                          SEARCH_TIMEOUT_S if timeout_s is None else timeout_s)
    except TimeoutError:
        cands, err = [], f"{p.name}: превышено время ожидания поиска"
    if err:
        log.info("listen %s: %s", ref, err)
        trouble.append(err)
    ready = [c for c in cands if not _deferred(recording, c)]
    if cands and not ready:
        trouble.append(f"{p.name}: найденные файлы временно недоступны")
    return ready[:MAX_TRIES_PER_PROVIDER]


async def _search_all(ref: str, recording: str, providers: list, pq: TrackQuery, trouble: list[str]) -> list[Candidate]:
    """Return the preferred source promptly and retain other results for later failures."""
    tasks = [asyncio.create_task(_search(ref, p, pq, trouble, recording=recording)) for p in providers]
    retained = False
    try:
        for t in tasks:
            if cands := await t:
                if recording not in _source_lookups:
                    retained = True
                    _start_source_lookup(recording, _finish_source_search(recording, pq, tasks))
                return cands
        return []
    finally:
        if not retained:
            for t in tasks:
                t.cancel()


_source_lookups: dict[str, asyncio.Task] = {}
_source_backoff: dict[tuple[str, str, str], float] = {}


def _start_source_lookup(recording: str, work: Coroutine[Any, Any, None]) -> None:
    task = _spawn(work, background=True)
    _source_lookups[recording] = task
    task.add_done_callback(lambda _: _source_lookups.pop(recording, None))


def _ensure_sources(choice: Choice) -> None:
    recording = choice.recording
    if recording and choice.pquery and choice.candidate and recording not in _source_lookups and not choice.sources_checked:
        choice.sources_checked = True
        _start_source_lookup(recording, _look_up_other_sources(choice, recording, choice.candidate))


async def _look_up_other_sources(choice: Choice, recording: str, primary: Candidate) -> None:
    query = TrackQuery.model_validate(choice.pquery)
    saved = await cache_get(f"listen-sources:{recording}")
    if saved is not None:
        if not any(entry["provider"] == primary.provider and entry["provider_id"] == primary.provider_id
                   for entry in saved) and _same_recording(query, primary):
            await _remember_sources(recording, query, [primary, *choice.alternates])
        return
    trouble: list[str] = []
    other = [p for p in svc.provider_order(query) if p.name != primary.provider]
    results = await asyncio.gather(*(_search(choice.ref, p, query, trouble, recording=recording) for p in other))
    await _remember_sources(recording, query, [primary, *choice.alternates, *(c for group in results for c in group)])


def _defer_source(choice: Choice, candidate: Candidate) -> None:
    if choice.recording is not None:
        if len(_source_backoff) >= 8192:
            _source_backoff.pop(next(iter(_source_backoff)))
        _source_backoff[choice.recording, candidate.provider, candidate.provider_id] = time.monotonic() + SOURCE_BACKOFF_S


def _deferred(recording: str | None, candidate: Candidate) -> bool:
    return recording is not None and (
        _source_backoff.get((recording, candidate.provider, candidate.provider_id), 0) > time.monotonic()
    )


def _same_recording(query: TrackQuery, candidate: Candidate) -> bool:
    """Require a close duration and exact normalized credits, title and edition for cross-source fallback."""
    if not query.duration_s or not candidate.duration_s or abs(query.duration_s - candidate.duration_s) > 2:
        return False
    q_artist, c_artist = normalize_artist(query.artist), normalize_artist(candidate.artist)
    q_title, c_title = normalize_title(query.title), normalize_title(candidate.title)
    if (q_artist.latin != c_artist.latin or q_title.latin != c_title.latin
            or (query.version or q_title.version) != c_title.version
            or set(q_artist.featured + q_title.featured) != set(c_artist.featured + c_title.featured)):
        return False
    explicit = candidate.explicit if candidate.explicit is not None else explicit_marker(candidate.title)
    if query.explicit is not None and explicit is not None and query.explicit != explicit:
        return False
    other_isrc = candidate.extra.get("isrc")
    if query.isrc and isinstance(other_isrc, str) and clean_isrc(other_isrc) not in (None, clean_isrc(query.isrc)):
        return False
    return not needs_uncensored(query) or trusted_original(candidate, get_settings().cfg.matching)


async def _finish_source_search(recording: str, query: TrackQuery, tasks: list[asyncio.Task[list[Candidate]]]) -> None:
    found = await asyncio.gather(*tasks)
    await _remember_sources(recording, query, [cand for group in found for cand in group])


async def _remember_sources(recording: str, query: TrackQuery, results: list[Candidate]) -> None:
    candidates = [cand for cand in results if _same_recording(query, cand)]
    key = f"listen-sources:{recording}"
    old = await cache_get(key) or []
    searched = {cand.provider for cand in candidates}
    await cache_set(key, [cand.model_dump(mode="json") for cand in candidates]
                    + [entry for entry in old if entry.get("provider") not in searched], KEEP_S)


async def _backup_choice(choice: Choice) -> Choice | None:
    if choice.recording is None or not choice.pquery:
        return None
    if task := _source_lookups.get(choice.recording):
        try:
            await asyncio.shield(task)
        except Exception:
            pass  # cached alternatives are optional
    query = TrackQuery.model_validate(choice.pquery)
    current = _choices.get(choice.ref)
    if current is not None and current is not choice:
        return current  # another request already picked a new file for this ref
    saved = await cache_get(f"listen-sources:{choice.recording}") or []
    candidates = [*choice.alternates, *(Candidate.model_validate(entry) for entry in saved)]
    order = _source_names(choice.pquery)
    for provider in order:
        for cand in candidates:
            if (cand.provider != provider or _same_file(cand, choice.candidate)
                    or _deferred(choice.recording, cand) or not _same_recording(query, cand)):
                continue
            current = _choices.get(choice.ref)
            if current is not None and current is not choice:
                return current
            next_choice = replace(choice, candidate=cand, alternates=[], source=None, local=None, heard=False,
                                  verified=cand.enriched, checking=None, sources_checked=False, at=time.monotonic())
            _choices[choice.ref] = next_choice
            return next_choice
    return None


async def _resolve_hitmoz(ref: str, id_: str, probe: bool) -> Choice:
    """hitmoz catalog track (Deezer was unavailable): the ref is the hitmoz song itself."""
    cand = Candidate(provider="hitmoz", provider_id=id_, artist="", title="", page_url=f"/song/{id_}")
    try:
        cand = await svc.providers["hitmoz"].enrich(cand)
    except Exception as e:
        raise ListenError(502, f"hitmoz: {e}") from e
    choice = Choice(ref, "full", "", str(cand.extra.get("full_title") or ""), cand.duration_s, candidate=cand,
                    verified=True)
    if probe and not await _opens(choice):
        raise ListenError(404, "Файл трека недоступен")
    return choice


async def _library_file(query: dict) -> tuple[Path | None, str | None]:
    """The owned file of this track and its Navidrome song id."""
    async with session_scope() as s:
        t = await find_owned(s, artist=query["artist"], title=query["title"], duration_s=query.get("duration_s"),
                             isrc=query.get("isrc"), catalog_ref=query.get("catalog_ref"))
        rel, nd_song_id = (t.path, t.nd_song_id) if t else (None, None)
    path = get_settings().library_dir / rel if rel else None
    return (path, nd_song_id) if path and path.is_file() else (None, None)


async def _opens(choice: Choice) -> bool:
    """Ask the upstream for one byte: a dead link or an HTML page instead of audio moves on to the next file. A
    source without plain HTTP audio: download the copy that will play."""
    if _uses_local_copy(choice):
        try:
            await _predownload(choice)
        except Exception as e:
            log.info("listen %s: no local copy: %s", choice.ref, e)
            return False
        return True
    try:
        resp, _ = await open_stream(choice, PROBE_RANGE)
    except Exception as e:
        log.info("listen %s: %s does not open: %s", choice.ref,
                 choice.candidate.page_url if choice.candidate else choice.kind, e)
        return False
    try:
        if resp.status_code not in (200, 206):
            return False
        return bool(await asyncio.wait_for(anext(resp.aiter_raw(), b""), FIRST_AUDIO_S))
    except (TimeoutError, httpx.HTTPError):
        return False
    finally:
        await resp.aclose()


async def _upstream(choice: Choice) -> StreamSource:
    assert choice.kind == "full" and choice.candidate is not None
    provider = svc.providers.get(choice.candidate.provider)
    if provider is None:
        raise ListenError(404, "Источник отключён")
    try:
        return await provider.stream(choice.candidate)
    except Exception as e:
        raise ListenError(502, str(e)) from e


# --- local copies ----------------------------------------------------------------------------------------------


def _downloads_only(c: Choice) -> bool:
    provider = svc.providers.get(c.candidate.provider) if c.kind == "full" and c.candidate else None
    return provider is not None and not getattr(provider, "streams", True)


def _uses_local_copy(c: Choice) -> bool:
    return _downloads_only(c) or (is_file_ref(c.ref) and
                                  (c.ref in _fetching or (c.local is not None and c.local.is_file())))


@dataclass(frozen=True)
class PreparedAudio:
    path: Path
    suffix: str


def prepared_file(ref: str, candidate: Candidate | None = None) -> PreparedAudio | None:
    """A whole server-side copy of this exact file. A partial stream isn't ready to be filed."""
    c = _choices.get(ref)
    if (c is not None and (candidate is None or _same_file(c.candidate, candidate))
            and c.local is not None and c.local.is_file()):
        return PreparedAudio(c.local, c.local.suffix)
    cp = _copies.get(ref)
    if (cp is not None and (candidate is None or _same_file(cp.cand, candidate))
            and cp.done and cp.failed is None and cp.have and cp.path.is_file()):
        if suffix := STREAM_SUFFIXES.get(cp.media_type):
            return PreparedAudio(cp.path, suffix)
    # a whole copy on the disk the other process made (the api's player, the worker's download): its note says which
    # file it is (`COPY_NOTE`)
    path = get_settings().staging_dir / LOCAL_DIR / _local_name(ref) / COPY_NAME
    try:
        note = json.loads((path.parent / COPY_NOTE).read_text())
        size = path.stat().st_size
    except (OSError, ValueError):
        return None
    if size and note.get("size") == size and (candidate is None or (note.get("provider"), note.get("provider_id")) == (
            candidate.provider, candidate.provider_id)) and (suffix := STREAM_SUFFIXES.get(note.get("media_type") or "")):
        return PreparedAudio(path, suffix)
    return None


async def prepare_file(cand: Candidate) -> PreparedAudio:
    """Finish the copy already playing, or download the candidate once if nobody opened it yet."""
    ref = file_ref(cand.provider, cand.provider_id)
    if ready := prepared_file(ref):
        await asyncio.to_thread(os.utime, ready.path)
        return ready
    await remember_files([cand])
    c = _choices.get(ref) or Choice(ref, "full", cand.artist, cand.title, cand.duration_s, candidate=cand, verified=True)
    cp = _copies.get(ref)
    if cp is not None and cp.failed is None and _same_file(cp.cand, cand):
        cp = _server_copy(c, background=False)
        await cp.until(cp.settled)
        if cp.failed is not None:
            raise cp.failed
        if ready := prepared_file(ref):
            return ready
    path = await _predownload(c)
    if current := _choices.get(ref):
        current.local = path
    else:
        _choices[ref] = c
    return PreparedAudio(path, path.suffix)


async def stage_prepared_file(ref: str, folder: Path) -> Path:
    """Copy the completed download for tagging, leaving the player's copy untouched."""
    audio = prepared_file(ref)
    if audio is None:
        raise ListenError(409, "Файл ещё не скачан или уже удалён из временного хранилища. Нажмите «Скачать».")
    return await _stage_audio(audio, folder)


async def stage_playback_file(candidate: Candidate, folder: Path, track_ref: str | None) -> Path | None:
    """Reuse the selected recording's completed or in-flight copy, keeping the playback file untouched."""
    exact = file_ref(candidate.provider, candidate.provider_id)
    refs = list(dict.fromkeys([exact, *(await _track_addresses(track_ref) if track_ref and track_ref != exact else [])]))
    for ref in refs:
        if audio := prepared_file(ref, candidate):
            return await _stage_audio(audio, Path(mkdtemp(prefix="playback-", dir=folder)))
    for ref in refs:
        if (copy := _copies.get(ref)) is not None and not copy.settled() and _same_file(copy.cand, candidate):
            copy.whole()
            copy.prio.foreground()
            _stir()
            await copy.until(copy.settled)
            if audio := prepared_file(ref, candidate):
                return await _stage_audio(audio, Path(mkdtemp(prefix="playback-", dir=folder)))
        if (job := _fetching.get(ref)) is not None and _same_file(job.candidate, candidate):
            job.prio.foreground()
            try:
                path = await asyncio.shield(job.task)
            except ListenError:
                continue
            return await _stage_audio(PreparedAudio(path, path.suffix), Path(mkdtemp(prefix="playback-", dir=folder)))
    return None


async def _stage_audio(audio: PreparedAudio, folder: Path) -> Path:
    from app.library.probe import ProbeError, remux_webm

    if audio.suffix == ".webm":  # WebM commonly carries Opus; keep the audio and use a container that supports tags.
        dest = folder / "audio.ogg"
        try:
            await remux_webm(audio.path, dest)
        except (ProbeError, TimeoutError) as e:
            raise ListenError(502, "Не удалось подготовить аудио к сохранению") from e
    else:
        dest = folder / ("audio" + audio.suffix)
        try:
            await asyncio.to_thread(shutil.copyfile, audio.path, dest)
        except FileNotFoundError as e:
            raise ListenError(409, "Временный файл уже удалён. Скачайте его ещё раз.") from e
    return dest


async def _predownload(c: Choice) -> Path:
    """The local copy of the file (downloaded now, or already). Concurrent callers share one download; a user
    request joining a background one lifts it."""
    if c.local is not None and c.local.is_file():
        await asyncio.to_thread(os.utime, c.local)  # in use: pruned last
        return c.local
    job = _fetching.get(c.ref)
    if job is None:
        assert c.candidate is not None
        prio = Priority(in_background())
        task = asyncio.create_task(_fetch_local(c.ref, c.candidate), context=priority_context(prio))
        job = _Job(task, prio, c.candidate)
        _fetching[c.ref] = job

        def done(t: asyncio.Task, _ref: str = c.ref) -> None:
            _fetching.pop(_ref, None)
            if not t.cancelled():
                t.exception()  # retrieved here: nobody may be awaiting it any more

        job.task.add_done_callback(done)
    elif not in_background():
        job.prio.foreground()
    c.local = await asyncio.shield(job.task)
    return c.local


async def _fetch_local(ref: str, cand: Candidate) -> Path:
    provider = svc.providers.get(cand.provider)
    if provider is None:
        raise ListenError(404, "Источник отключён")
    t0 = time.monotonic()
    root = get_settings().staging_dir / LOCAL_DIR
    await asyncio.to_thread(prune_local, root, _busy_dirs() - {_local_name(ref)})
    dest = root / _local_name(ref)
    try:
        await asyncio.to_thread(dest.mkdir, parents=True, exist_ok=True)
        path = await provider.fetch(cand, dest)
        if (await asyncio.to_thread(path.stat)).st_size == 0:
            raise ValueError("Источник вернул пустой файл")
    except Exception as e:
        await asyncio.to_thread(shutil.rmtree, dest, True)
        raise ListenError(502, f"{cand.provider}: {e}") from e
    log.info("listen %s: local copy %s (%.1f s)", ref, path.name, time.monotonic() - t0)
    return path


def _local_name(ref: str) -> str:
    return hashlib.sha1(ref.encode()).hexdigest()[:16]


def copies_usage() -> tuple[int, int]:
    """What the server copies take: how many tracks and how many bytes."""
    root = get_settings().staging_dir / LOCAL_DIR
    count = total = 0
    if root.is_dir():
        for d in root.iterdir():
            if d.is_dir():
                sizes = [f.stat().st_size for f in d.iterdir() if f.is_file()]
                count += 1
                total += sum(sizes)
    return count, total


def prune_local(root: Path, busy: set[str], now: float | None = None) -> None:
    """Copies unused for `LOCAL_KEEP_S` go, and the least recently used ones while all of them take more than
    `LOCAL_MAX_BYTES`. Copies being downloaded (`busy` folder names) stay."""
    if not root.is_dir():
        return
    now = time.time() if now is None else now
    dirs: list[tuple[float, int, Path]] = []
    for d in root.iterdir():
        if d.name in busy or not d.is_dir():
            continue
        files = [f.stat() for f in d.iterdir() if f.is_file()]
        dirs.append((max((st.st_mtime for st in files), default=0.0), sum(st.st_size for st in files), d))
    total = sum(size for _, size, _ in dirs)
    for used, size, d in sorted(dirs, key=lambda x: x[0]):
        if now - used < LOCAL_KEEP_S and total <= LOCAL_MAX_BYTES:
            break
        shutil.rmtree(d, ignore_errors=True)
        total -= size


# --- server copies ---------------------------------------------------------------------------------------------


def _same_file(a: Candidate | None, b: Candidate | None) -> bool:
    # the heard file's track page check replaces the candidate with an enriched one: still the same file
    return a is not None and b is not None and (a.provider, a.provider_id) == (b.provider, b.provider_id)


def _server_copy(c: Choice, *, background: bool, head: bool = False) -> _Copy:
    """The server copy of the picked file: being downloaded or on disk, else started now. A played track's request
    lifts a copy loaded ahead. `head`: only the start of the file is wanted for now (`HEAD_BYTES`); any other
    request has a copy that stopped there go on to the end."""
    cp = _copies.get(c.ref)
    if cp is not None and _same_file(cp.cand, c.candidate) and cp.failed is None and (
            not cp.done or cp.path.is_file()):
        if not head:
            cp.whole()
        if not background:
            cp.prio.foreground()
            _stir()
        return cp
    if cp is not None and cp.task is not None:
        cp.task.cancel()
    for ref in [r for r, old in _copies.items() if old.failed or (old.done and not old.path.is_file())]:
        del _copies[ref]
    assert c.candidate is not None
    if (kept := _kept_copy(c)) is not None:
        _copies[c.ref] = kept
        return kept
    cp = _Copy(c.candidate, get_settings().staging_dir / LOCAL_DIR / _local_name(c.ref) / COPY_NAME,
               Priority(background), limit=HEAD_BYTES if head else None)
    if not head:
        cp.go.set()
    cp.task = _spawn(_download_copy(c, cp), prio=cp.prio)
    _copies[c.ref] = cp
    _stir()
    return cp


def _kept_copy(c: Choice) -> _Copy | None:
    """A whole copy of the picked file left on disk (`COPY_NOTE`): the Hub restarted, or the pick was forgotten in
    the meantime. It is served as it is, and its use keeps it (`prune_local` goes by the time of the last use)."""
    assert c.candidate is not None
    path = get_settings().staging_dir / LOCAL_DIR / _local_name(c.ref) / COPY_NAME
    try:
        note = json.loads((path.parent / COPY_NOTE).read_text())
        size = path.stat().st_size
    except (OSError, ValueError):
        return None
    if (note.get("provider"), note.get("provider_id")) != (c.candidate.provider, c.candidate.provider_id) \
            or note.get("size") != size or not size:
        return None
    os.utime(path)
    return _Copy(c.candidate, path, Priority(False), media_type=note.get("media_type") or "audio/mpeg", size=size,
                 have=size, done=True)


def _note_copy(cp: _Copy) -> None:
    (cp.path.parent / COPY_NOTE).write_text(json.dumps({"provider": cp.cand.provider, "provider_id": cp.cand.provider_id,
                                                        "media_type": cp.media_type, "size": cp.have}))


def _urgent() -> bool:
    """A copy of a played track is downloading."""
    return any(not cp.prio.background and not cp.settled() for cp in _copies.values())


def _stir() -> None:
    """Copies changed: wake those loaded ahead once no copy of a played track downloads."""
    global _calm
    if _calm is None:
        _calm = asyncio.Event()
    if _urgent():
        _calm.clear()
    else:
        _calm.set()


async def _yield_to_played(cp: _Copy) -> None:
    """A copy loaded ahead waits while a copy of a played track downloads."""
    while cp.prio.background and _urgent():
        _stir()
        assert _calm is not None
        try:  # re-checked now and then: the copy may have been lifted meanwhile
            await asyncio.wait_for(_calm.wait(), CALM_RECHECK_S)
        except TimeoutError:
            pass


def _busy_dirs() -> set[str]:
    return ({_local_name(r) for r in _fetching}
            | {cp.path.parent.name for cp in _copies.values() if not cp.settled()})


async def _download_copy(c: Choice, cp: _Copy) -> None:
    """Download the file to its server copy from start to end. A connection the source drops midway is resumed
    from the byte it broke at; a copy loaded ahead pauses while one of a played track downloads."""
    t0 = time.monotonic()
    f = None
    try:
        await asyncio.to_thread(prune_local, cp.path.parent.parent, _busy_dirs())
        await asyncio.to_thread(cp.path.parent.mkdir, parents=True, exist_ok=True)
        f = await asyncio.to_thread(open, cp.path, "wb", buffering=0)  # unbuffered: readers see every byte written
        drops = 0
        while not cp.done:
            if not _same_file(c.candidate, cp.cand):
                if cp.have:
                    raise ListenError(502, "Файл трека сменился")
                cp.cand = c.candidate  # the first file didn't open: the pick moved on to the next one
            resp, src = await open_stream(c, f"bytes={cp.have}-", allow_alternates=not cp.have and not c.heard)
            try:
                if not cp.have:
                    cp.cand = c.candidate
                if resp.status_code == 416:
                    raise ListenError(502, "Источник не отдаёт файл (HTTP 416)")
                if cp.have and ((span := _span(resp)) is None or span[0] != cp.have):
                    raise ListenError(502, "Источник не продолжил передачу с нужной позиции")
                if cp.size is None:
                    cp.size = _total_size(resp)
                    ctype = resp.headers.get("content-type", "").split(";")[0].strip().lower()
                    cp.media_type = ctype if ctype.startswith("audio/") else src.media_type
                    await cp.changed()
                async for chunk in _audio_chunks(resp):
                    await _yield_to_played(cp)
                    await asyncio.to_thread(_write_all, f, chunk)
                    cp.have += len(chunk)
                    drops = 0
                    await cp.changed()
                    if cp.limit is not None and cp.have >= cp.limit:
                        break  # the start is here: the source is let go of until the rest is wanted
            except (httpx.TransportError, TimeoutError) as e:
                if not cp.have:
                    raise ListenError(502, "Источник не отдал первые байты аудио") from e
                drops += 1
                if drops > COPY_RESUMES:
                    raise ListenError(502, f"Источник обрывает передачу файла: {e!r}") from e
                log.info("listen %s: upstream dropped at %d of %s bytes (%r), resuming", c.ref, cp.have, cp.size, e)
                continue
            finally:
                await resp.aclose()
            if not cp.have:
                raise ListenError(502, "Источник вернул пустой файл")
            if cp.limit is not None and cp.have >= cp.limit and (cp.size is None or cp.have < cp.size):
                try:  # stopped at the start: it goes on from this byte when the track is played or is the next one
                    await asyncio.wait_for(cp.go.wait(), HEAD_KEEP_S)
                except TimeoutError:
                    raise ListenError(503, "Начало файла не понадобилось") from None
                continue
            if cp.size is None or cp.have >= cp.size:
                cp.size = cp.have
                await asyncio.to_thread(_note_copy, cp)
                cp.done = True
        log.info("listen %s: server copy %d KB (%.1f s%s)", c.ref, cp.have // 1024, time.monotonic() - t0,
                 ", ahead" if cp.prio.background else "")
    except ListenError as e:
        cp.failed = e
    except Exception as e:
        log.warning("listen %s: server copy failed: %r", c.ref, e)
        cp.failed = ListenError(502, f"Не удалось скачать файл: {e}")
    finally:
        if f is not None:
            f.close()
        if not cp.done and cp.failed is None:
            cp.failed = ListenError(503, "Загрузка файла прервана")
        _stir()
        await asyncio.shield(cp.changed())


async def _audio_chunks(resp: httpx.Response) -> AsyncIterator[bytes]:
    chunks = resp.aiter_raw()
    if first := await asyncio.wait_for(anext(chunks, b""), FIRST_AUDIO_S):
        yield first
    async for chunk in chunks:
        yield chunk


def _write_all(f, data: bytes) -> None:
    view = memoryview(data)
    while view:
        view = view[f.write(view):]


def _total_size(resp: httpx.Response) -> int | None:
    """The whole file's size: from Content-Range of a 206, from Content-Length of a 200."""
    if resp.status_code == 206:
        total = resp.headers.get("content-range", "").rpartition("/")[2]
        return int(total) if total.isdigit() else None
    length = resp.headers.get("content-length", "")
    return int(length) if length.isdigit() else None


def _byte_range(header: str | None, size: int) -> tuple[int, int] | Literal[False] | None:
    """The client's single `bytes=` range as (first, last); None: the whole file; False: not satisfiable."""
    spec = (header or "").strip()
    if not spec.startswith("bytes=") or "," in spec:
        return None
    a, sep, b = spec.removeprefix("bytes=").strip().partition("-")
    try:
        if not sep:
            return None
        if not a:  # the last `b` bytes
            n = int(b)
            return (max(size - n, 0), size - 1) if n > 0 and size else False
        first, last = int(a), int(b) if b else size - 1
    except ValueError:
        return None
    if first >= size or last < first:
        return False
    return first, min(last, size - 1)


async def _from_copy(c: Choice, cp: _Copy, range_header: str | None, *, foreground: bool) -> Media | None:
    """The answer from the server copy; None when the range lies far past what it has (the upstream serves it)."""
    if cp.done:
        await asyncio.to_thread(os.utime, cp.path)  # in use: pruned last
        return Media(c, path=cp.path, media_type=cp.media_type)
    if cp.size is None:
        return None
    rng = _byte_range(range_header, cp.size)
    if rng is False:
        return Media(c, status=416, headers={"content-range": f"bytes */{cp.size}"})
    first, last = rng or (0, cp.size - 1)
    if first > cp.have + SEEK_WAIT_BYTES:
        return None
    headers = {"content-length": str(last - first + 1)}
    if rng:
        headers["content-range"] = f"bytes {first}-{last}/{cp.size}"
    return Media(c, body=_read_copy(c, cp, first, last, foreground=foreground), status=206 if rng else 200,
                 headers=headers, media_type=cp.media_type)


async def _read_copy(c: Choice, cp: _Copy, first: int, last: int, *, foreground: bool) -> AsyncIterator[bytes]:
    """Bytes `first`..`last` of the server copy as they arrive; when the copy fails, the rest from the upstream."""
    pos = first
    if foreground:
        cp.readers += 1
    f = None
    try:
        f = await asyncio.to_thread(open, cp.path, "rb")
        while pos <= last:
            if pos >= cp.have:
                await cp.until(lambda pos=pos: cp.have > pos or cp.settled())
                if pos >= cp.have:
                    break
            data = await asyncio.to_thread(_read_at, f, pos, min(COPY_CHUNK, cp.have - pos, last + 1 - pos))
            if not data:
                break
            yield data
            pos += len(data)
    finally:
        if f is not None:
            f.close()
        if foreground:
            cp.readers -= 1
            if not cp.readers and not cp.settled():
                cp.prio.background = True  # nobody plays it any more (a skip): a played track's copy goes first
                _stir()
    if pos <= last and _same_file(c.candidate, cp.cand):
        resp, _ = await open_stream(c, f"bytes={pos}-{last}", allow_alternates=False)
        await _require_offset(resp, pos)
        async for chunk in _relay(c, resp):
            yield chunk


def _read_at(f, pos: int, n: int) -> bytes:
    f.seek(pos)
    return f.read(n)


def _upstream_media(c: Choice, resp: httpx.Response, src: StreamSource) -> Media:
    headers = {k: resp.headers[k] for k in ("content-length", "content-range") if k in resp.headers}
    ctype = resp.headers.get("content-type", "").split(";")[0].strip().lower()
    return Media(c, body=_relay(c, resp), status=resp.status_code, headers=headers,
                 media_type=ctype if ctype.startswith("audio/") else src.media_type, close=resp.aclose)


async def _relay(c: Choice, resp: httpx.Response) -> AsyncIterator[bytes]:
    """The upstream body; a connection the source drops midway is resumed from the byte it broke at."""
    span = _span(resp)
    pos = span[0] if span else 0
    drops = 0
    try:
        while True:
            try:
                async for chunk in resp.aiter_raw():
                    pos += len(chunk)
                    drops = 0
                    yield chunk
                return
            except httpx.TransportError as e:
                drops += 1
                if span is None or drops > COPY_RESUMES:
                    raise
                log.info("listen %s: upstream dropped at byte %d (%r), resuming", c.ref, pos, e)
            await resp.aclose()
            resp, _ = await open_stream(c, f"bytes={pos}-{span[1]}", allow_alternates=False)
            await _require_offset(resp, pos)
    finally:
        await resp.aclose()


async def _require_offset(resp: httpx.Response, pos: int) -> None:
    span = _span(resp)
    if span is None or span[0] != pos:
        await resp.aclose()
        raise ListenError(502, "Источник не продолжил передачу с нужной позиции")


def _span(resp: httpx.Response) -> tuple[int, int] | None:
    """The bytes (first, last) of the file a response carries, when it says."""
    if resp.status_code == 206:
        rng = resp.headers.get("content-range", "").removeprefix("bytes ").partition("/")[0]
        a, _, b = rng.partition("-")
        return (int(a), int(b)) if a.isdigit() and b.isdigit() else None
    length = resp.headers.get("content-length", "")
    return (0, int(length) - 1) if resp.status_code == 200 and length.isdigit() and int(length) else None


# --- after listening -------------------------------------------------------------------------------------------


async def _after_heard(c: Choice) -> None:
    """Check the heard file on its track page (hitmoz: Explicit/Clean, version, real bitrate): a download takes
    it only if it still fits. One that doesn't is replaced, for the next listen, by a download-grade pick."""
    if not c.verified and c.candidate is not None:
        cand = c.candidate
        provider = svc.providers.get(cand.provider)
        try:
            enriched = await provider.enrich(cand) if provider else None
        except Exception as e:
            log.info("listen %s: track page check failed: %r", c.ref, e)
            enriched = None
        if enriched is not None and c.candidate is cand:
            if _still_fits(c, enriched):
                c.candidate, c.verified = enriched, True
            else:
                log.info("listen %s: %s does not fit after its track page, picking again", c.ref, cand.page_url)
                await _repick(c, provider)
                return
    await _save(c)


def _still_fits(c: Choice, enriched: Candidate) -> bool:
    if not c.pquery:
        return True
    q = TrackQuery(**c.pquery)
    if q.explicit and enriched.explicit is False:
        return False  # the censored copy of an explicit track
    return bool(rank_candidates(q, [enriched], get_settings().cfg.matching))


async def _repick(c: Choice, provider) -> None:
    """A download-grade search (track pages of the top results) of the source the heard file came from; the best
    file is remembered for the next listen (the one playing now stays: a seek must hit the same file)."""
    assert c.pquery is not None
    cands, err = await search_provider(provider, TrackQuery(**c.pquery), 0, limit=SEARCH_LIMIT)
    if err or not cands:
        await cache_delete(_key(c.ref))
        return
    best = cands[0]
    await _save(replace(c, candidate=best, alternates=cands[1:MAX_TRIES_PER_PROVIDER], source=None, heard=False,
                        verified=best.enriched, checking=None))


# --- remembered picks ------------------------------------------------------------------------------------------


def _key(ref: str) -> str:
    return f"listen:{ref}"


async def recording_checks(s: AsyncSession, refs: list[str]) -> dict[str, tuple[datetime, bool]]:
    """The time and availability from each address's last unexpired recording search."""
    keys = {_key(ref): ref for ref in refs}
    batch = list(keys)
    now = utcnow()
    kind = CatalogCache.payload_json["v"]["kind"].as_string()
    checks = {}
    for i in range(0, len(batch), 500):
        rows = await s.execute(select(CatalogCache.key, CatalogCache.fetched_at, CatalogCache.ttl_s, kind).where(
            CatalogCache.key.in_(batch[i:i + 500]),
            kind.in_(("full", MISSING_RECORDING)),
        ))
        checks.update({keys[key]: (at, result == "full") for key, at, ttl, result in rows
                       if at + timedelta(seconds=ttl) >= now})
    return checks


async def _save(c: Choice) -> None:
    if c.kind == "library":
        await cache_delete(_key(c.ref))
        return
    await cache_set(_key(c.ref), _payload(c), KEEP_S)


def _payload(c: Choice) -> dict:
    return {
        "kind": c.kind,
        "artist": c.artist,
        "title": c.title,
        "duration_s": c.duration_s,
        "candidate": c.candidate.model_dump(mode="json") if c.candidate else None,
        "alternates": [a.model_dump(mode="json") for a in c.alternates],
        "query": c.query,
        "pquery": c.pquery,
        "recording": c.recording,
        "heard": c.heard,
        "verified": c.verified,
        "sources": _source_names(c.pquery),
    }


def _source_names(pquery: dict | None) -> list[str]:
    """The sources a pick of this query is searched in, in order (with the download fallback)."""
    return [p.name for p in svc.provider_order(TrackQuery(**pquery), for_download=True)] if pquery else []


def _outranked(provider: str, pquery: dict, sources: list[str]) -> bool:
    """The source order has changed since the pick: a source now ahead of the picked file's was not ahead of it
    then (the first source in order that has the track wins, those behind it were not asked)."""
    order = _source_names(pquery)
    ahead = order[: order.index(provider)] if provider in order else order
    asked = sources[: sources.index(provider)] if provider in sources else sources
    return not set(ahead) <= set(asked)


async def _remembered(ref: str, *, library: bool = True) -> Choice | None:
    """The remembered pick (without an opened link); an owned track plays from the library instead."""
    try:
        d = await cache_get(_key(ref))
        if not d or d.get("kind") != "full":
            return None
        cand = Candidate(**d["candidate"]) if d.get("candidate") else None
        c = Choice(ref, "full", d["artist"], d["title"], d.get("duration_s"), candidate=cand,
                   alternates=[Candidate(**a) for a in d.get("alternates") or []], query=d.get("query"),
                   pquery=d.get("pquery"), recording=d.get("recording"), heard=bool(d.get("heard")),
                   verified=bool(d.get("verified")))
        if c.pquery and c.recording is None:
            c.recording = (await _track_addresses(ref))[0]
    except Exception as e:
        log.warning("listen %s: remembered pick unreadable: %r", ref, e)
        return None
    if c.kind == "full" and c.candidate is None:
        return None
    if (c.kind == "full" and c.candidate is not None and c.pquery
            and _outranked(c.candidate.provider, c.pquery, d.get("sources") or [])):
        return None  # picked before a preferred source was added (YouTube after SoundCloud for Russian tracks)
    if library and c.query:
        path, nd_song_id = await _library_file(c.query)
        if path:
            return Choice(ref, "library", c.artist, c.title, c.duration_s, path=path, nd_song_id=nd_song_id,
                          query=c.query)
    return c


def _register() -> None:
    from app import relay

    relay.on("forget")(_forget_relayed)


_register()
