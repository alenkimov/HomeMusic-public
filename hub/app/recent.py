"""«Продолжить» of the home screen: the lists the user last played from, the newest first.

A listen says the page its list was started on (`PlayEvent.page`) — a playlist, «Любимое», an album, an artist,
a list of «Для вас»; where it does not (the listens before the player said it), the kind of list and the track tell
what can be told: «Любимое», «Микс дня», the track's album. A place is named once however often it was played from;
what is not a list of its own (the search, a wave, the history) is not a place to go back to.
"""

from __future__ import annotations

import logging
from typing import Any
from urllib.parse import quote, unquote

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app import stats
from app.catalog.models import split_ref
from app.models import PlayEvent, Playlist, PlaylistCover, TagSelection, Track
from app.services import svc

log = logging.getLogger(__name__)

LOOK_BACK = 600  # starts of a track looked through, the newest first
SHOWN = 8


def _path(page: str | None, context: str | None, track: Track | None) -> str | None:
    """The page a listen leads back to, as the web app writes it; None: nowhere of its own."""
    page = unquote(page) if page else None
    ctx = context or ""
    if page:
        head = page.split("/")[1] if page.count("/") else ""
        if head in ("playlist", "album", "selection", "auto") and page.count("/") == 2:
            return page
        if head == "artist" and page.count("/") == 2:
            return page
        if head == "lib" and page.count("/") == 3 and page.split("/")[2] in ("album", "artist"):
            return page
        if page in ("/liked", "/mix", "/discoveries"):
            return page
        if page.startswith("/mix"):
            return "/mix"
        return None
    if ctx == "liked":
        return "/liked"
    if ctx.startswith("mix:"):
        return "/mix"
    if ctx == "discoveries":
        return "/discoveries"
    if ctx == "album" and track is not None and (track.catalog or {}).get("album_ref"):
        return f"/album/{track.catalog['album_ref']}"
    return None


async def places(s: AsyncSession, user_id: int, limit: int = SHOWN) -> list[dict[str, Any]]:
    rows = (await s.execute(
        select(PlayEvent.page, PlayEvent.context, PlayEvent.track_id).where(
            PlayEvent.user_id == user_id, PlayEvent.kind == "start")
        .order_by(PlayEvent.ts.desc(), PlayEvent.id.desc()).limit(LOOK_BACK))).all()
    ids = {t for _, _, t in rows if t}
    tracks = {t.id: t for t in (await s.execute(select(Track).where(Track.id.in_(ids)))).scalars()} if ids else {}
    order: list[tuple[str, Track | None]] = []
    seen: set[str] = set()
    for page, context, track_id in rows:
        track = tracks.get(track_id) if track_id else None
        path = _path(page, context, track)
        if path and path not in seen:
            seen.add(path)
            order.append((path, track))
    out: list[dict[str, Any]] = []
    for path, track in order:
        item = await _place(s, user_id, path, track)
        if item is not None:
            out.append(item)
            if len(out) >= limit:
                break
    return out


def _to(path: str) -> str:
    """The address as the web app's router reads it: the last part is a ref and goes encoded."""
    head, _, tail = path.rpartition("/")
    return f"{head}/{quote(tail, safe='')}" if head else path


async def _place(s: AsyncSession, user_id: int, path: str, track: Track | None) -> dict[str, Any] | None:
    """What to call the place and what picture it has; None: it is not there any more (a deleted playlist)."""
    parts = path.strip("/").split("/")
    head, tail = parts[0], parts[-1]
    cat = (track.catalog or {}) if track is not None else {}
    if path == "/liked":
        return {"to": path, "kind": "liked", "title": "Любимое", "icon": "heart"}
    if path == "/mix":
        return {"to": path, "kind": "for-you", "title": "Микс дня", "icon": "sparkle"}
    if path == "/discoveries":
        return {"to": path, "kind": "for-you", "title": "Открытия недели", "icon": "wave"}
    if head == "auto":
        return {"to": path, "kind": "for-you", "title": stats.AUTO[tail][0],
                "icon": "history" if tail == "forgotten" else "chart"} if tail in stats.AUTO else None
    if head == "playlist":
        p = await s.get(Playlist, int(tail)) if tail.isdigit() else None
        if p is None or p.user_id != user_id:
            return None
        key = (await s.execute(select(PlaylistCover.key).where(PlaylistCover.playlist_id == p.id))).scalar_one_or_none()
        cover = f"/api/playlists/{p.id}/cover?v={key}" if key else (
            f"/api/nd/cover/{quote(p.nd_cover, safe='')}?size=160" if p.nd_cover else None)
        return {"to": path, "kind": "playlist", "title": p.name, "cover_url": cover, "icon": "playlist"}
    if head == "selection":
        sel = await s.get(TagSelection, int(tail)) if tail.isdigit() else None
        if sel is None or sel.user_id != user_id:
            return None
        return {"to": path, "kind": "selection", "title": sel.name, "icon": "filter"}
    if head == "album":
        title, cover = cat.get("album_title") if cat.get("album_ref") == tail else None, None
        if title:
            cover = cat.get("cover_small_url") or cat.get("cover_url")
        else:
            try:
                src, id_ = split_ref(tail)
                a = await svc.deezer.get_album(id_) if src == "dz" and svc.deezer else None
            except Exception as e:
                log.info("recent: album %s: %r", tail, e)
                a = None
            if a is None:
                return None
            title, cover = a.title, a.cover_small_url or a.cover_url
        return {"to": _to(path), "kind": "album", "title": title, "cover_url": cover, "icon": "disc"}
    if head == "artist":
        try:
            src, id_ = split_ref(tail)
            a = await svc.deezer.get_artist(id_) if src == "dz" and svc.deezer else None
        except Exception as e:
            log.info("recent: artist %s: %r", tail, e)
            a = None
        if a is None:
            return None
        return {"to": _to(path), "kind": "artist", "title": a.name, "cover_url": a.picture_small_url or a.picture_url,
                "icon": "user", "round": True}
    if head == "lib" and track is not None:
        if parts[1] == "album":
            return {"to": _to(path), "kind": "album", "title": track.album or "Альбом",
                    "cover_url": f"/api/nd/cover/{quote(tail, safe='')}?size=160", "icon": "disc"}
        return {"to": _to(path), "kind": "artist", "title": (track.album_artist or track.artist).split(",")[0].strip(),
                "cover_url": f"/api/nd/cover/{quote('ar-' + tail, safe='')}?size=160", "icon": "user", "round": True}
    return None
