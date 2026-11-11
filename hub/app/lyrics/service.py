"""Lyrics of the library's tracks, kept in the Hub's database so they open at once and without the internet.

A track's row (`models.Lyrics`) holds what was found — or that nothing was (`kind` "none"), looked for again after
`retry_days`. The sources are asked in the order of the settings (`app_settings["lyrics"]`, the admin's page); a
text chosen by hand (`pinned`) is never replaced by the background search. Job `lyrics_scan` fills the rows of the
tracks that have none: after every library scan (new music) and when the admin asks for the whole library.
"""

from __future__ import annotations

import asyncio
import logging
from datetime import timedelta
from typing import Any

from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.catalog.models import split_ref
from app.config import get_settings
from app.db import session_scope
from app.jobs.queue import enqueue, handler
from app.lyrics import parse
from app.lyrics.providers import ORDER, TITLES, Found, Query, ask
from app.models import AppSetting, Job, Lyrics, Track, utcnow

log = logging.getLogger(__name__)

SETTINGS_KEY = "lyrics"
SCAN_BATCH = 25
SCAN_PAUSE_S = 0.4  # between tracks: the sources are free services, not to be hammered
DEFAULT_RETRY_DAYS = 7


# --- settings -----------------------------------------------------------------------------------------


def _clean(value: dict[str, Any] | None) -> dict[str, Any]:
    v = value or {}
    seen: set[str] = set()
    sources = []
    for s in v.get("sources") or []:
        sid = s.get("id") if isinstance(s, dict) else None
        if sid in TITLES and sid not in seen:
            seen.add(sid)
            sources.append({"id": sid, "on": bool(s.get("on", True))})
    sources += [{"id": sid, "on": True} for sid in ORDER if sid not in seen]  # a source added by an update
    retry = v.get("retry_days", DEFAULT_RETRY_DAYS)
    return {"sources": sources, "retry_days": retry if retry in (0, 7, 30) else DEFAULT_RETRY_DAYS,
            "on_add": bool(v.get("on_add", True))}


async def get_settings_row(s: AsyncSession) -> dict[str, Any]:
    row = await s.get(AppSetting, SETTINGS_KEY)
    return _clean(row.value_json if row else None)


async def save_settings(s: AsyncSession, value: dict[str, Any]) -> dict[str, Any]:
    clean = _clean(value)
    row = await s.get(AppSetting, SETTINGS_KEY)
    if row is None:
        s.add(AppSetting(key=SETTINGS_KEY, value_json=clean))
    else:
        row.value_json = clean
    return clean


def enabled(settings: dict[str, Any]) -> list[str]:
    return [x["id"] for x in settings["sources"] if x["on"]]


# --- finding ------------------------------------------------------------------------------------------


def query_of(track: Track, album: str | None = None) -> Query:
    main = track.artist.split(",")[0].strip() or track.artist
    return Query(artist=main, title=track.title, album=album, duration_s=track.duration_s,
                 path=get_settings().library_dir / track.path)


async def track_of(s: AsyncSession, *, nd_song_id: str | None = None, ref: str | None = None) -> Track | None:
    """The library track a page names by its Navidrome song or its catalog ref."""
    if nd_song_id:
        t = (await s.execute(select(Track).where(Track.nd_song_id == nd_song_id).limit(1))).scalar_one_or_none()
        if t is not None:
            return t
    if ref:
        src, id_ = split_ref(ref)
        if src == "dz":
            return (await s.execute(select(Track).where(Track.deezer_track_id == id_).limit(1))).scalar_one_or_none()
        if src == "lib" and id_.isdigit():
            return await s.get(Track, int(id_))
    return None


async def find(q: Query, sources: list[str]) -> Found | None:
    """The first source that has the text. A plain text is kept as a fallback while a later source may have a timed
    one: timings are what the player is for."""
    plain: Found | None = None
    for source in sources:
        found = await ask(source, q)
        if found is None or found.kind == "none":
            continue
        if found.lines or source == "file":  # the owner's own text is taken as it is
            return found
        plain = plain or found
    return plain


def _store(row: Lyrics, found: Found | None) -> None:
    row.kind = found.kind if found else "none"
    row.source = found.source if found else None
    row.lines_json = found.lines if found else None
    row.plain = (found.plain if found and not found.lines else None)
    row.search_text = parse.plain_of(row.lines_json, row.plain).casefold() if found else None
    row.fetched_at = utcnow()


async def ensure(s: AsyncSession, track: Track, *, force: bool = False) -> Lyrics:
    """The track's lyrics row: from the database, or found now and stored."""
    row = await s.get(Lyrics, track.id)
    settings = await get_settings_row(s)
    if row is not None and not force:
        retry = settings["retry_days"]
        stale = row.kind == "none" and not row.pinned and retry and utcnow() - row.fetched_at > timedelta(days=retry)
        if not stale:
            return row
    found = await find(query_of(track), enabled(settings))
    if row is None:
        row = Lyrics(track_id=track.id, offset_ms=0, pinned=False)
        s.add(row)
    _store(row, found)
    await s.flush()
    return row


