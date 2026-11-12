"""«Статистика»: what a user listened to — the tracks, artists, genres and albums of a period, the hours and the days.

Two sources. The Hub's own journal (`PlayEvent`): one playback is its events with one `playback_id`, the time
listened is the last `listened_ms` of them; a scrobble of Navidrome (another client) is a whole listen. And the history
brought in from Spotify (`ListenHistory`, `import_spotify`), which is only counted here.

A listen counts from 30 seconds (half the track for one shorter than a minute, `listened_threshold_ms`); a shorter
one is a skip: it adds to the time, not to the number; under two seconds it is nothing at all (leafing through the
queue). Everything is each user's own.
"""

from __future__ import annotations

import io
import json
import re
import zipfile
from bisect import bisect_left
from collections import defaultdict
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from typing import Any, BinaryIO
from zoneinfo import ZoneInfo

from sqlalchemy import delete, func, select
from sqlalchemy.dialects.sqlite import insert
from sqlalchemy.ext.asyncio import AsyncSession

from app.listening_summary import listened_threshold_ms
from app.matching.normalize import fingerprint
from app.models import ListenHistory, PlayEvent, Tag, Track, TrackLink, TrackTag, utcnow

PERIODS = {"week": 7, "month": 30, "halfyear": 182, "year": 365, "all": None}
HEATMAP_WEEKS = 26
LISTEN_KINDS = ("start", "progress", "interrupted", "error", "skip", "finish")
SAME_LISTEN_S = 90  # the same track again within this of itself is one listen told twice (two export files)
COUNTED_MS = 30_000
TOUCHED_MS = 2_000  # less than this is leafing through the queue: neither a listen nor a skip
MAX_MEMBER_BYTES = 200 * 1024 * 1024
TREND_WEEKS = 8
AUTO = {"top-month": ("Топ месяца", 30), "top-year": ("Топ года", 365), "top-all": ("Топ за всё время", None),
        "forgotten": ("Давно не слушал", None)}
AUTO_SIZE = 50
FORGOTTEN_DAYS = 90  # «Давно не слушал»: not once in this long…
FORGOTTEN_PLAYS = 3  # …though listened to at least this many times before
VARIOUS = {"various artists", "various", "va", "разные исполнители", "сборник"}


@dataclass(slots=True)
class Play:
    ts: datetime  # UTC, naive
    ms: int
    counted: bool
    track_id: int | None  # the library track, if the library has it
    key: str  # what names the track where the library has not got it
    title: str
    artist: str
    album: str | None


@dataclass(slots=True)
class LibTrack:
    id: int
    nd_song_id: str | None
    title: str
    artist: str
    album: str | None
    main_artist: str
    duration_s: int | None


def _main_artist(artist: str, album_artist: str | None) -> str:
    """Whom a track counts for: the album's artist where the track names several («A, B, C»)."""
    if album_artist and album_artist.strip().lower() not in VARIOUS:
        return album_artist.strip()
    return artist.strip()


@dataclass
class Library:
    tracks: dict[int, LibTrack]
    by_song: dict[str, int]
    by_ref: dict[str, int]  # catalog ref -> track
    by_spotify: dict[str, int]
    by_fingerprint: dict[str, int]


async def library(s: AsyncSession) -> Library:
    lib = Library({}, {}, {}, {}, {})
    rows = (await s.execute(select(Track.id, Track.nd_song_id, Track.title, Track.artist, Track.album, Track.album_artist,
                                   Track.duration_s, Track.ref, Track.fingerprint).order_by(Track.id))).all()
    for tid, nd, title, artist, album, album_artist, duration, ref, fp in rows:
        lib.tracks[tid] = LibTrack(tid, nd, title, artist, album, _main_artist(artist, album_artist), duration)
        if nd:
            lib.by_song[nd] = tid
        if ref:
            lib.by_ref[ref] = tid
        if fp and nd:
            lib.by_fingerprint.setdefault(fp, tid)
    for tid, external in (await s.execute(select(TrackLink.track_id, TrackLink.external_id)
                                          .where(TrackLink.service == "spotify"))).all():
        lib.by_spotify[external] = tid
    return lib


