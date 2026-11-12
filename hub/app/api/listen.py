"""Listening to catalog tracks before downloading them (see app/listen.py)."""

from __future__ import annotations

from typing import Literal
from urllib.parse import quote

from fastapi import APIRouter, Depends, HTTPException, Request
from fastapi.responses import FileResponse, Response, StreamingResponse
from sqlalchemy import select
from starlette.background import BackgroundTask

from app import listen
from app.api.schemas import ListenOut, ListenPrefetchIn, ListenPrefetchOut
from app.auth import current_user
from app.db import session_scope
from app.library.tracks import by_ref
from app.models import TrackFile, User

router = APIRouter(prefix="/api/listen", tags=["listen"])


async def _choose(ref: str, *, background: bool = False) -> listen.Choice:
    try:
        return await listen.choose(ref, background=background)
    except listen.ListenError as e:
        raise HTTPException(e.status, str(e)) from e


@router.get("", response_model=ListenOut)
async def resolve(ref: str, ahead: Literal["whole", "head"] | None = None, user: User = Depends(current_user)):
    """Picks the file (may take a while: providers are searched), then the client plays `stream_url`. `ahead`: the
    client's queue plays the track soon — the Hub opens the file and copies it to its disk now (`listen.load_ahead`):
    "whole" for the track that plays next, "head" for the ones after it (their start only)."""
    c = await (_choose(ref, background=True) if ahead == "head" else _choose(ref))
    if ahead:
        listen.load_ahead(c, whole=ahead == "whole")
    cand = c.candidate
    async with session_scope() as s:
        track_id = None
        if c.nd_song_id:
            track_id = (await s.execute(select(TrackFile.track_id).where(TrackFile.nd_song_id == c.nd_song_id)
                                        .limit(1))).scalar_one_or_none()
        if track_id is None and (track := await by_ref(s, c.ref)) is not None:
            track_id = track.id
    return ListenOut(
        ref=c.ref,
        kind=c.kind,
        artist=c.artist,
        title=c.title,
        duration_s=c.duration_s,
        provider=cand.provider if cand else None,
        bitrate_kbps=cand.bitrate_kbps if cand else None,
        page_url=cand.page_url if cand else None,
        nd_song_id=c.nd_song_id,
        track_id=track_id,
        stream_url=f"/api/listen/stream?ref={quote(c.ref, safe='')}",
    )


@router.post("/prefetch", response_model=ListenPrefetchOut)
async def prefetch(body: ListenPrefetchIn, user: User = Depends(current_user)):
    """The tracks on the user's screen, top first: the Hub picks their files in the background (replaces the
    previous list)."""
    return ListenPrefetchOut(queued=listen.prefetch(user.id, body.refs))


@router.get("/stream")
async def stream(ref: str, request: Request, q: str | None = None, full: bool = False, user: User = Depends(current_user)):
    """Audio of the chosen file (Range → 206): the library file, the server copy of the picked file (read while
    it downloads), the copy of a file of a source without plain HTTP audio, or a preview proxied from its source.
    `q=mobile`: the light variant for a metered network (Opus copy of a heavy file, `app.light`); the answer says
    which variant it is in `X-HomeMusic-Variant`."""
    rng = request.headers.get("range") or ""
    # the app loads the next tracks of its queue ahead (`X-HomeMusic-Prefetch`): that is no listen yet, its
    # `start` event is (app/api/plays.py), and its server copy waits for those of played tracks
    ahead = request.headers.get("x-homemusic-prefetch") == "1"
    try:
        m = await listen.open_media(ref, rng or None, ahead=ahead, quality=q, full_only=full)
    except listen.ListenError as e:
        raise HTTPException(e.status, str(e)) from e
    if m.choice.kind == "full" and not ahead and (not rng or rng.startswith("bytes=0-")):
        from app.imports.core import listened

        await listened(user.id, ref)  # a track of an economy import is downloaded once listened to
    variant = {"X-HomeMusic-Listen-Kind": m.choice.kind}
    if m.variant:
        variant["X-HomeMusic-Variant"] = m.variant
    if m.path is not None:
        return FileResponse(m.path, media_type=m.media_type, headers={"Cache-Control": "no-store", **variant})
    headers = {**m.headers, "accept-ranges": "bytes", "cache-control": "no-store", **variant}
    if m.body is None:
        return Response(status_code=m.status, headers=headers)
    return StreamingResponse(m.body, status_code=m.status, headers=headers, media_type=m.media_type,
                             background=BackgroundTask(m.close) if m.close else None)
