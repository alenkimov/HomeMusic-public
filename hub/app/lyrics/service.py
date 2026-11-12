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
from app.library.tracks import dz_id
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
                 path=get_settings().library_dir / track.path if track.path else None, isrc=track.isrc)


async def track_of(s: AsyncSession, *, nd_song_id: str | None = None, ref: str | None = None) -> Track | None:
    """The library track a page names by its Navidrome song or its catalog ref."""
    if nd_song_id:
        t = (await s.execute(select(Track).where(Track.nd_song_id == nd_song_id).limit(1))).scalar_one_or_none()
        if t is not None:
            return t
    if ref:
        src, id_ = split_ref(ref)
        if src == "lib" and id_.isdigit():
            return await s.get(Track, int(id_))
        return (await s.execute(select(Track).where(Track.ref == ref))).scalar_one_or_none()
    return None


async def find(q: Query, sources: list[str]) -> Found | None:
    """The best text the sources have. The kind matters more than the source: a text timed by the word beats one
    timed by the line, and that beats a plain one — timings are what the player is for. Of two of a kind the
    earlier source wins. So the sources are asked in order until one has the words timed; what the earlier ones
    gave is kept to fall back on."""
    best: Found | None = None
    instrumental = False
    for source in sources:
        if best is not None and RANK[AT_BEST.get(source, "word")] <= RANK[best.kind]:
            continue  # it has nothing better than what is in hand: not asked
        found = await ask(source, q)
        if found is None or found.kind == "none":
            instrumental = instrumental or bool(found and found.instrumental)
            continue
        if source == "file" or found.kind == "word":  # the owner's own text is taken as it is; nothing beats words
            return found
        if best is None or RANK[found.kind] > RANK[best.kind]:
            best = found
    # no text anywhere, and a source says there are no words: kept as that (the «без слов» tag, app/tags.py)
    return best or (Found(INSTRUMENTAL, instrumental=True) if instrumental else None)


RANK = {"plain": 1, "synced": 2, "word": 3}
AT_BEST = {"ovh": "plain"}  # the best kind of text a source ever has, where that is not «timed by the word»


INSTRUMENTAL = "instrumental"  # `Lyrics.source` of a track a source called an instrumental


async def set_no_words(s: AsyncSession, track_ids: list[int], on: bool) -> None:
    """«Без слов» put on tracks by hand, or taken off (app/tags.py). On: they have no text and none is looked for,
    as if the listener said so in the lyrics window. Off: a track without a text is looked for again, as a new one."""
    for i in range(0, len(track_ids), 300):
        part = track_ids[i:i + 300]
        rows = {r.track_id: r for r in (await s.execute(select(Lyrics).where(Lyrics.track_id.in_(part)))).scalars()}
        for tid in part:
            row = rows.get(tid)
            if on:
                if row is None:
                    row = Lyrics(track_id=tid, offset_ms=0, pinned=False)
                    s.add(row)
                _store(row, None)
                row.pinned, row.offset_ms = True, 0
            elif row is not None and row.kind == "none":
                await s.delete(row)  # the scan takes a track without a row as a new one
    await s.flush()


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
        # the search takes seconds: meanwhile the track may have got its row («без слов» set on an upload, the
        # player asking for the same text)
        row = (await s.execute(select(Lyrics).where(Lyrics.track_id == track.id))).scalar_one_or_none()
        if row is not None and (row.pinned or row.kind != "none"):
            return row
    if row is None:
        row = Lyrics(track_id=track.id, offset_ms=0, pinned=False)
        s.add(row)
    _store(row, found)
    await s.flush()
    await _retag(s, track.id)
    return row


async def _retag(s: AsyncSession, track_id: int) -> None:
    """The text of a track changed: its language tags follow it (app/tags.py)."""
    from app import tags

    try:
        await tags.sync(s, [track_id])
    except Exception as e:  # the lyrics matter more than their tags
        log.info("lyrics: tags of track %s not updated: %r", track_id, e)


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
    if source is None:  # «no lyrics» said here outweighs a «без слов» tag taken off before
        from app import tags

        await tags.forgive_no_words(s, [track.id])
    await _retag(s, track.id)
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
        select(Lyrics, Track.nd_song_id, Track.ref).join(Track, Track.id == Lyrics.track_id)
        .where(func.instr(Lyrics.search_text, needle) > 0).limit(limit))).all()
    out = []
    for row, nd_song_id, ref in rows:
        text = parse.plain_of(row.lines_json, row.plain)
        line = next((ln.strip() for ln in text.splitlines() if needle in " ".join(ln.casefold().split())), "")
        out.append({"track_id": row.track_id, "nd_song_id": nd_song_id, "ref": ref if dz_id(ref) else None, "line": line[:200]})
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