def _history_track(lib: Library, spotify_id: str | None, key: str) -> int | None:
    if spotify_id and spotify_id in lib.by_spotify:
        return lib.by_spotify[spotify_id]
    return lib.by_fingerprint.get(key[2:]) if key.startswith("t:") else None


async def plays(s: AsyncSession, user_id: int, lib: Library | None = None) -> list[Play]:
    """Every listen of the user, oldest first."""
    lib = lib or await library(s)
    now = utcnow()
    out: list[Play] = []

    def play(ts: datetime, ms: int, counted: bool, nd: str | None, ref: str | None, meta: dict[str, Any],
             track_id: int | None = None) -> None:
        tid = track_id if track_id in lib.tracks else None
        if tid is None and nd:
            tid = lib.by_song.get(nd)
        if tid is None and ref:
            tid = lib.by_ref.get(ref)
        title, artist = str(meta.get("title") or ""), str(meta.get("artist_credit") or "")
        if tid is None and not title:
            return  # nothing to show it by
        out.append(Play(ts, ms, counted, tid, ref or f"nd:{nd}", title, artist, meta.get("album")))

    rows = (await s.execute(
        select(PlayEvent.ts, PlayEvent.kind, PlayEvent.ref, PlayEvent.nd_song_id, PlayEvent.track_id, PlayEvent.playback_id,
               PlayEvent.device_id, PlayEvent.listened_ms, PlayEvent.duration_s, PlayEvent.track_metadata, PlayEvent.client)
        .where(PlayEvent.user_id == user_id, PlayEvent.ts <= now, PlayEvent.kind.in_(LISTEN_KINDS),
               PlayEvent.superseded_scrobble.is_(False)).order_by(PlayEvent.ts, PlayEvent.id))).all()
    groups: dict[tuple[str, str], list[Any]] = defaultdict(list)
    for r in rows:
        if r.playback_id and r.device_id:
            groups[(r.device_id, r.playback_id)].append(r)
        elif r.kind == "finish" and r.client == "navidrome":
            tid = r.track_id if r.track_id in lib.tracks else lib.by_song.get(r.nd_song_id) if r.nd_song_id else None
            seconds = r.duration_s or (lib.tracks[tid].duration_s if tid else None) or 0
            play(r.ts, int(seconds * 1000), True, r.nd_song_id, r.ref, r.track_metadata or {}, tid)
    for events in groups.values():
        listened = [e.listened_ms for e in events if e.listened_ms is not None]
        if not listened:
            continue
        meta: dict[str, Any] = {}
        for e in events:
            for k, v in (e.track_metadata or {}).items():
                meta.setdefault(k, v)
        duration = next((e.duration_s for e in reversed(events) if e.duration_s), None) or meta.get("duration_s")
        ms = max(listened)
        if ms < TOUCHED_MS:
            continue
        play(events[0].ts, ms, ms >= listened_threshold_ms(duration),
             next((e.nd_song_id for e in events if e.nd_song_id), None), next((e.ref for e in events if e.ref), None), meta,
             next((e.track_id for e in events if e.track_id), None))

    history = (await s.execute(select(ListenHistory.ts, ListenHistory.ms, ListenHistory.key, ListenHistory.spotify_id,
                                      ListenHistory.title, ListenHistory.artist, ListenHistory.album)
                               .where(ListenHistory.user_id == user_id, ListenHistory.ts <= now))).all()
    for ts, ms, key, spotify_id, title, artist, album in history:
        if ms < TOUCHED_MS:
            continue
        out.append(Play(ts, ms, ms >= COUNTED_MS, _history_track(lib, spotify_id, key), key, title, artist, album))
    out.sort(key=lambda p: p.ts)
    return out


# --- the page ------------------------------------------------------------------------------------------------


async def _genres(s: AsyncSession) -> dict[int, list[str]]:
    out: dict[int, list[str]] = defaultdict(list)
    for tid, name in (await s.execute(select(TrackTag.track_id, Tag.name).join(Tag, Tag.id == TrackTag.tag_id)
                                      .where(Tag.group == "genre"))).all():
        out[tid].append(name)
    return out


def _week(now: datetime, ts: datetime) -> int | None:
    """Which of the last 8 weeks (seven days each, the last one ends now) a listen is in: 0 the oldest … 7 this one."""
    i = TREND_WEEKS - 1 - (now - ts).days // 7
    return i if 0 <= i < TREND_WEEKS else None


