"""Listening data for mixes and «Моя волна»: player events and «Не нравится» (see app/plays.py)."""

from __future__ import annotations

from datetime import date
from typing import Any, Literal
from zoneinfo import ZoneInfoNotFoundError

from fastapi import APIRouter, Depends, HTTPException, Query
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app import plays
from app.api.schemas import DislikeIn, DislikeOut, PlayEventRejected, PlayEventsIn, PlayEventsOut
from app.auth import current_user
from app.db import get_session
from app.models import Dislike, User

router = APIRouter(tags=["plays"])


@router.post("/api/plays/events", response_model=PlayEventsOut)
async def events(body: PlayEventsIn, user: User = Depends(current_user),
                 s: AsyncSession = Depends(get_session, scope="function")):
    if body.expected_user_id is not None and body.expected_user_id != user.id:
        raise HTTPException(409, "Listening events belong to another account")
    r = await plays.record_events(s, user.id, body.events)
    # a catalog track the app played from its precache never asked the stream as a listen: its `start` is one
    # (a track of an economy import is downloaded once listened to)
    refs = {e.get("ref") for e in body.events if isinstance(e, dict) and e.get("kind") == "start"}
    if refs := sorted(r for r in refs if isinstance(r, str) and r and not r.startswith("nd:")):
        from app.imports.core import listened

        await s.commit()
        for ref in refs:
            await listened(user.id, ref)
    return PlayEventsOut(accepted=r.accepted, duplicates=r.duplicates,
                         rejected=[PlayEventRejected(**x) for x in r.rejected])


@router.get("/api/plays/summary")
async def listening_summary(start: date, end: date, timezone: str = "UTC", user: User = Depends(current_user),
                            s: AsyncSession = Depends(get_session, scope="function")):
    from app.listening_summary import summary

    if end < start or (end - start).days > 3660 or end == date.max:
        raise HTTPException(422, "Invalid listening period")
    try:
        return await summary(s, user.id, start, end, timezone)
    except (ZoneInfoNotFoundError, ValueError):
        raise HTTPException(422, "Unknown timezone") from None


async def _outs(s: AsyncSession, rows: list[Dislike]) -> list[DislikeOut]:
    from app.models import Track, TrackFile

    songs = {d.nd_song_id for d in rows if d.nd_song_id}
    refs = {d.ref for d in rows if d.ref}
    by_song = dict((await s.execute(select(TrackFile.nd_song_id, TrackFile.track_id)
                                    .where(TrackFile.nd_song_id.in_(songs)))).all()) if songs else {}
    by_ref = dict((await s.execute(select(Track.ref, Track.id).where(Track.ref.in_(refs)))).all()) if refs else {}
    return [DislikeOut(id=d.id, track_id=by_song.get(d.nd_song_id) or by_ref.get(d.ref), ref=d.ref,
                       nd_song_id=d.nd_song_id, artist_ref=d.artist_ref, artist=d.artist, title=d.title,
                       source=d.source, created_at=d.created_at) for d in rows]


async def _named(s: AsyncSession, track_id: int | None, ref: str | None, nd_song_id: str | None):
    """What a track named by the Hub's id is called outside the Hub."""
    from app.library import tracks as registry

    if not track_id:
        return ref, nd_song_id
    names = await registry.names_of(s, track_id)
    if names is None:
        raise HTTPException(404, "Хаб не знает этот трек")
    return ref or names[0], nd_song_id or names[1]


@router.get("/api/plays/history")
async def listened(offset: int = Query(0, ge=0), limit: int = Query(100, ge=1, le=300),
                   show: Literal["all", "finished", "skipped"] = "all", q: str = Query("", max_length=100),
                   user: User = Depends(current_user), s: AsyncSession = Depends(get_session, scope="function")) -> dict[str, Any]:
    """«История»: the user's listens, the newest first (`app/history.py`) — `items` of this page, the totals of the
    days they are of, and where the next page starts."""
    from app import history

    return await history.page(s, user, offset=offset, limit=limit, show=show, q=q)


@router.delete("/api/plays/history")
async def forget_listen(uid: str = Query(min_length=3, max_length=200), user: User = Depends(current_user),
                        s: AsyncSession = Depends(get_session, scope="function")) -> dict[str, bool]:
    """Takes a listen out of the history, the statistics and what the taste is made of."""
    from app import history

    if not await history.remove(s, user.id, uid):
        raise HTTPException(404, "Такой записи нет")
    await s.commit()
    return {"ok": True}


@router.get("/api/dislikes", response_model=list[DislikeOut])
async def list_dislikes(user: User = Depends(current_user), s: AsyncSession = Depends(get_session, scope="function")):
    rows = (await s.execute(select(Dislike).where(Dislike.user_id == user.id)
                            .order_by(Dislike.created_at.desc(), Dislike.id.desc()))).scalars()
    return await _outs(s, list(rows))


@router.post("/api/dislikes", response_model=DislikeOut)
async def add(body: DislikeIn, user: User = Depends(current_user),
              s: AsyncSession = Depends(get_session, scope="function")):
    ref, nd_song_id = await _named(s, body.track_id, body.ref, body.nd_song_id)
    d = await plays.add_dislike(s, user.id, ref=ref, nd_song_id=nd_song_id, artist_ref=body.artist_ref,
                                artist=body.artist, title=body.title, source=plays.dislike_source(body.client))
    if d is None:
        raise HTTPException(400, "Укажите трек или исполнителя")
    await plays.dislike_event(s, user.id, "dislike", ref=d.artist_ref or d.ref, nd_song_id=d.nd_song_id,
                              client=body.client, context=body.context,
                              target_type="artist" if d.artist_ref else "track")
    return (await _outs(s, [d]))[0]


@router.delete("/api/dislikes")
async def remove(track_id: int | None = None, ref: str | None = None, nd_song_id: str | None = None,
                 artist_ref: str | None = None, client: str = "pwa", user: User = Depends(current_user),
                 s: AsyncSession = Depends(get_session, scope="function")):
    """«Не нравится» off: the track (the Hub's `track_id`, or its `ref` and/or `nd_song_id`) or the artist
    (`artist_ref`)."""
    if not (track_id or ref or nd_song_id or artist_ref):
        raise HTTPException(400, "Укажите трек или исполнителя")
    ref, nd_song_id = await _named(s, track_id, ref, nd_song_id)
    n = await plays.remove_dislikes(s, user.id, ref=ref, nd_song_id=nd_song_id, artist_ref=artist_ref)
    if n:
        await plays.dislike_event(s, user.id, "undislike", ref=artist_ref or ref, nd_song_id=nd_song_id,
                                  client=client[:32], target_type="artist" if artist_ref else "track")
    return {"removed": n}


@router.delete("/api/dislikes/{dislike_id}")
async def remove_one(dislike_id: int, user: User = Depends(current_user),
                     s: AsyncSession = Depends(get_session, scope="function")):
    d = await s.get(Dislike, dislike_id)
    if d is None or d.user_id != user.id:
        raise HTTPException(404, "Не найдено")
    await plays.dislike_event(s, user.id, "undislike", ref=d.artist_ref or d.ref, nd_song_id=d.nd_song_id,
                              client="pwa", target_type="artist" if d.artist_ref else "track")
    await s.delete(d)
    return {"removed": 1}
