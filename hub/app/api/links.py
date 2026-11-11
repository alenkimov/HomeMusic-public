"""«Поделиться ссылкой»: a track on the music services the Hub knows its id of (app/links.py)."""

from __future__ import annotations

from fastapi import APIRouter, Depends
from pydantic import BaseModel
from sqlalchemy.ext.asyncio import AsyncSession

from app import links
from app.auth import current_user
from app.catalog.models import split_ref
from app.db import get_session
from app.lyrics.service import track_of
from app.models import User

router = APIRouter(prefix="/api/links", tags=["links"])


class LinkOut(BaseModel):
    service: str  # spotify/yandex/ytm/deezer/youtube/soundcloud
    title: str
    url: str


class LinksOut(BaseModel):
    links: list[LinkOut] = []
    universal: str | None = None  # song.link: the same song on every service


@router.get("", response_model=LinksOut)
async def get(nd_song_id: str | None = None, ref: str | None = None, user: User = Depends(current_user),
              s: AsyncSession = Depends(get_session, scope="function")):
    track = await track_of(s, nd_song_id=nd_song_id, ref=ref)
    if track is not None:
        out = await links.of_track(s, track)
        await s.commit()
        return out
    if ref:  # a catalog track that is not in the library: its catalog id is all there is
        src, id_ = split_ref(ref)
        if src == "dz":
            return links.of_ids({"deezer": id_})
    return LinksOut()