def _streak(days: set, today) -> int:
    """Days in a row with a listen, up to today (or yesterday: today is not over yet)."""
    day = today if today in days else today - timedelta(days=1)
    n = 0
    while day in days:
        n, day = n + 1, day - timedelta(days=1)
    return n


async def overview(s: AsyncSession, user_id: int, *, period: str = "month", by: str = "plays", timezone: str = "UTC",
                   limit: int = 5, now: datetime | None = None) -> dict[str, Any]:
    """The page: the sums of a period, its tops (the first `limit` of each, ordered `by` plays or time), listening
    by hour and by day of the week, and the last 26 weeks day by day."""
    try:
        zone = ZoneInfo(timezone)
    except Exception:
        zone = ZoneInfo("UTC")
    now = now or utcnow()
    lib = await library(s)
    everything = await plays(s, user_id, lib)
    genres = await _genres(s)
    days = PERIODS.get(period, 30)
    since = now - timedelta(days=days) if days else None
    local = lambda ts: ts.replace(tzinfo=UTC).astimezone(zone)  # noqa: E731
    value = (lambda p: p.ms) if by == "time" else (lambda p: int(p.counted))

    tops: dict[str, dict[Any, dict[str, Any]]] = {"tracks": {}, "artists": {}, "genres": {}, "albums": {}}

    def add(kind: str, key: Any, p: Play, **info: Any) -> None:
        row = tops[kind].get(key)
        if row is None:
            row = tops[kind][key] = {"plays": 0, "ms": 0, "nd_song_id": None, **info}
        row["plays"] += int(p.counted)
        row["ms"] += p.ms
        if row["nd_song_id"] is None and p.track_id is not None:
            row["nd_song_id"] = lib.tracks[p.track_id].nd_song_id

    hours, weekdays = [0] * 24, [0] * 7
    total_ms = counted = attempts = 0
    heard: set[Any] = set()
    today = local(now).date()
    heat_from = today - timedelta(days=today.weekday() + 7 * (HEATMAP_WEEKS - 1))
    heat: dict[str, int] = defaultdict(int)
    listened_days = set()
    weeks: dict[Any, list[int]] = {}  # of every track: its listens in each of the last 8 weeks
    before: dict[Any, int] = defaultdict(int)  # of every track: the same sum in the period before this one
    for p in everything:
        at = local(p.ts)
        t = lib.tracks.get(p.track_id) if p.track_id is not None else None
        track_key = ("lib", t.id) if t else ("out", p.key)
        week = _week(now, p.ts) if p.counted else None
        if week is not None:
            weeks.setdefault(track_key, [0] * TREND_WEEKS)[week] += 1
        if since and days and since - timedelta(days=days) <= p.ts < since:
            before[track_key] += value(p)
        if p.counted:
            listened_days.add(at.date())
        if at.date() >= heat_from and value(p):
            heat[at.date().isoformat()] += value(p)
        if since and p.ts < since:
            continue
        attempts += 1
        total_ms += p.ms
        counted += int(p.counted)
        hours[at.hour] += value(p)
        weekdays[at.weekday()] += value(p)
        title, artist, album = (t.title, t.artist, t.album) if t else (p.title, p.artist, p.album)
        main = t.main_artist if t else p.artist
        if p.counted:
            heard.add(track_key)
        add("tracks", track_key, p, name=title, sub=artist)
        if main:
            add("artists", main.lower(), p, name=main, sub=None)
        if album:
            add("albums", (album.lower(), main.lower()), p, name=album, sub=main)
        for g in genres.get(t.id, ()) if t else ():
            add("genres", g, p, name=g, sub=None)

    metric = "ms" if by == "time" else "plays"

    def top(kind: str) -> dict[str, Any]:
        rows = sorted(((k, r) for k, r in tops[kind].items() if r[metric] > 0),
                      key=lambda kr: (-kr[1][metric], -kr[1]["ms"], kr[1]["name"].lower()))
        items = [r for _, r in rows[:limit]]
        if kind == "tracks":
            # the place a track had in the period before this one — where there was one to compare with
            compared = bool(since and first and first < since)
            was = {k: i + 1 for i, (k, _) in enumerate(sorted(((k, v) for k, v in before.items() if v > 0), key=lambda kv: -kv[1]))}
            items = [{**r, "weeks": weeks.get(k, [0] * TREND_WEEKS), "was": was.get(k) if compared else None,
                      "fresh": compared and k not in was} for k, r in rows[:limit]]
        return {"total": len(rows), "items": items}

    first = everything[0].ts if everything else None
    return {
        "period": period if period in PERIODS else "month", "by": "time" if by == "time" else "plays",
        "summary": {"ms": total_ms, "plays": counted, "tracks": len(heard), "streak": _streak(listened_days, today),
                    "skips": attempts - counted, "attempts": attempts},
        "tracks": top("tracks"), "artists": top("artists"), "genres": top("genres"), "albums": top("albums"),
        "hours": hours, "weekdays": weekdays,
        "heatmap": {"from": heat_from.isoformat(), "to": today.isoformat(), "days": dict(heat)},
        "first_at": first.replace(tzinfo=UTC).isoformat() if first else None,
        "imported": (await s.execute(select(func.count(ListenHistory.id)).where(ListenHistory.user_id == user_id))).scalar_one(),
    }


