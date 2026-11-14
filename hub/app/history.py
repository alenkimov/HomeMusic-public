"""«История»: what the user listened to, one listen after another, the newest first.

The listens are the ones «Статистика» counts (`app.stats.plays`: the Hub's own events put together by playback, the
scrobbles of Navidrome, a history brought from Spotify), so the two pages agree. A listen shorter than `SHOWN_MS` is
leafing through a queue and is not shown. Each says where it was played from — the page the list was started on,
when the player told it (`PlayEvent.page`), else what kind of list it was.

A listen can be taken out (`remove`): its events are deleted, so it leaves the statistics and what the taste is made
of too."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import UTC
from typing import Any
from zoneinfo import ZoneInfo

from sqlalchemy import delete, select
from sqlalchemy.ext.asyncio import AsyncSession

from app import stats
from app.config import get_settings
from app.models import ListenHistory, PlayEvent, Playlist, TagSelection, Track

SHOWN_MS = 5_000
# what kind of list a listen was played from, by its context (`web/src/lib/plays.ts`); a page names it better
CONTEXTS = {"liked": ("Любимое", "/liked"), "playlist": ("Плейлист", None), "album": ("Альбом", None),
            "artist": ("Исполнитель", None), "search": ("Поиск", None), "queue": ("Очередь", None),
            "discoveries": ("Открытия недели", "/discoveries"), "selection": ("Подборка по тегам", None),
            "auto": ("Для вас", None), "radio": ("Радио", None), "history": ("История", "/history")}


@dataclass(slots=True)
class Entry:
    uid: str
    play: stats.Play


def local_day(ts) -> str:
    try:
        zone = ZoneInfo(get_settings().timezone)
    except Exception:
        zone = UTC
    return ts.replace(tzinfo=UTC).astimezone(zone).date().isoformat()


async def entries(s: AsyncSession, user_id: int, *, show: str = "all", q: str = "") -> tuple[list[stats.Play], stats.Library]:
    """The user's listens worth showing, the newest first. `show`: "finished" / "skipped"; `q`: words of the name."""
    lib = await stats.library(s)
    needle = q.strip().casefold()
    out: list[stats.Play] = []
    for p in await stats.plays(s, user_id, lib):
        if p.ms < SHOWN_MS or not p.uid:
            continue
        if (show == "finished" and not p.counted) or (show == "skipped" and p.counted):
            continue
        if needle:
            t = lib.tracks.get(p.track_id) if p.track_id is not None else None
            words = f"{t.title} {t.artist}" if t else f"{p.title} {p.artist}"
            if needle not in words.casefold():
                continue
        out.append(p)
    out.reverse()
    return out, lib


async def places(s: AsyncSession, user_id: int, plays: list[stats.Play]) -> dict[str, tuple[str, str | None]]:
    """(what to call it, where it leads) for the pages and contexts of these listens."""
    out: dict[str, tuple[str, str | None]] = {}
    pages = {p.page for p in plays if p.page}
    lists = {int(x.rsplit("/", 1)[1]) for x in pages if x.startswith("/playlist/") and x.rsplit("/", 1)[1].isdigit()}
    names = dict((await s.execute(select(Playlist.id, Playlist.name).where(
        Playlist.user_id == user_id, Playlist.id.in_(lists)))).all()) if lists else {}
    sels = {int(x.rsplit("/", 1)[1]) for x in pages if x.startswith("/selection/") and x.rsplit("/", 1)[1].isdigit()}
    sel_names = dict((await s.execute(select(TagSelection.id, TagSelection.name).where(
        TagSelection.user_id == user_id, TagSelection.id.in_(sels)))).all()) if sels else {}
    for page in pages:
        tail = page.rsplit("/", 1)[1] if "/" in page else ""
        if page.startswith("/playlist/") and tail.isdigit() and int(tail) in names:
            out[page] = (names[int(tail)], page)
        elif page.startswith("/selection/") and tail.isdigit() and int(tail) in sel_names:
            out[page] = (sel_names[int(tail)], page)
        elif page.startswith("/auto/") and tail in stats.AUTO:
            out[page] = (stats.AUTO[tail][0], page)
        elif page.startswith("/mix"):
            out[page] = ("Микс дня", "/mix")
    return out