async def candidates(s: AsyncSession, track: Track) -> list[Found]:
    """What every enabled source has for the track («другой вариант»)."""
    q = query_of(track)
    found = await asyncio.gather(*(ask(src, q) for src in enabled(await get_settings_row(s))))
    return [f for f in found if f is not None and f.kind != "none"]


PROBE_WAIT_S = 15  # «Другой вариант» waits for a source this long, then says it did not answer


async def probe(track: Track, source: str) -> tuple[str, Found | None, float]:
    """One source asked for the track, on the clock: (found / none / timeout / error, the text, seconds)."""
    from app.lyrics import providers

    fn = providers.PROVIDERS.get(source)
    started = asyncio.get_running_loop().time()
    status, found = "none", None
    try:
        found = await asyncio.wait_for(fn(query_of(track)), PROBE_WAIT_S) if fn else None
        if found is not None and found.kind != "none":
            status = "found"
        else:
            found = None
    except TimeoutError:
        status = "timeout"
    except Exception as e:
        log.info("lyrics: %s failed for %s - %s: %r", source, track.artist, track.title, e)
        status = "error"
    return status, found, round(asyncio.get_running_loop().time() - started, 1)


async def choose(s: AsyncSession, track: Track, source: str | None) -> Lyrics:
    """The user's own choice: the text of this source, or none at all. It stays until they choose again."""
    row = await s.get(Lyrics, track.id)
    if row is None:
        row = Lyrics(track_id=track.id, offset_ms=0, pinned=False)
        s.add(row)
    found = await ask(source, query_of(track)) if source else None
    if source and (found is None or found.kind == "none"):
        raise LookupError("В этом источнике текста нет")
    _store(row, found)
    row.pinned = True
    row.offset_ms = 0
    await s.flush()
    return row


def text_of(row: Lyrics | None) -> str:
    return parse.plain_of(row.lines_json, row.plain) if row is not None and row.kind != "none" else ""


async def search(s: AsyncSession, q: str, limit: int = 300) -> list[dict[str, Any]]:
    """Tracks whose lyrics have `q` in them, with the line it is in."""
    needle = " ".join(q.casefold().split())
    # one letter finds everything; one hieroglyph is a word
    if len(needle) < 2 and not any(ord(ch) >= 0x2E80 for ch in needle):
        return []
    # instr, not LIKE: the words are looked for as they are, `%` and `_` included
    rows = (await s.execute(
        select(Lyrics, Track.nd_song_id, Track.deezer_track_id).join(Track, Track.id == Lyrics.track_id)
        .where(func.instr(Lyrics.search_text, needle) > 0).limit(limit))).all()
    out = []
    for row, nd_song_id, dz in rows:
        text = parse.plain_of(row.lines_json, row.plain)
        line = next((ln.strip() for ln in text.splitlines() if needle in " ".join(ln.casefold().split())), "")
        out.append({"track_id": row.track_id, "nd_song_id": nd_song_id, "ref": f"dz:{dz}" if dz else None, "line": line[:200]})
    return out


# --- background ---------------------------------------------------------------------------------------


async def stats(s: AsyncSession) -> dict[str, int]:
    total = (await s.execute(select(func.count(Track.id)))).scalar_one()
    kinds = dict((await s.execute(select(Lyrics.kind, func.count(Lyrics.track_id)).group_by(Lyrics.kind))).all())
    known = sum(kinds.values())
    return {"total": total, "word": kinds.get("word", 0), "synced": kinds.get("synced", 0), "plain": kinds.get("plain", 0),
            "none": kinds.get("none", 0), "unchecked": max(0, total - known)}


async def enqueue_scan_once(s: AsyncSession, *, delay_s: float = 0, forced: bool = False) -> None:
    """One queued scan at a time. `forced`: the admin asked for it (`on_add` off does not stop it)."""
    pending = (await s.execute(select(Job.id).where(Job.kind == "lyrics_scan", Job.status == "queued").limit(1))).scalar_one_or_none()
    if pending is None:
        await enqueue(s, "lyrics_scan", {"forced": forced}, delay_s=delay_s, priority=-5)


@handler("lyrics_scan")
async def handle_scan(job: Job) -> None:
    """Find the lyrics of a batch of tracks that have no row yet; queue itself again while there are more."""
    forced = bool((job.payload_json or {}).get("forced"))
    async with session_scope() as s:
        if not forced and not (await get_settings_row(s))["on_add"]:
            return
        ids = list((await s.execute(
            select(Track.id).outerjoin(Lyrics, Lyrics.track_id == Track.id).where(Lyrics.track_id.is_(None))
            .order_by(Track.id.desc()).limit(SCAN_BATCH))).scalars())
    for track_id in ids:
        try:
            async with session_scope() as s:
                track = await s.get(Track, track_id)
                if track is not None:
                    await ensure(s, track)
        except Exception as e:
            log.warning("lyrics: track %s failed: %r", track_id, e)
        await asyncio.sleep(SCAN_PAUSE_S)
    if len(ids) == SCAN_BATCH:
        async with session_scope() as s:
            await enqueue_scan_once(s, forced=forced)