async def counts(s: AsyncSession, user_id: int) -> dict[str, int]:
    """Navidrome song id -> how many times the user listened to it, in all the time (the «Прослушивания» column)."""
    lib = await library(s)
    out: dict[str, int] = defaultdict(int)
    for p in await plays(s, user_id, lib):
        if p.counted and p.track_id is not None:
            nd = lib.tracks[p.track_id].nd_song_id
            if nd:
                out[nd] += 1
    return dict(out)


async def auto(s: AsyncSession, user_id: int, kind: str, now: datetime | None = None) -> list[dict[str, Any]]:
    """An «автоплейлист»: the library songs of a rule, made anew every time — the most listened to of a period, or
    («Давно не слушал») those listened to much and not once lately. `plays`: the listens the rule counted."""
    now = now or utcnow()
    lib = await library(s)
    days = AUTO[kind][1]
    since = now - timedelta(days=days) if days else None
    count: dict[str, int] = defaultdict(int)
    last: dict[str, datetime] = {}
    for p in await plays(s, user_id, lib):
        nd = lib.tracks[p.track_id].nd_song_id if p.track_id is not None else None
        if not nd or not p.counted or (since and p.ts < since):
            continue
        count[nd] += 1
        last[nd] = p.ts
    if kind == "forgotten":
        edge = now - timedelta(days=FORGOTTEN_DAYS)
        count = {nd: n for nd, n in count.items() if n >= FORGOTTEN_PLAYS and last[nd] < edge}
    order = sorted(count, key=lambda nd: (-count[nd], -last[nd].timestamp(), nd))[:AUTO_SIZE]
    return [{"nd_song_id": nd, "plays": count[nd]} for nd in order]


async def track(s: AsyncSession, user_id: int, nd_song_id: str, now: datetime | None = None) -> dict[str, Any]:
    """One library song: its listens in all the time, the first and the last of them, the last 8 weeks."""
    now = now or utcnow()
    lib = await library(s)
    tid = lib.by_song.get(nd_song_id)
    mine = [p for p in await plays(s, user_id, lib) if tid is not None and p.track_id == tid]
    counted = [p for p in mine if p.counted]
    weeks = [0] * TREND_WEEKS
    for p in counted:
        week = _week(now, p.ts)
        if week is not None:
            weeks[week] += 1
    iso = lambda ts: ts.replace(tzinfo=UTC).isoformat()  # noqa: E731
    return {"plays": len(counted), "ms": sum(p.ms for p in mine), "weeks": weeks,
            "first_at": iso(counted[0].ts) if counted else None, "last_at": iso(counted[-1].ts) if counted else None}


# --- the history of Spotify ----------------------------------------------------------------------------------

_HISTORY_NAME = re.compile(r"(^|/)(streaming_?history|endsong)[^/]*\.json$", re.IGNORECASE)