def place_of(p: stats.Play, known: dict[str, tuple[str, str | None]]) -> tuple[str | None, str | None]:
    if p.origin == "spotify":
        return "Spotify", None
    if p.origin == "navidrome":
        return "Другое приложение", None
    if p.page and p.page in known:
        return known[p.page]
    ctx = p.context or ""
    if ctx.startswith("mix:"):
        return "Микс дня", "/mix"
    if ctx.startswith("wave:"):
        return "Моя волна", None
    if ctx.startswith("smart:"):
        return "Умное перемешивание", None
    return CONTEXTS.get(ctx, (None, None))


async def page(s: AsyncSession, user: Any, *, offset: int, limit: int, show: str, q: str) -> dict[str, Any]:
    from app.api.playlists import tracks_out

    plays, lib = await entries(s, user.id, show=show, q=q)
    part = plays[offset:offset + limit]
    known = await places(s, user.id, part)
    ids = list(dict.fromkeys(p.track_id for p in part if p.track_id is not None))
    rows = {t.id: t for t in (await s.execute(select(Track).where(Track.id.in_(ids)))).scalars()} if ids else {}
    drawn = dict(zip(rows, await tracks_out(s, user, list(rows.values())), strict=True)) if rows else {}
    days: dict[str, dict[str, int]] = {}
    shown_days = {local_day(p.ts) for p in part}
    for p in plays:  # a day's total is of the whole day, not of the part of it on this page
        day = local_day(p.ts)
        if day in shown_days:
            d = days.setdefault(day, {"count": 0, "ms": 0})
            d["count"] += 1
            d["ms"] += p.ms
    items = []
    for p in part:
        name, to = place_of(p, known)
        track = drawn.get(p.track_id) if p.track_id is not None else None
        lt = lib.tracks.get(p.track_id) if p.track_id is not None else None
        items.append({
            "uid": p.uid, "ts": p.ts.isoformat(), "day": local_day(p.ts), "ms": p.ms, "finished": p.counted,
            "duration_ms": p.duration_ms or ((lt.duration_s or 0) * 1000 if lt else None) or None,
            "title": lt.title if lt else p.title, "artist": lt.artist if lt else p.artist,
            "from": name, "from_to": to, "origin": p.origin,
            "track": track.model_dump(mode="json") if track is not None else None,
            # a catalog track the library has not got plays by its ref; a line of another service's history does not
            "ref": None if track is not None or not p.key.startswith(("dz:", "hm:")) else p.key,
        })
    return {"items": items, "days": days, "total": len(plays), "next_offset": offset + limit if offset + limit < len(plays) else None}


async def remove(s: AsyncSession, user_id: int, uid: str) -> int:
    """Takes a listen out of the history: its events (or the line brought from another service) are deleted. Returns
    how many rows went; 0 — there was no such listen of this user."""
    kind, _, rest = uid.partition(":")
    if kind == "p":  # a playback of the Hub's own players: every event of it
        device, _, playback = rest.partition("|")
        if not device or not playback:
            return 0
        res = await s.execute(delete(PlayEvent).where(
            PlayEvent.user_id == user_id, PlayEvent.device_id == device, PlayEvent.playback_id == playback,
            PlayEvent.kind.in_(stats.LISTEN_KINDS)))
    elif kind == "e" and rest.isdigit():  # a scrobble of Navidrome
        res = await s.execute(delete(PlayEvent).where(PlayEvent.user_id == user_id, PlayEvent.id == int(rest),
                                                      PlayEvent.kind.in_(stats.LISTEN_KINDS)))
    elif kind == "h" and rest.isdigit():
        res = await s.execute(delete(ListenHistory).where(ListenHistory.user_id == user_id, ListenHistory.id == int(rest)))
    else:
        return 0
    return res.rowcount or 0