def _entry(x: Any) -> dict[str, Any] | None:
    """One row of an export file: «Extended streaming history» (`ts`, `ms_played`, `master_metadata_*`) or the short
    «Account data» one (`endTime`, `msPlayed`, `trackName`). Podcasts and rows without a track are left out."""
    if not isinstance(x, dict):
        return None
    try:
        if "ms_played" in x:
            title, artist = x.get("master_metadata_track_name"), x.get("master_metadata_album_artist_name")
            ts = datetime.fromisoformat(str(x.get("ts")).replace("Z", "+00:00"))
            ms, album, uri = int(x["ms_played"]), x.get("master_metadata_album_album_name"), x.get("spotify_track_uri")
        elif "msPlayed" in x:
            title, artist = x.get("trackName"), x.get("artistName")
            ts = datetime.strptime(str(x.get("endTime")), "%Y-%m-%d %H:%M")
            ms, album, uri = int(x["msPlayed"]), None, None
        else:
            return None
    except (ValueError, TypeError):
        return None
    if not title or not artist or ms <= 0:
        return None
    if ts.tzinfo is not None:
        ts = ts.astimezone(UTC).replace(tzinfo=None)
    spotify_id = uri.rsplit(":", 1)[-1] if isinstance(uri, str) and uri.startswith("spotify:track:") else None
    title, artist = str(title)[:512], str(artist)[:512]
    key = f"sp:{spotify_id}" if spotify_id else f"t:{fingerprint(artist, title)}"
    return {"ts": ts, "ms": min(ms, 24 * 3600 * 1000), "key": key[:200], "spotify_id": spotify_id, "title": title,
            "artist": artist, "album": str(album)[:512] if album else None}


def _documents(name: str, data: BinaryIO) -> list[Any]:
    """The rows of an uploaded file: a history .json, or every history .json of the archive Spotify sends."""
    head = data.read(4)
    data.seek(0)
    if head.startswith(b"PK"):
        out: list[Any] = []
        with zipfile.ZipFile(data) as z:
            for info in z.infolist():
                if info.is_dir() or not _HISTORY_NAME.search(info.filename) or "video" in info.filename.lower():
                    continue
                if info.file_size > MAX_MEMBER_BYTES:
                    raise ValueError(f"Файл {info.filename} в архиве слишком большой")
                out += _documents(info.filename, io.BytesIO(z.read(info)))
        return out
    try:
        doc = json.loads(data.read().decode("utf-8-sig"))
    except (ValueError, UnicodeDecodeError) as e:
        raise ValueError(f"{name}: это не файл истории Spotify") from e
    return doc if isinstance(doc, list) else []


def parse_history(files: list[tuple[str, BinaryIO]]) -> list[dict[str, Any]]:
    """The listens of the uploaded files, oldest first; the same listen told twice is one."""
    rows = [e for name, data in files for e in map(_entry, _documents(name, data)) if e]
    rows.sort(key=lambda e: e["ts"])
    return rows


async def import_history(s: AsyncSession, user_id: int, rows: list[dict[str, Any]]) -> dict[str, int]:
    """Adds the listens the user has not got yet. Returns what became of them."""
    lib = await library(s)
    known: dict[str, list[datetime]] = defaultdict(list)
    for key, ts in (await s.execute(select(ListenHistory.key, ListenHistory.ts).where(ListenHistory.user_id == user_id)
                                    .order_by(ListenHistory.ts))).all():
        known[key].append(ts)
    window = timedelta(seconds=SAME_LISTEN_S)
    fresh: list[dict[str, Any]] = []
    duplicates = 0
    for e in rows:
        times = known[e["key"]]
        i = bisect_left(times, e["ts"])
        if any(abs(times[j] - e["ts"]) <= window for j in (i - 1, i) if 0 <= j < len(times)):
            duplicates += 1
            continue
        times.insert(i, e["ts"])
        fresh.append({"user_id": user_id, "source": "spotify", **e})
    for i in range(0, len(fresh), 400):
        await s.execute(insert(ListenHistory).values(fresh[i:i + 400]).on_conflict_do_nothing())
    linked = sum(1 for e in fresh if _history_track(lib, e["spotify_id"], e["key"]) is not None)
    return {"found": len(rows), "added": len(fresh), "duplicates": duplicates, "linked": linked,
            "outside": len(fresh) - linked, "listens": sum(1 for e in fresh if e["ms"] >= COUNTED_MS)}


async def forget_history(s: AsyncSession, user_id: int) -> int:
    res = await s.execute(delete(ListenHistory).where(ListenHistory.user_id == user_id))
    return res.rowcount or 0
